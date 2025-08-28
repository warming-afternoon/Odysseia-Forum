import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING, Optional, Sequence

from shared.safe_defer import safe_defer
from .dto.tag import TagDTO
from .views.global_search_view import GlobalSearchView
from sqlalchemy.ext.asyncio import async_sessionmaker
from .repository import SearchRepository
from ThreadManager.repository import ThreadManagerRepository
from core.tagService import TagService
from core.cache_service import CacheService
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
        session_factory: async_sessionmaker,
        config: dict,
        tag_service: TagService,
        cache_service: CacheService,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.tag_service = tag_service
        self.cache_service = cache_service
        self.tag_system_repo = ThreadManagerRepository
        self.prefs_handler = SearchPreferencesHandler(
            self, bot, session_factory, self.tag_service
        )
        self.global_search_view = GlobalSearchView(self)
        self.persistent_channel_search_view = PersistentChannelSearchView(self)
        self._has_cached_tags = False  # 用于确保 on_ready 只执行一次缓存
        logger.info("Search 模块已加载")

    async def cog_load(self):
        """在Cog加载时注册持久化View"""
        # 注册持久化view，使其在bot重启后仍能响应
        self.bot.add_view(self.global_search_view)
        self.bot.add_view(self.persistent_channel_search_view)

    def get_merged_tags(self, channel_ids: list[int]) -> list[TagDTO]:
        """
        获取多个频道的合并tags，重名tag会被合并显示。
        返回一个 TagDTO 对象列表
        """
        all_tags_names = set()

        for channel_id in channel_ids:
            channel = self.cache_service.indexed_channels.get(channel_id)
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
        await safe_defer(interaction, ephemeral=True)
        try:
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                await repo.save_user_preferences(
                    interaction.user.id, {"results_per_page": num}
                )

            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"已将每页结果数量设置为 {num}", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 设置失败: {e}", ephemeral=True
                ),
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

    @app_commands.command(
        name="创建频道搜索", description="在当前帖子内创建频道搜索按钮"
    )
    @app_commands.guild_only()
    async def create_channel_search(self, interaction: discord.Interaction):
        """在一个帖子内创建一个持久化的搜索按钮，该按钮将启动一个仅限于该频道的搜索流程。"""
        await safe_defer(interaction, ephemeral=True)
        try:
            if (
                not isinstance(interaction.channel, discord.Thread)
                or not interaction.channel.parent
            ):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
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
            channel = interaction.channel
            if isinstance(channel, discord.Thread):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: channel.send(
                        embed=embed, view=self.persistent_channel_search_view
                    ),
                    priority=1,
                )
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "✅ 已成功创建频道内搜索按钮。", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 创建失败: {e}", ephemeral=True
                ),
                priority=1,
            )

    @app_commands.command(
        name="创建公开全局搜索", description="在当前频道创建全局搜索面板"
    )
    async def create_global_search(self, interaction: discord.Interaction):
        """在当前频道创建一个持久化的全局搜索按钮。"""
        await safe_defer(interaction, ephemeral=True)
        try:
            embed = discord.Embed(
                title="🌐 全局搜索",
                description="搜索服务器内所有论坛频道的帖子",
                color=0x2ECC71,
            )
            embed.add_field(
                name="使用方法",
                value="1. 点击下方左侧按钮，选择要搜索的论坛频道\n2. 设置搜索条件（标签、关键词等）\n3. 查看搜索结果",
                inline=False,
            )
            embed.add_field(
                name="偏好配置",
                value="1. 点击下方右侧按钮\n2. 修改搜索时的默认配置（标签、关键词、频道等）",
                inline=False,
            )
            view = GlobalSearchView(self)
            channel = interaction.channel
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: channel.send(embed=embed, view=view),
                    priority=1,
                )
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "✅ 已创建全局搜索面板。", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 创建失败: {e}", ephemeral=True
                ),
                priority=1,
            )

    async def _start_global_search(self, interaction: discord.Interaction):
        """
        启动全局搜索流程的通用逻辑。
        该函数会被 /全局搜索 命令和全局搜索按钮回调调用。
        """
        await safe_defer(interaction, ephemeral=True)
        try:
            # 直接从缓存中获取所有可搜索的频道
            channels = self.cache_service.get_indexed_channels()

            logger.debug(f"从缓存中加载了 {len(channels)} 个频道。")

            if not channels:
                await interaction.followup.send(
                    "❌ 未找到任何可供搜索的已索引论坛频道。\n请确保已使用 /indexer 命令正确索引频道。",
                    ephemeral=True,
                )
                return

            all_channel_ids = list(self.cache_service.indexed_channel_ids)

            # 获取用户偏好 DTO
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                user_prefs = await repo.get_user_preferences(interaction.user.id)

            view = ChannelSelectionView(
                self, interaction, channels, all_channel_ids, user_prefs
            )

            message_content = "请选择想搜索的论坛频道（可多选）："
            if user_prefs and user_prefs.preferred_channels:
                message_content = "已根据偏好预选了频道，可以直接点击“确定搜索”继续或进行修改。"

            await interaction.followup.send(
                message_content, view=view, ephemeral=True
            )
        except Exception:
            logger.error("在启动全局搜索中发生严重错误", exc_info=True)
            # 确保即使有异常，也能给用户一个反馈
            if not interaction.response.is_done():
                await safe_defer(interaction, ephemeral=True)
            await interaction.followup.send(
                "❌ 启动搜索时发生严重错误，请联系管理员。", ephemeral=True
            )

    @app_commands.command(name="全局搜索", description="开始一次仅自己可见的全局搜索")
    async def start_global_search_flow(self, interaction: discord.Interaction):
        """启动全局搜索流程的通用逻辑。"""
        await self._start_global_search(interaction)

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
            # logger.debug(f"搜索开始时QO: {search_qo}")
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)
                user_prefs = await repo.get_user_preferences(interaction.user.id)
                # logger.debug(f"用户偏好: {user_prefs}")

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
                    if search_qo.exclude_keyword_exemption_markers is None:
                        search_qo.exclude_keyword_exemption_markers = (
                            user_prefs.exclude_keyword_exemption_markers
                        )

                # logger.debug(f"合并后QO: {search_qo}")

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
                logger.warning("搜索时，无法获取 guild 对象，无法构建结果 embeds")
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
