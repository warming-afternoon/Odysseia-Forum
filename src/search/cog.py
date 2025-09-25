import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import TYPE_CHECKING, Sequence

from shared.safe_defer import safe_defer
from .dto.tag import TagDTO
from .views.global_search_view import GlobalSearchView
from sqlalchemy.ext.asyncio import async_sessionmaker
from .repository import SearchRepository
from core.tagService import TagService
from core.cache_service import CacheService
from search.qo.thread_search import ThreadSearchQuery
from .views.channel_selection_view import ChannelSelectionView
from .views.generic_search_view import GenericSearchView
from .views.persistent_channel_search_view import PersistentChannelSearchView
from ..preferences.preferences_service import PreferencesService
from .embed_builder import ThreadEmbedBuilder
from .dto.search_state import SearchStateDTO


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
        preferences_service: PreferencesService,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.tag_service = tag_service
        self.cache_service = cache_service
        self.preferences_service = preferences_service
        self.global_search_view = GlobalSearchView(self)
        self.persistent_channel_search_view = PersistentChannelSearchView(self)
        self._has_cached_tags = False  # 用于确保 on_ready 只执行一次缓存
        logger.info("Search 模块已加载")

    async def cog_load(self):
        """在Cog加载时注册持久化View和上下文菜单"""
        # 注册持久化view
        self.bot.add_view(self.global_search_view)
        self.bot.add_view(self.persistent_channel_search_view)

        # 手动创建和注册上下文菜单
        search_user_context_menu = app_commands.ContextMenu(
            name="搜索作品", callback=self.search_user_posts
        )
        self.bot.tree.add_command(search_user_context_menu)

        # search_message_context_menu = app_commands.ContextMenu(
        #     name="搜索作品", callback=self.search_message_author
        # )
        # self.bot.tree.add_command(search_message_context_menu)

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
                value="点击“**搜索本频道**”按钮，可以根据标签、作者、关键词等条件进行搜索。\n"
                "点击“**偏好设置**”按钮，可以修改您的默认搜索偏好。",
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
            error_message = f"❌ 创建失败: {e}"
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    error_message, ephemeral=True
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
            error_message = f"❌ 创建失败: {e}"
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    error_message, ephemeral=True
                ),
                priority=1,
            )

    async def _start_global_search(self, interaction: discord.Interaction):
        """
        启动全局搜索流程的通用逻辑。
        该函数会被 /全局搜索 命令和全局搜索按钮回调调用。
        """
        try:
            await safe_defer(interaction, ephemeral=True)
            # 直接从缓存中获取所有可搜索的频道
            channels = self.cache_service.get_indexed_channels()

            if not channels:
                await interaction.followup.send(
                    "❌ 未找到任何可供搜索的已索引论坛频道。\n请确保管理员已正确索引频道",
                    ephemeral=True,
                )
                return

            all_channel_ids = list(self.cache_service.indexed_channel_ids)

            # 获取用户偏好 DTO
            user_prefs = await self.preferences_service.get_user_preferences(
                interaction.user.id
            )

            # 基于用户偏好创建初始的 SearchStateDTO
            if user_prefs:
                initial_state = SearchStateDTO(
                    channel_ids=user_prefs.preferred_channels or [],
                    include_authors=set(user_prefs.include_authors or []),
                    exclude_authors=set(user_prefs.exclude_authors or []),
                    include_tags=set(user_prefs.include_tags or []),
                    exclude_tags=set(user_prefs.exclude_tags or []),
                    all_available_tags=[],  # 初始化为空列表，延迟加载
                    keywords=user_prefs.include_keywords or "",
                    exclude_keywords=user_prefs.exclude_keywords or "",
                    exemption_markers=user_prefs.exclude_keyword_exemption_markers,
                    page=1,
                    results_per_page=user_prefs.results_per_page,
                    preview_image_mode=user_prefs.preview_image_mode,
                    sort_method=user_prefs.sort_method,
                )
            else:
                initial_state = SearchStateDTO(all_available_tags=[], page=1)  # 初始化为空列表

            view = ChannelSelectionView(
                self, interaction, channels, all_channel_ids, initial_state
            )

            message_content = "请选择想搜索的论坛频道（可多选）："
            if user_prefs and user_prefs.preferred_channels:
                message_content = (
                    "已根据偏好预选了频道，可以直接点击“确定搜索”继续或进行修改。"
                )

            await interaction.followup.send(message_content, view=view, ephemeral=True)
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
    async def quick_author_search_command(
        self, interaction: discord.Interaction, author: discord.User
    ):
        """启动一个交互式视图，用于搜索特定作者的帖子并按标签等进行筛选。"""
        await self._quick_author_search(interaction, author)

    async def _quick_author_search(
        self, interaction: discord.Interaction, author: discord.User | discord.Member
    ):
        """快速作者搜索的内部逻辑"""
        try:
            await safe_defer(interaction, ephemeral=True)
            # 获取所有已索引的频道ID
            all_channel_ids = self.cache_service.get_indexed_channel_ids_list()
            if not all_channel_ids:
                await interaction.followup.send(
                    "❌ 未找到任何可供搜索的已索引论坛频道。", ephemeral=True
                )
                return

            # 获取该作者使用过的所有标签，并确保唯一性
            author_tags = await self.get_tags_for_author(author.id)
            author_tag_names = sorted({tag.name for tag in author_tags})

            # 获取用户偏好 DTO，用于填充非作者和非标签的字段
            user_prefs = await self.preferences_service.get_user_preferences(
                interaction.user.id
            )

            # 创建一个为本次作者搜索定制的 SearchStateDTO
            search_state = SearchStateDTO(
                channel_ids=all_channel_ids,
                # 设置要搜索的作者
                include_authors={author.id},
                exclude_authors=set(),
                # 提供作者专属的标签列表
                all_available_tags=author_tag_names,
                # 忽略用户的标签偏好
                include_tags=set(),
                exclude_tags=set(),
                # 从用户偏好继承其他设置
                keywords=user_prefs.include_keywords if user_prefs else "",
                exclude_keywords=user_prefs.exclude_keywords if user_prefs else "",
                exemption_markers=user_prefs.exclude_keyword_exemption_markers
                if user_prefs
                else ["禁", "🈲"],
                page=1,
                results_per_page=user_prefs.results_per_page if user_prefs else 5,
                preview_image_mode=user_prefs.preview_image_mode
                if user_prefs
                else "thumbnail",
            )

            # 创建通用搜索视图
            view = GenericSearchView(self, interaction, search_state)

            # 启动视图
            await view.start()

        except Exception as e:
            logger.error(f"启动搜索作者时出错: {e}", exc_info=True)
            # 确保即使有异常，也能给用户一个反馈
            if not interaction.response.is_done():
                await safe_defer(interaction, ephemeral=True)
            await interaction.followup.send(f"❌ 启动搜索作者失败: {e}", ephemeral=True)

    # 上下文菜单命令的回调函数
    async def search_user_posts(
        self, interaction: discord.Interaction, user: discord.User
    ):
        """右键点击用户，搜索该用户的作品"""
        await self._quick_author_search(interaction, author=user)

    async def search_message_author(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """右键点击消息，搜索该消息作者的作品"""
        await self._quick_author_search(interaction, author=message.author)

    async def get_tags_for_author(self, author_id: int):
        """获取给定作者使用过的全部标签"""
        async with self.session_factory() as session:
            repo = SearchRepository(session, self.tag_service)
            return await repo.get_tags_for_author(author_id)

    async def get_indexed_channel_ids(self) -> Sequence[int]:
        """获取索引过的频道id列表"""
        return self.cache_service.get_indexed_channel_ids_list()

    async def _search_and_display(
        self,
        interaction: discord.Interaction,
        search_qo: "ThreadSearchQuery",
        page: int,
        per_page: int,
        preview_mode: str,
    ) -> dict:
        """通用搜索和显示函数"""
        try:
            async with self.session_factory() as session:
                repo = SearchRepository(session, self.tag_service)

                offset = (page - 1) * per_page
                limit = per_page

                threads, total_threads = await repo.search_threads_with_count(
                    search_qo, offset=offset, limit=limit
                )

            if not threads:
                return {"has_results": False, "total": total_threads}

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
                "max_page": (total_threads + per_page - 1) // per_page or 1,
            }
        except Exception:
            logger.error("在 _search_and_display 中发生错误", exc_info=True)
            return {"has_results": False, "total": 0, "error": True}
