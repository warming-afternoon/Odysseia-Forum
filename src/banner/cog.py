"""Banner申请和管理Cog"""

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import async_sessionmaker

from banner.banner_service import BannerService
from banner.views.banner_application_button_view import BannerApplicationButtonView
from banner.views.review_view import ReviewView
from shared.permissions import is_admin_or_bot_admin
from shared.safe_defer import safe_defer

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class BannerManagement(commands.Cog):
    """Banner申请和管理系统"""

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker):
        self.bot = bot
        self.session_factory = session_factory
        self.config = bot.config.get("banner", {})
        logger.info("Banner管理模块已加载")

        # 检查配置
        if not self.config.get("enabled", True):
            logger.warning("Banner系统已在配置中禁用")
            return

        # 启动清理任务
        self.cleanup_expired_banners.start()

    async def cog_load(self):
        """Cog加载时注册持久化视图"""
        # 解析申请人角色 ID
        applicant_role_ids_str = self.config.get("applicant_role_ids", "")
        allowed_role_ids = [
            int(rid.strip()) for rid in applicant_role_ids_str.split(",") if rid.strip()
        ]

        # 注册申请按钮持久化视图
        application_view = BannerApplicationButtonView(allowed_role_ids)
        self.bot.add_view(application_view)

        # 注册审核按钮持久化视图
        review_view = ReviewView()
        self.bot.add_view(review_view)

        logger.info("Banner持久化视图已注册")

    def cog_unload(self):
        """Cog卸载时停止任务"""
        self.cleanup_expired_banners.cancel()

    @tasks.loop(hours=1)
    async def cleanup_expired_banners(self):
        """每小时清理过期的banner"""
        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                cleaned = await service.cleanup_expired_banners()
                if cleaned > 0:
                    logger.info(f"清理了 {cleaned} 个过期的banner")
        except Exception as e:
            logger.error(f"清理过期banner时出错: {e}", exc_info=True)

    @cleanup_expired_banners.before_loop
    async def before_cleanup(self):
        """等待bot准备就绪"""
        await self.bot.wait_until_ready()

    banner_group = app_commands.Group(name="banner", description="Banner申请系统管理")

    @banner_group.command(
        name="创建申请通道",
        description="在当前频道创建Banner申请按钮（使用config.json配置）",
    )
    @is_admin_or_bot_admin()
    async def create_application_channel(self, interaction: discord.Interaction):
        """创建Banner申请按钮（从config.json读取配置）"""
        await safe_defer(interaction, ephemeral=True)

        try:
            # 检查配置
            applicant_role_ids_str = self.config.get("applicant_role_ids", "")
            review_thread_id = self.config.get("review_thread_id")
            archive_thread_id = self.config.get("archive_thread_id")
            channels_config = self.config.get("available_channels", {})

            # 验证必需配置
            if not all([applicant_role_ids_str, review_thread_id, archive_thread_id]):
                await interaction.followup.send(
                    "❌ Banner配置不完整。请在config.json中配置：\n"
                    "- banner.applicant_role_ids (允许申请的身份组)\n"
                    "- banner.review_thread_id (审核Thread)\n"
                    "- banner.archive_thread_id (存档Forum频道)",
                    ephemeral=True,
                )
                return

            if not channels_config:
                await interaction.followup.send(
                    "❌ 未配置可用频道列表。请在config.json中配置banner.available_channels",
                    ephemeral=True,
                )
                return

            # 解析申请人身份组ID列表
            applicant_role_ids = [
                int(rid.strip())
                for rid in applicant_role_ids_str.split(",")
                if rid.strip()
            ]

            # 获取身份组（用于显示）
            role_mentions = []
            if interaction.guild and applicant_role_ids:
                for role_id in applicant_role_ids:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        role_mentions.append(role.mention)
                    else:
                        role_mentions.append(f"<@&{role_id}>")
                role_mention = ", ".join(role_mentions)
            else:
                role_mention = "指定身份组"

            # 创建申请按钮视图
            view = BannerApplicationButtonView(applicant_role_ids)

            # 发送带按钮的消息
            embed = discord.Embed(
                title="🎨 Banner申请",
                description=(
                    "点击下方按钮申请将您的帖子展示在论坛Banner轮播中！\n\n"
                    f"**申请资格**: {role_mention}\n"
                    "**展示时长**: 3天\n"
                    "**全频道限制**: 最多3个\n"
                    "**单频道限制**: 最多5个\n\n"
                    "超出限制的申请将进入等待列表，待有空位时自动展示。"
                ),
                color=discord.Color.blue(),
            )

            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                message = await channel.send(embed=embed, view=view)
                await interaction.followup.send(
                    f"✅ Banner申请按钮已创建！\n消息ID: {message.id}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ 只能在文字频道中创建申请按钮", ephemeral=True
                )

        except Exception as e:
            logger.error(f"创建申请通道时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 创建申请通道失败: {str(e)}", ephemeral=True
            )

    @create_application_channel.error
    async def on_create_application_channel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ 您没有权限使用此命令。需要服务器管理员或bot管理员权限。",
                ephemeral=True,
            )
        else:
            logger.error("创建申请通道命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )

    @banner_group.command(name="查看状态", description="查看Banner系统状态")
    @is_admin_or_bot_admin()
    async def view_status(self, interaction: discord.Interaction):
        """查看Banner系统当前状态"""
        await safe_defer(interaction, ephemeral=True)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)

                # 获取全频道banner
                global_banners = await service.get_active_banners(channel_id=None)

                # 构建状态消息
                status_msg = "📊 **Banner系统状态**\n\n"
                status_msg += (
                    f"**全频道Banner**: {len(global_banners)}"
                    f"/{service.GLOBAL_MAX_BANNERS}\n"
                )

                if global_banners:
                    for banner in global_banners:
                        remaining = (banner.end_time - discord.utils.utcnow()).days
                        status_msg += (
                            f"  • 帖子 {banner.thread_id}: "
                            f"{banner.title[:30]}... (剩余{remaining}天)\n"
                        )

                # 获取配置的频道（dict格式）
                channels_dict = self.config.get("available_channels", {})

                status_msg += "\n**频道Banner统计**:\n"
                for idx, (ch_id, ch_name) in enumerate(channels_dict.items()):
                    if idx >= 5:  # 只显示前5个
                        break

                    ch_banners = await service.get_active_banners(channel_id=int(ch_id))
                    ch_specific = [b for b in ch_banners if b.channel_id is not None]
                    status_msg += (
                        f"  • {ch_name}: "
                        f"{len(ch_specific)}/{service.CHANNEL_MAX_BANNERS}\n"
                    )

                await interaction.followup.send(status_msg, ephemeral=True)

        except Exception as e:
            logger.error(f"查看状态时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 查看状态失败: {str(e)}", ephemeral=True
            )

    @view_status.error
    async def on_view_status_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ 您没有权限使用此命令。", ephemeral=True
            )
        else:
            logger.error("查看状态命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )

    @banner_group.command(
        name="删除轮播",
        description="从轮播或等待列表中删除指定Banner（管理员）",
    )
    @app_commands.describe(
        thread_id="要删除的Banner对应的帖子ID（使用 /banner 查看状态 获取ID）"
    )
    @is_admin_or_bot_admin()
    async def delete_carousel_banner(
        self, interaction: discord.Interaction, thread_id: str
    ):
        """从轮播或等待列表中删除指定的Banner"""
        await safe_defer(interaction, ephemeral=True)

        # 验证帖子ID格式
        if not thread_id.isdigit():
            await interaction.followup.send(
                "❌ 帖子ID必须是纯数字。请使用 `/banner 查看状态` 获取正确的帖子ID。",
                ephemeral=True,
            )
            return

        tid = int(thread_id)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                result = await service.delete_banner_by_thread(tid)

                if not result.success:
                    await interaction.followup.send(
                        f"❌ {result.message}", ephemeral=True
                    )
                    return

                # 构建成功 Embed
                location_label = (
                    "轮播列表" if result.deleted_from == "carousel" else "等待列表"
                )
                embed = discord.Embed(
                    title="✅ Banner已删除",
                    description=(
                        f"已从 **{location_label}** 中移除Banner\n"
                        f"**帖子ID**: `{result.thread_id}`\n"
                        f"**标题**: {result.banner_title}\n"
                        f"**范围**: {result.scope_label}"
                    ),
                    color=discord.Color.green(),
                )
                if result.promoted_from_waitlist:
                    embed.add_field(
                        name="🔄 自动补充",
                        value="已将等待列表中的下一个Banner提升至轮播列表",
                        inline=False,
                    )

                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"删除Banner时出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 删除Banner时发生错误: {str(e)}", ephemeral=True
            )

    @delete_carousel_banner.error
    async def on_delete_carousel_banner_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ 您没有权限使用此命令。需要服务器管理员或bot管理员权限。",
                ephemeral=True,
            )
        else:
            logger.error("删除轮播命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )


async def setup(bot: "MyBot"):
    """设置Cog"""
    from banner.listeners.banner_event_listener import BannerEventListener
    from shared.database import AsyncSessionFactory

    banner_config = bot.config.get("banner", {})

    await bot.add_cog(BannerManagement(bot, AsyncSessionFactory))
    await bot.add_cog(BannerEventListener(bot, AsyncSessionFactory, banner_config))
