import logging
import discord
from typing import TYPE_CHECKING, Optional

from shared.safe_defer import safe_defer
from ..repository import SearchRepository
from ..dto.user_search_preferences import UserSearchPreferencesDTO
from .components.time_range_modal import TimeRangeModal

if TYPE_CHECKING:
    from ..prefs_handler import SearchPreferencesHandler

# 获取一个模块级别的 logger
logger = logging.getLogger(__name__)

class PreferencesView(discord.ui.View):
    """统一的搜索偏好设置面板"""

    def __init__(
        self,
        handler: "SearchPreferencesHandler",
        interaction: discord.Interaction,
    ):
        super().__init__(timeout=900)  # 15分钟超时
        self.handler = handler
        self.cog = handler.bot.get_cog("Search")
        self.original_interaction = interaction
        self.preferences: Optional[UserSearchPreferencesDTO] = None

    async def fetch_preferences(self) -> "UserSearchPreferencesDTO":
        """获取最新的用户偏好设置"""
        async with self.handler.session_factory() as session:
            repo = SearchRepository(session, self.handler.tag_service)
            prefs_dto = await repo.get_user_preferences(
                self.original_interaction.user.id
            )
            if not prefs_dto:
                # 创建一个临时的空DTO对象
                prefs_dto = UserSearchPreferencesDTO(
                    user_id=self.original_interaction.user.id
                )
            self.preferences = prefs_dto
            return prefs_dto

    def build_embed(self) -> discord.Embed:
        """构建显示当前偏好设置的Embed"""
        embed = discord.Embed(title="🔍 搜索偏好设置", color=0x3498DB)
        prefs = self.preferences

        if not prefs:
            embed.description = "正在加载偏好设置..."
            return embed

        # 检查是否有任何实际的偏好被设置
        is_default = not any(
            [
                prefs.include_authors,
                prefs.exclude_authors,
                prefs.include_tags,
                prefs.exclude_tags,
                prefs.include_keywords,
                prefs.exclude_keywords,
                prefs.after_date,
                prefs.before_date,
            ]
        )

        if is_default:
            embed.description = "您当前未设置任何偏好。"
        else:
            embed.description = "在这里统一管理您的搜索偏好。"

        # 标签偏好
        tag_info = []
        if prefs.include_tags:
            tag_info.append(f"**✅ 正选标签：** {', '.join(prefs.include_tags)}")
        if prefs.exclude_tags:
            tag_info.append(f"**❌ 反选标签：** {', '.join(prefs.exclude_tags)}")
        embed.add_field(
            name="🏷️ 标签设置",
            value="\n".join(tag_info) if tag_info else "无限制",
            inline=False,
        )

        # 关键词偏好
        keyword_info = []
        if prefs.include_keywords:
            keyword_info.append(f"**✅ 包含关键词：** {prefs.include_keywords}")
        if prefs.exclude_keywords:
            keyword_info.append(f"**❌ 排除关键词：** {prefs.exclude_keywords}")
        if (
            prefs.exclude_keyword_exemption_markers
            and prefs.exclude_keyword_exemption_markers != ["禁", "🈲"]
        ):
            markers_str = ", ".join(prefs.exclude_keyword_exemption_markers)
            keyword_info.append(f"**豁免标记：** {markers_str}")

        embed.add_field(
            name="📝 关键词设置",
            value="\n".join(keyword_info) if keyword_info else "无限制",
            inline=False,
        )

        # 时间偏好
        time_info = []
        if prefs.after_date:
            time_info.append(f"**开始时间：** {prefs.after_date.strftime('%Y-%m-%d')}")
        if prefs.before_date:
            time_info.append(f"**结束时间：** {prefs.before_date.strftime('%Y-%m-%d')}")
        embed.add_field(
            name="⏱️ 时间设置",
            value="\n".join(time_info) if time_info else "无限制",
            inline=False,
        )

        # 预览图设置
        preview_display = (
            "大图（下方大图）"
            if prefs.preview_image_mode == "image"
            else "缩略图（右侧小图）"
        )
        embed.add_field(
            name="🖼️ 预览图设置",
            value=f"**显示方式：** {preview_display}",
            inline=False,
        )

        # 每页数量
        embed.add_field(
            name="📊 显示数量",
            value=f"**每页结果：** {prefs.results_per_page}",
            inline=False,
        )

        embed.set_footer(text="点击下方按钮进行修改")
        return embed

    def update_components(self):
        """根据当前状态更新组件（主要是预览图按钮）"""
        self.clear_items()

        # 第一行
        self.add_item(
            discord.ui.Button(
                label="🏷️ 标签",
                style=discord.ButtonStyle.secondary,
                custom_id="prefs_tags",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="📝 关键词",
                style=discord.ButtonStyle.secondary,
                custom_id="prefs_keywords",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="⏱️ 时间范围",
                style=discord.ButtonStyle.secondary,
                custom_id="prefs_time",
            )
        )

        # 第二行
        if self.preferences:
            preview_button_label = (
                "🔄 切换为缩略图"
                if self.preferences.preview_image_mode == "image"
                else "🔄 切换为大图"
            )
            self.add_item(
                discord.ui.Button(
                    label=preview_button_label,
                    style=discord.ButtonStyle.primary,
                    custom_id="prefs_preview",
                    row=1,
                )
            )

        
        self.add_item(
            discord.ui.Button(
                label="🗑️ 清空所有设置",
                style=discord.ButtonStyle.danger,
                custom_id="prefs_clear",
                row=2,
            )
        )

        # 绑定回调
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.callback = self.button_callback

    async def start(self):
        """发送包含视图的初始消息"""
        await self.fetch_preferences()
        self.update_components()
        embed = self.build_embed()
        await self.handler.bot.api_scheduler.submit(
            coro_factory=lambda: self.original_interaction.followup.send(
                embed=embed, view=self, ephemeral=True
            ),
            priority=1,
        )

    async def refresh(self, interaction: discord.Interaction):
        """
        使用最新的偏好设置刷新视图。
        """
        await self.fetch_preferences()
        self.update_components()
        embed = self.build_embed()
        await self.handler.bot.api_scheduler.submit(
            coro_factory=lambda: interaction.edit_original_response(embed=embed, view=self),
            priority=1,
        )

    async def button_callback(self, interaction: discord.Interaction):
        """统一处理所有按钮点击事件"""
        assert interaction.data is not None
        custom_id = interaction.data.get("custom_id")

        if custom_id == "prefs_tags":
            await self.handler.search_preferences_tags(interaction, self)
            return  # 标签视图是独立的流程

        elif custom_id == "prefs_keywords":
            await self.handler.search_preferences_keywords(interaction, self)
            return  # Modal流程自己处理响应，此处返回

        elif custom_id == "prefs_time":
            current_after = (
                self.preferences.after_date.strftime("%Y-%m-%d")
                if self.preferences and self.preferences.after_date
                else ""
            )
            current_before = (
                self.preferences.before_date.strftime("%Y-%m-%d")
                if self.preferences and self.preferences.before_date
                else ""
            )

            modal = TimeRangeModal(self.handler, self, current_after, current_before)
            await self.handler.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.response.send_modal(modal), priority=1
            )
            return  # Modal 流程自己处理响应，此处返回

        elif custom_id == "prefs_preview":
            await self.handler.bot.api_scheduler.submit(
                coro_factory=lambda: safe_defer(interaction), priority=1
            )
            await self.handler.toggle_preview_mode(interaction.user.id)
            await self.refresh(interaction)
            return

        elif custom_id == "prefs_clear":
            await self.handler.bot.api_scheduler.submit(
                coro_factory=lambda: safe_defer(interaction), priority=1
            )
            await self.handler.clear_user_preferences(interaction.user.id)
            await self.refresh(interaction)
            return
