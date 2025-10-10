import logging
import discord
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING, Optional
from sqlalchemy.ext.asyncio import async_sessionmaker

from shared.ranking_config import RankingConfig, PresetConfigs
from shared.safe_defer import safe_defer
from .embed_builder import ConfigEmbedBuilder
from .mutex_tags_handler import MutexTagsHandler
from src.core.tagService import TagService
from src.webpage.index_sync import manual_sync


if TYPE_CHECKING:
    from bot_main import MyBot


# 获取一个模块级别的 logger
logger = logging.getLogger(__name__)


class Configuration(commands.Cog):
    """管理机器人各项配置"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        api_scheduler,
        tag_service: TagService,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.api_scheduler = api_scheduler
        self.tag_service = tag_service
        self.mutex_handler = MutexTagsHandler(
            bot, self.session_factory, self.api_scheduler, self.tag_service
        )
        logger.info("Config 模块已加载")

    @app_commands.command(
        name="查看目前排序算法", description="查看当前搜索排序算法配置"
    )
    async def view_ranking_config(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        embed = ConfigEmbedBuilder.build_view_config_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)

    config_group = app_commands.Group(name="配置", description="管理机器人各项配置")

    @config_group.command(name="排序算法", description="管理员设置搜索排序算法参数")
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
    @app_commands.checks.has_permissions(administrator=True)
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
        await safe_defer(interaction, ephemeral=True)

        try:
            # 应用预设配置
            if preset:
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
                    if abs(current_total - 1.0) > 0.001 and current_total > 0:
                        RankingConfig.TIME_WEIGHT_FACTOR /= current_total
                        RankingConfig.TAG_WEIGHT_FACTOR /= current_total
                        RankingConfig.REACTION_WEIGHT_FACTOR /= current_total

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
            embed = ConfigEmbedBuilder.build_config_updated_embed(config_name)
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    embed=embed, ephemeral=True
                ),
                priority=1,
            )

        except ValueError as e:
            error_message = f"❌ 配置错误：{e}"
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    error_message, ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            error_message = f"❌ 配置失败：{e}"
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    error_message, ephemeral=True
                ),
                priority=1,
            )

    @configure_ranking.error
    async def on_ranking_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ 此命令需要 admin 权限。", ephemeral=True
            )
        else:
            logger.error("配置排序命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )

    @config_group.command(name="互斥标签组", description="配置互斥标签组")
    @app_commands.checks.has_permissions(administrator=True)
    async def configure_mutex_tags(self, interaction: discord.Interaction):
        """唤出私密的互斥标签组配置面板。"""
        await self.mutex_handler.start_configuration_flow(interaction)

    @configure_mutex_tags.error
    async def on_mutex_tags_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ 此命令需要 admin 权限。", ephemeral=True
            )
        else:
            logger.error("配置互斥标签命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )

    @config_group.command(name="重载配置", description="重新加载配置文件并重新导出部署网页")
    @app_commands.checks.has_permissions(administrator=True)
    async def reload_config(self, interaction: discord.Interaction):
        """重新加载配置文件并重新导出部署网页"""
        await safe_defer(interaction, ephemeral=True)
        
        try:
            # 发送开始消息
            await interaction.followup.send("🔄 开始重载配置文件...", ephemeral=True)
            
            # 重载配置文件
            success, message = self.bot.reload_config()
            
            if not success:
                await interaction.followup.send(f"❌ {message}", ephemeral=True)
                return
            
            # 发送配置重载成功消息
            await interaction.followup.send("✅ 配置文件重载成功！开始重新部署网页...", ephemeral=True)
            
            # 执行手动同步和部署
            await manual_sync(self.bot, self.bot.config)
            
            # 发送完成消息
            await interaction.followup.send("✅ 配置重载并重新部署完成！", ephemeral=True)
            
        except Exception as e:
            logger.error("重载配置时出错", exc_info=e)
            await interaction.followup.send(
                f"❌ 重载配置失败: {e}", ephemeral=True
            )

    @reload_config.error
    async def on_reload_config_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ 此命令需要 admin 权限。", ephemeral=True
            )
        else:
            logger.error("重载配置命令出错", exc_info=error)
            await interaction.response.send_message(
                f"❌ 命令执行失败: {error}", ephemeral=True
            )
