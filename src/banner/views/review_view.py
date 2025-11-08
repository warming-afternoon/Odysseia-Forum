"""审核视图"""
import logging
import discord
from typing import TYPE_CHECKING
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.banner.banner_service import BannerService

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class RejectReasonModal(discord.ui.Modal, title="拒绝理由"):
    """拒绝理由输入Modal"""

    reason = discord.ui.TextInput(
        label="请输入拒绝理由",
        style=discord.TextStyle.paragraph,
        placeholder="请详细说明拒绝原因...",
        required=True,
        max_length=500,
    )

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        application_id: int,
        applicant_id: int,
        reviewer_id: int,
        config: dict,
        original_interaction: discord.Interaction,
    ):
        super().__init__()
        self.bot = bot
        self.session_factory = session_factory
        self.application_id = application_id
        self.applicant_id = applicant_id
        self.reviewer_id = reviewer_id
        self.config = config
        self.archive_channel_id = config.get("archive_channel_id")
        self.original_interaction = original_interaction

    async def on_submit(self, interaction: discord.Interaction):
        """处理拒绝理由提交"""
        await interaction.response.defer(ephemeral=True)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                application = await service.reject_application(
                    self.application_id, self.reviewer_id, str(self.reason.value)
                )

            # 更新原始审核消息
            original_embed = self.original_interaction.message.embeds[0]
            original_embed.color = discord.Color.red()
            original_embed.add_field(
                name="审核结果",
                value=f"❌ 已拒绝 by <@{self.reviewer_id}>",
                inline=False,
            )
            original_embed.add_field(
                name="拒绝理由", value=str(self.reason.value), inline=False
            )

            await self.original_interaction.message.edit(embed=original_embed, view=None)

            # 私聊通知申请者
            try:
                applicant = await self.bot.fetch_user(self.applicant_id)
                dm_embed = discord.Embed(
                    title="Banner申请被拒绝",
                    description=f"您的Banner申请（ID: {self.application_id}）已被审核员拒绝。",
                    color=discord.Color.red(),
                )
                dm_embed.add_field(
                    name="拒绝理由", value=str(self.reason.value), inline=False
                )
                await applicant.send(embed=dm_embed)
            except Exception as e:
                logger.warning(f"无法向申请者发送私信: {e}")

            # 在存档频道留档
            await self._archive_review(application, "rejected", self.reviewer_id)

            await interaction.followup.send("✅ 已拒绝申请并通知申请者", ephemeral=True)

        except Exception as e:
            logger.error(f"处理拒绝申请时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 处理失败: {str(e)}", ephemeral=True
            )

    async def _archive_review(self, application, status: str, reviewer_id: int):
        """在存档频道留档"""
        try:
            archive_channel = self.bot.get_channel(self.archive_channel_id)
            if isinstance(archive_channel, discord.ForumChannel):
                # 在Forum频道创建帖子留档
                thread = await archive_channel.create_thread(
                    name=f"Banner审核 - {application.id} - {status}",
                    content=f"申请ID: {application.id}\n"
                    f"申请人: <@{application.applicant_id}>\n"
                    f"审核员: <@{reviewer_id}>\n"
                    f"状态: {status}\n"
                    f"时间: {discord.utils.format_dt(discord.utils.utcnow())}\n"
                    f"拒绝理由: {application.reject_reason or 'N/A'}",
                )
        except Exception as e:
            logger.error(f"存档审核记录时出错: {e}", exc_info=True)


class ReviewView(discord.ui.View):
    """审核按钮视图"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        application_id: int,
        applicant_id: int,
        config: dict,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.session_factory = session_factory
        self.application_id = application_id
        self.applicant_id = applicant_id
        self.config = config
        self.archive_channel_id = config.get("archive_channel_id")

    @discord.ui.button(
        label="同意",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="banner_approve_button",
    )
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """处理同意按钮"""
        await interaction.response.defer(ephemeral=True)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                application, entered_carousel = await service.approve_application(
                    self.application_id, interaction.user.id
                )

            # 更新原始审核消息
            original_embed = interaction.message.embeds[0]
            original_embed.color = discord.Color.green()
            status_text = (
                "✅ 已同意 - 已加入轮播"
                if entered_carousel
                else "✅ 已同意 - 已加入等待列表"
            )
            original_embed.add_field(
                name="审核结果",
                value=f"{status_text} by <@{interaction.user.id}>",
                inline=False,
            )

            await interaction.message.edit(embed=original_embed, view=None)

            # 私聊通知申请者
            try:
                applicant = await self.bot.fetch_user(self.applicant_id)
                dm_embed = discord.Embed(
                    title="Banner申请已通过",
                    description=f"您的Banner申请（ID: {self.application_id}）已被批准！",
                    color=discord.Color.green(),
                )
                if entered_carousel:
                    dm_embed.add_field(
                        name="状态",
                        value="您的Banner已加入轮播列表，将展示3天。",
                        inline=False,
                    )
                else:
                    dm_embed.add_field(
                        name="状态",
                        value="由于当前轮播列表已满，您的Banner已加入等待列表。待有空位时将自动展示。",
                        inline=False,
                    )
                await applicant.send(embed=dm_embed)
            except Exception as e:
                logger.warning(f"无法向申请者发送私信: {e}")

            # 在存档频道留档
            await self._archive_review(
                application,
                "approved_carousel" if entered_carousel else "approved_waitlist",
                interaction.user.id,
            )

            result_msg = "✅ 已同意申请并通知申请者"
            if entered_carousel:
                result_msg += "\n🎨 Banner已加入轮播列表"
            else:
                result_msg += "\n⏳ Banner已加入等待列表"
            await interaction.followup.send(result_msg, ephemeral=True)

        except Exception as e:
            logger.error(f"处理批准申请时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 处理失败: {str(e)}", ephemeral=True
            )

    @discord.ui.button(
        label="拒绝",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="banner_reject_button",
    )
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """处理拒绝按钮"""
        # 显示拒绝理由输入modal
        modal = RejectReasonModal(
            bot=self.bot,
            session_factory=self.session_factory,
            application_id=self.application_id,
            applicant_id=self.applicant_id,
            reviewer_id=interaction.user.id,
            config=self.config,
            original_interaction=interaction,
        )
        await interaction.response.send_modal(modal)

    async def _archive_review(self, application, status: str, reviewer_id: int):
        """在存档频道留档"""
        try:
            archive_channel = self.bot.get_channel(self.archive_channel_id)
            if isinstance(archive_channel, discord.ForumChannel):
                # 在Forum频道创建帖子留档
                await archive_channel.create_thread(
                    name=f"Banner审核 - {application.id} - {status}",
                    content=f"申请ID: {application.id}\n"
                    f"申请人: <@{application.applicant_id}>\n"
                    f"审核员: <@{reviewer_id}>\n"
                    f"状态: {status}\n"
                    f"时间: {discord.utils.format_dt(discord.utils.utcnow())}\n"
                    f"帖子链接: https://discord.com/channels/{application.channel_id}/{application.thread_id}",
                )
        except Exception as e:
            logger.error(f"存档审核记录时出错: {e}", exc_info=True)