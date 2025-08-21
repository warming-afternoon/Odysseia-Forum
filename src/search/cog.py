import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING, Optional, Sequence

from ranking_config import RankingConfig
from shared.safe_defer import safe_defer
from .dto.tag import TagDTO
from .views.global_search_view import GlobalSearchView
from sqlalchemy.orm import sessionmaker
from .repository import SearchRepository
from tag_system.repository import TagSystemRepository
from tag_system.tagService import TagService
from search.qo.thread_search import ThreadSearchQuery
from .views.channel_selection_view import ChannelSelectionView
from .views.generic_search_view import GenericSearchView
from .views.persistent_channel_search_view import PersistentChannelSearchView
from .prefs_handler import SearchPreferencesHandler
from .views.preferences_view import PreferencesView
from .embed_builder import ThreadEmbedBuilder


if TYPE_CHECKING:
    from bot_main import MyBot

# 获取一个模块级别的 logger
logger = logging.getLogger(__name__)


class Search(commands.Cog):
    """搜索相关命令"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: sessionmaker,
        config: dict,
        tag_service: TagService,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.tag_service = tag_service
        self.tag_system_repo = TagSystemRepository
        self.prefs_handler = SearchPreferencesHandler(
            bot, session_factory, self.tag_service
        )
        self.channel_cache: dict[int, discord.ForumChannel] = {}  # 缓存频道对象
        self.global_search_view = GlobalSearchView(self)
        self.persistent_channel_search_view = PersistentChannelSearchView(self)
        self._has_cached_tags = False  # 用于确保 on_ready 只执行一次缓存

    @commands.Cog.listener()
    async def on_ready(self):
        """当机器人准备就绪时，执行一次性的缓存任务"""
        if not self._has_cached_tags:
            logger.info("机器人已准备就绪，开始缓存已索引的论坛频道...")
            await self.cache_indexed_channels()
            self._has_cached_tags = True

    @commands.Cog.listener()
    async def on_index_updated(self):
        """监听由 Indexer 发出的索引更新事件，并刷新所有相关缓存。"""
        logger.info("接收到 'index_updated' 事件，开始刷新缓存...")

        # 刷新频道缓存
        await self.cache_indexed_channels()

        # 刷新 TagService 缓存
        if self.tag_service:
            logger.info("正在刷新 TagService 缓存...")
            await self.tag_service.build_cache()
            logger.info("TagService 缓存已刷新。")

        logger.info("所有缓存刷新完毕。")

    async def cog_load(self):
        """在Cog加载时注册持久化View"""
        # 注册持久化view，使其在bot重启后仍能响应
        self.bot.add_view(self.global_search_view)
        self.bot.add_view(self.persistent_channel_search_view)

    async def cache_indexed_channels(self):
        """高效缓存所有已索引的论坛频道对象"""
        logger.info("开始刷新频道缓存...")
        try:
            async with self.session_factory() as session:
                repo = self.tag_system_repo(session)
                indexed_channel_ids = await repo.get_indexed_channel_ids()

            new_cache = {}
            for channel_id in indexed_channel_ids:
                # bot.get_channel() 从内部缓存获取，无API调用
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.ForumChannel):
                    new_cache[channel_id] = channel
                else:
                    logger.warning(
                        f"无法从机器人缓存中找到ID为 {channel_id} 的论坛频道，或该频道类型不正确。"
                    )

            self.channel_cache = new_cache
            logger.info(
                f"频道缓存刷新完毕，共缓存 {len(self.channel_cache)} 个论坛频道。"
            )

        except Exception as e:
            logger.error(f"缓存频道时出错: {e}", exc_info=True)

    def get_merged_tags(self, channel_ids: list[int]) -> list[TagDTO]:
        """
        获取多个频道的合并tags，重名tag会被合并显示。
        返回一个 TagDTO 对象列表
        """
        all_tags_names = set()

        for channel_id in channel_ids:
            channel = self.channel_cache.get(channel_id)
            if channel:
                all_tags_names.update(tag.name for tag in channel.available_tags)

        # 返回 TagDTO 对象列表，确保后续代码可以安全地访问 .id 和 .name
        return [TagDTO(id=0, name=tag_name) for tag_name in sorted(all_tags_names)]

    # ----- 用户偏好设置 -----
    @app_commands.command(
        name="每页结果数量", description="设置每页展示的搜索结果数量（3-9）"
    )
    @app_commands.describe(num="要设置的数量 (3-9)")
    async def set_page_size(
        self, interaction: discord.Interaction, num: app_commands.Range[int, 3, 9]
    ):
        await safe_defer(interaction)
        try:
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                await repo.save_user_preferences(
                    interaction.user.id, {"results_per_page": num}
                )

            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(
                    f"已将每页结果数量设置为 {num}", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 设置失败: {e}", ephemeral=True),
                priority=1,
            )

    # ----- 搜索偏好设置 -----
    search_prefs = app_commands.Group(name="搜索偏好", description="管理搜索偏好设置")

    @search_prefs.command(name="作者", description="管理作者偏好设置")
    @app_commands.describe(action="操作类型", user="要设置的用户（@用户 或 用户ID）")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="只看作者", value="include"),
            app_commands.Choice(name="屏蔽作者", value="exclude"),
            app_commands.Choice(name="取消屏蔽", value="unblock"),
            app_commands.Choice(name="清空作者偏好", value="clear"),
        ]
    )
    async def search_preferences_author(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        user: Optional[discord.User] = None,
    ):
        await self.prefs_handler.search_preferences_author(interaction, action, user)

    @search_prefs.command(name="设置", description="打开交互式偏好设置面板")
    async def open_search_preferences_panel(self, interaction: discord.Interaction):
        """打开一个新的交互式视图来管理搜索偏好"""
        try:
            await safe_defer(interaction, ephemeral=True)

            view = PreferencesView(self.prefs_handler, interaction)
            await view.start()

        except Exception as e:
            logger.error(f"打开偏好设置面板时出错: {e}", exc_info=True)
            if not interaction.response.is_done():
                await safe_defer(interaction, ephemeral=True)
            await interaction.followup.send(
                f"❌ 打开设置面板时发生错误: {e}", ephemeral=True
            )

    # ----- 排序算法管理 -----
    @app_commands.command(name="排序算法配置", description="管理员设置搜索排序算法参数")
    @app_commands.describe(
        preset="预设配置方案",
        time_weight="时间权重因子 (0.0-1.0)",
        tag_weight="标签权重因子 (0.0-1.0)",
        reaction_weight="反应权重因子 (0.0-1.0)",
        time_decay="时间衰减率 (0.01-0.5)",
        reaction_log_base="反应数对数基数 (10-200)",
        severe_penalty="严重惩罚阈值 (0.0-1.0)",
        mild_penalty="轻度惩罚阈值 (0.0-1.0)",
    )
    @app_commands.choices(
        preset=[
            app_commands.Choice(name="平衡配置 (默认)", value="balanced"),
            app_commands.Choice(name="偏重时间新鲜度", value="time_focused"),
            app_commands.Choice(name="偏重内容质量", value="quality_focused"),
            app_commands.Choice(name="偏重受欢迎程度", value="popularity_focused"),
            app_commands.Choice(name="严格质量控制", value="strict_quality"),
        ]
    )
    async def configure_ranking(
        self,
        interaction: discord.Interaction,
        preset: Optional[app_commands.Choice[str]] = None,
        time_weight: Optional[float] = None,
        tag_weight: Optional[float] = None,
        reaction_weight: Optional[float] = None,
        time_decay: Optional[float] = None,
        reaction_log_base: Optional[int] = None,
        severe_penalty: Optional[float] = None,
        mild_penalty: Optional[float] = None,
    ):
        # 检查权限 (需要管理员权限)
        await safe_defer(interaction)
        assert isinstance(interaction.user, discord.Member)
        if not interaction.user.guild_permissions.administrator:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(
                    "此命令需要管理员权限。", ephemeral=True
                ),
                priority=1,
            )
            return

        try:
            # 应用预设配置
            if preset:
                from ranking_config import PresetConfigs

                if preset.value == "balanced":
                    PresetConfigs.balanced()
                elif preset.value == "time_focused":
                    PresetConfigs.time_focused()
                elif preset.value == "quality_focused":
                    PresetConfigs.quality_focused()
                elif preset.value == "popularity_focused":
                    PresetConfigs.popularity_focused()
                elif preset.value == "strict_quality":
                    PresetConfigs.strict_quality()

                config_name = preset.name
            else:
                # 手动配置参数
                if time_weight is not None:
                    if 0 <= time_weight <= 1:
                        RankingConfig.TIME_WEIGHT_FACTOR = time_weight
                    else:
                        raise ValueError("时间权重必须在0-1之间")

                if tag_weight is not None:
                    if 0 <= tag_weight <= 1:
                        RankingConfig.TAG_WEIGHT_FACTOR = tag_weight
                    else:
                        raise ValueError("标签权重必须在0-1之间")

                if reaction_weight is not None:
                    if 0 <= reaction_weight <= 1:
                        RankingConfig.REACTION_WEIGHT_FACTOR = reaction_weight
                    else:
                        raise ValueError("反应权重必须在0-1之间")

                # 确保权重和为1 (三个权重)
                if (
                    time_weight is not None
                    or tag_weight is not None
                    or reaction_weight is not None
                ):
                    # 计算当前权重总和
                    current_total = (
                        RankingConfig.TIME_WEIGHT_FACTOR
                        + RankingConfig.TAG_WEIGHT_FACTOR
                        + RankingConfig.REACTION_WEIGHT_FACTOR
                    )

                    # 如果权重和不为1，按比例重新分配
                    if abs(current_total - 1.0) > 0.001:
                        RankingConfig.TIME_WEIGHT_FACTOR = (
                            RankingConfig.TIME_WEIGHT_FACTOR / current_total
                        )
                        RankingConfig.TAG_WEIGHT_FACTOR = (
                            RankingConfig.TAG_WEIGHT_FACTOR / current_total
                        )
                        RankingConfig.REACTION_WEIGHT_FACTOR = (
                            RankingConfig.REACTION_WEIGHT_FACTOR / current_total
                        )

                if time_decay is not None:
                    if 0.01 <= time_decay <= 0.5:
                        RankingConfig.TIME_DECAY_RATE = time_decay
                    else:
                        raise ValueError("时间衰减率必须在0.01-0.5之间")

                if reaction_log_base is not None:
                    if 10 <= reaction_log_base <= 200:
                        RankingConfig.REACTION_LOG_BASE = reaction_log_base
                    else:
                        raise ValueError("反应数对数基数必须在10-200之间")

                if severe_penalty is not None:
                    if 0 <= severe_penalty <= 1:
                        RankingConfig.SEVERE_PENALTY_THRESHOLD = severe_penalty
                    else:
                        raise ValueError("严重惩罚阈值必须在0-1之间")

                if mild_penalty is not None:
                    if 0 <= mild_penalty <= 1:
                        RankingConfig.MILD_PENALTY_THRESHOLD = mild_penalty
                    else:
                        raise ValueError("轻度惩罚阈值必须在0-1之间")

                config_name = "自定义配置"

            # 验证配置
            RankingConfig.validate()

            # 构建响应消息
            embed = discord.Embed(
                title="✅ 排序算法配置已更新",
                description=f"当前配置：**{config_name}**",
                color=0x00FF00,
            )

            embed.add_field(
                name="权重配置",
                value=f"• 时间权重：**{RankingConfig.TIME_WEIGHT_FACTOR:.1%}**\n"
                f"• 标签权重：**{RankingConfig.TAG_WEIGHT_FACTOR:.1%}**\n"
                f"• 反应权重：**{RankingConfig.REACTION_WEIGHT_FACTOR:.1%}**\n"
                f"• 时间衰减率：**{RankingConfig.TIME_DECAY_RATE}**\n"
                f"• 反应对数基数：**{RankingConfig.REACTION_LOG_BASE}**",
                inline=True,
            )

            embed.add_field(
                name="惩罚机制",
                value=f"• 严重惩罚阈值：**{RankingConfig.SEVERE_PENALTY_THRESHOLD}**\n"
                f"• 轻度惩罚阈值：**{RankingConfig.MILD_PENALTY_THRESHOLD}**\n"
                f"• 严重惩罚系数：**{RankingConfig.SEVERE_PENALTY_FACTOR}**",
                inline=True,
            )

            # 添加算法说明
            embed.add_field(
                name="算法说明",
                value="新的排序算法将立即生效，影响所有后续搜索结果。\n"
                "时间权重基于指数衰减，标签权重基于Wilson Score算法。",
                inline=False,
            )

            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(embed=embed, ephemeral=True), priority=1
            )

        except ValueError as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 配置错误：{e}", ephemeral=True),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 配置失败：{e}", ephemeral=True),
                priority=1,
            )

    @app_commands.command(name="查看排序配置", description="查看当前搜索排序算法配置")
    async def view_ranking_config(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        embed = discord.Embed(
            title="🔧 当前排序算法配置",
            description="智能混合权重排序算法参数",
            color=0x3498DB,
        )

        embed.add_field(
            name="权重配置",
            value=f"• 时间权重：**{RankingConfig.TIME_WEIGHT_FACTOR:.1%}**\n"
            f"• 标签权重：**{RankingConfig.TAG_WEIGHT_FACTOR:.1%}**\n"
            f"• 反应权重：**{RankingConfig.REACTION_WEIGHT_FACTOR:.1%}**\n"
            f"• 时间衰减率：**{RankingConfig.TIME_DECAY_RATE}**\n"
            f"• 反应对数基数：**{RankingConfig.REACTION_LOG_BASE}**",
            inline=True,
        )

        embed.add_field(
            name="惩罚机制",
            value=f"• 严重惩罚阈值：**{RankingConfig.SEVERE_PENALTY_THRESHOLD}**\n"
            f"• 轻度惩罚阈值：**{RankingConfig.MILD_PENALTY_THRESHOLD}**\n"
            f"• 严重惩罚系数：**{RankingConfig.SEVERE_PENALTY_FACTOR:.1%}**\n"
            f"• 轻度惩罚系数：**{RankingConfig.MILD_PENALTY_FACTOR:.1%}**",
            inline=True,
        )

        embed.add_field(
            name="算法特性",
            value="• **Wilson Score**：置信度评估标签质量\n"
            "• **指数衰减**：时间新鲜度自然衰减\n"
            "• **智能惩罚**：差评内容自动降权\n"
            "• **可配置权重**：灵活调整排序偏好",
            inline=False,
        )

        embed.set_footer(text="管理员可使用 /排序算法配置 命令调整参数")

        await self.bot.api_scheduler.submit(
            coro=interaction.followup.send(embed=embed, ephemeral=True), priority=1
        )

    @app_commands.command(
        name="创建频道搜索", description="在当前帖子内创建频道搜索按钮"
    )
    @app_commands.guild_only()
    async def create_channel_search(self, interaction: discord.Interaction):
        """在一个帖子内创建一个持久化的搜索按钮，该按钮将启动一个仅限于该频道的搜索流程。"""
        await safe_defer(interaction)
        try:
            if (
                not isinstance(interaction.channel, discord.Thread)
                or not interaction.channel.parent
            ):
                await self.bot.api_scheduler.submit(
                    coro=interaction.followup.send(
                        "请在帖子内使用此命令。", ephemeral=True
                    ),
                    priority=1,
                )
                return

            channel_id = interaction.channel.parent_id

            # 创建美观的embed
            embed = discord.Embed(
                title=f"🔍 {interaction.channel.parent.name} 频道搜索",
                description=f"点击下方按钮，搜索 <#{channel_id}> 频道内的所有帖子",
                color=0x3498DB,
            )
            embed.add_field(
                name="使用方法",
                value="根据标签、作者、关键词等条件进行搜索。",
                inline=False,
            )

            # 发送带有持久化视图的消息
            await self.bot.api_scheduler.submit(
                coro=interaction.channel.send(
                    embed=embed, view=self.persistent_channel_search_view
                ),
                priority=1,
            )
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(
                    "✅ 已成功创建频道内搜索按钮。", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 创建失败: {e}", ephemeral=True),
                priority=1,
            )

    @app_commands.command(
        name="创建公开全局搜索", description="在当前频道创建全局搜索按钮"
    )
    async def create_global_search(self, interaction: discord.Interaction):
        """在当前频道创建一个持久化的全局搜索按钮。"""
        await safe_defer(interaction)
        try:
            embed = discord.Embed(
                title="🌐 全局搜索",
                description="搜索服务器内所有论坛频道的帖子",
                color=0x2ECC71,
            )
            embed.add_field(
                name="使用方法",
                value="1. 点击下方按钮选择要搜索的论坛频道\n2. 设置搜索条件（标签、关键词等）\n3. 查看搜索结果",
                inline=False,
            )
            view = GlobalSearchView(self)
            if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
                await self.bot.api_scheduler.submit(
                    coro=interaction.channel.send(embed=embed, view=view), priority=1
                )
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(
                    "✅ 已创建全局搜索面板。", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 创建失败: {e}", ephemeral=True),
                priority=1,
            )

    @app_commands.command(name="全局搜索", description="开始一次仅自己可见的全局搜索")
    async def start_global_search_flow(self, interaction: discord.Interaction):
        """启动全局搜索流程的通用逻辑。"""
        await safe_defer(interaction)
        try:
            # 直接从缓存中获取所有可搜索的频道
            channels = list(self.channel_cache.values())

            logger.debug(f"从缓存中加载了 {len(channels)} 个频道。")

            if not channels:
                await interaction.followup.send(
                    "❌ 未找到任何可供搜索的已索引论坛频道。\n请确保已使用 /indexer 命令正确索引频道。",
                    ephemeral=True,
                )
                return

            all_channel_ids = list(self.channel_cache.keys())
            view = ChannelSelectionView(self, interaction, channels, all_channel_ids)
            await interaction.followup.send(
                "请选择要搜索的频道：", view=view, ephemeral=True
            )
        except Exception:
            logger.error("在 start_global_search_flow 中发生严重错误", exc_info=True)
            # 确保即使有异常，也能给用户一个反馈
            if not interaction.response.is_done():
                await safe_defer(interaction)
            await interaction.followup.send(
                "❌ 启动搜索时发生严重错误，请联系管理员。", ephemeral=True
            )

    @app_commands.command(name="搜索作者", description="快速搜索指定作者的所有帖子")
    @app_commands.describe(author="要搜索的作者（@用户 或 用户ID）")
    async def quick_author_search(
        self, interaction: discord.Interaction, author: discord.User
    ):
        """启动一个交互式视图，用于搜索特定作者的帖子并按标签等进行筛选。"""
        await safe_defer(interaction, ephemeral=True)
        try:
            # 获取所有已索引的频道ID
            async with self.session_factory() as session:
                repo = self.tag_system_repo(session)
                all_channel_ids = await repo.get_indexed_channel_ids()

            if not all_channel_ids:
                await interaction.followup.send(
                    "❌ 未找到任何可供搜索的已索引论坛频道。", ephemeral=True
                )
                return

            # 获取用户偏好 DTO
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                user_prefs_dto = await repo.get_user_preferences(interaction.user.id)

            # 创建通用搜索视图，并传入用户偏好
            view = GenericSearchView(
                self, interaction, list(all_channel_ids), user_prefs_dto
            )

            # 预设作者（这将覆盖偏好中的作者设置）
            view.author_ids = {author.id}

            # 启动视图
            await view.start()

        except Exception as e:
            logger.error(f"启动搜索作者时出错: {e}", exc_info=True)
            # 确保即使有异常，也能给用户一个反馈
            if not interaction.response.is_done():
                await safe_defer(interaction, ephemeral=True)
            await interaction.followup.send(f"❌ 启动搜索作者失败: {e}", ephemeral=True)

    async def get_tags_for_author(self, author_id: int):
        """Gets all unique tags for a given author's posts."""
        async with self.session_factory() as session:
            repo = self.tag_system_repo(session)
            return await repo.get_tags_for_author(author_id)

    async def get_indexed_channel_ids(self) -> Sequence[int]:
        """Gets all indexed channel IDs."""
        async with self.session_factory() as session:
            repo = self.tag_system_repo(session)
            return await repo.get_indexed_channel_ids()

    async def _search_and_display(
        self,
        interaction: discord.Interaction,
        search_qo: "ThreadSearchQuery",
        page: int = 1,
    ) -> dict:
        """
        通用搜索和显示函数

        :param interaction: discord.Interaction
        :param search_qo: ThreadSearchQO 查询对象
        :param page: 当前页码
        :return: 包含搜索结果信息的字典
        """
        try:
            logger.debug(f"--- 搜索开始 (Page: {page}) ---")
            logger.debug(f"初始QO: {search_qo}")
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                user_prefs = await repo.get_user_preferences(interaction.user.id)
                logger.debug(f"用户偏好: {user_prefs}")

                per_page = 5
                preview_mode = "thumbnail"
                if user_prefs:
                    per_page = user_prefs.results_per_page
                    preview_mode = user_prefs.preview_image_mode

                    # 合并偏好设置到查询对象
                    # 只有当查询对象中没有相应值时，才使用偏好设置
                    if search_qo.include_authors is None:
                        search_qo.include_authors = user_prefs.include_authors
                    if search_qo.exclude_authors is None:
                        search_qo.exclude_authors = user_prefs.exclude_authors
                    if search_qo.after_ts is None:
                        search_qo.after_ts = user_prefs.after_date
                    if search_qo.before_ts is None:
                        search_qo.before_ts = user_prefs.before_date

                logger.debug(f"合并后QO: {search_qo}")

                # 设置分页
                offset = (page - 1) * per_page
                limit = per_page

                # 执行搜索查询
                threads, total_threads = await repo.search_threads_with_count(
                    search_qo, offset=offset, limit=limit
                )

            if not threads:
                return {"has_results": False, "total": total_threads}

            # 构建 embeds
            embeds = []
            if not interaction.guild:
                logger.warning("搜索时，无法获取 guild 对象，无法构建结果 embeds。")
            else:
                for thread in threads:
                    embed = await ThreadEmbedBuilder.build(
                        thread, interaction.guild, preview_mode
                    )
                    embeds.append(embed)

            return {
                "has_results": True,
                "embeds": embeds,
                "total": total_threads,
                "page": page,
                "per_page": per_page,
                "max_page": (total_threads + per_page - 1) // per_page,
            }
        except Exception as e:
            logger.error(f"搜索时发生错误: {e}", exc_info=True)
            return {"has_results": False, "error": str(e)}
