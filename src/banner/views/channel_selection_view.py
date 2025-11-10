"""频道选择视图"""
import logging
import discord
from typing import TYPE_CHECKING
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.banner.banner_service import BannerService
from .review_view import ReviewView

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class ChannelSelectionView(discord.ui.View):
    """频道选择下拉视图"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        thread_id: int,
        channel_id: int,
        cover_image_url: str,
        applicant_id: int,
        config: dict,
    ):
        super().__init__(timeout=300)  # 5分钟超时
        self.bot = bot
        self.session_factory = session_factory
        self.thread_id = thread_id
        self.channel_id = channel_id
        self.cover_image_url = cover_image_url
        self.applicant_id = applicant_id
        self.config = config
        self.review_thread_id = config.get("review_thread_id")
        self.archive_thread_id = config.get("archive_thread_id")

        # 创建频道选择下拉菜单
        options = [
            discord.SelectOption(
                label="全频道",
                value="global",
                description="在所有频道展示（最多3个）",
                emoji="🌐",
            )
        ]

        # 转换available_channels从dict格式到list格式
        channels_dict = config.get("available_channels", {})
        for ch_id, ch_name in channels_dict.items():
            ch_config = {"id": ch_id, "name": ch_name}
            ch_id = ch_config.get("id")
            ch_name = ch_config.get("name", "未知频道")
            if ch_id:
                options.append(
                    discord.SelectOption(
                        label=ch_name,
                        value=str(ch_id),
                        description=f"仅在{ch_name}展示（最多5个）",
                        emoji="📋",
                    )
                )

        self.channel_select.options = options

    @discord.ui.select(placeholder="选择展示范围...")
    async def channel_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        """处理频道选择"""
        await interaction.response.defer(ephemeral=True)

        try:
            target_scope = select.values[0]

            # 创建申请
            async with self.session_factory() as session:
                service = BannerService(session)
                application = await service.create_application(
                    thread_id=self.thread_id,
                    channel_id=self.channel_id,
                    applicant_id=self.applicant_id,
                    cover_image_url=self.cover_image_url,
                    target_scope=target_scope,
                )

                # 发送审核消息到指定thread
                review_thread = self.bot.get_channel(self.review_thread_id)
                if not isinstance(review_thread, discord.Thread):
                    await interaction.followup.send(
                        "❌ 审核Thread配置错误", ephemeral=True
                    )
                    return

                # 构建审核embed
                # 获取频道名称
                if target_scope == "global":
                    scope_text = "全频道"
                else:
                    channels_dict = self.config.get("available_channels", {})
                    scope_text = channels_dict.get(target_scope, f"频道 {target_scope}")

                embed = discord.Embed(
                    title="🎨 新的Banner申请",
                    color=discord.Color.orange(),
                )
                embed.add_field(
                    name="申请人", value=f"<@{self.applicant_id}>", inline=True
                )
                embed.add_field(name="展示范围", value=scope_text, inline=True)
                embed.add_field(
                    name="帖子",
                    value=f"[点击查看](https://discord.com/channels/{interaction.guild_id}/{self.channel_id}/{self.thread_id})",
                    inline=False,
                )
                embed.set_image(url=self.cover_image_url)
                embed.set_footer(text=f"申请ID: {application.id}")

                # 创建审核视图
                review_view = ReviewView(
                    bot=self.bot,
                    session_factory=self.session_factory,
                    application_id=application.id,
                    applicant_id=self.applicant_id,
                    config=self.config,
                )

                review_message = await review_thread.send(
                    embed=embed, view=review_view
                )

                # 更新申请记录的消息ID
                await service.update_review_message_info(
                    application.id, review_message.id, self.review_thread_id
                )

            # 禁用当前视图
            for item in self.children:
                item.disabled = True
            await interaction.edit_original_response(view=self)

            # 通知用户
            await interaction.followup.send(
                "✅ 申请已提交！审核员将尽快处理您的申请。", ephemeral=True
            )

        except Exception as e:
            logger.error(f"处理频道选择时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 提交申请失败: {str(e)}", ephemeral=True
            )

    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            item.disabled = True