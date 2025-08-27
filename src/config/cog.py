import logging
import discord
from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING, Optional

from shared.ranking_config import RankingConfig, PresetConfigs
from shared.safe_defer import safe_defer
from .embed_builder import RankingEmbedBuilder


if TYPE_CHECKING:
    from bot_main import MyBot


# 获取一个模块级别的 logger
logger = logging.getLogger(__name__)

class Configuration(commands.Cog):
    """管理搜索排序算法的配置"""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        logger.info("Config 模块已加载")

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
        await safe_defer(interaction, ephemeral=True)
        assert isinstance(interaction.user, discord.Member)
        if not interaction.user.guild_permissions.administrator:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "此命令需要管理员权限。", ephemeral=True
                ),
                priority=1,
            )
            return

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
            embed = RankingEmbedBuilder.build_config_updated_embed(config_name)

            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    embed=embed, ephemeral=True
                ),
                priority=1,
            )

        except ValueError as e:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 配置错误：{e}", ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 配置失败：{e}", ephemeral=True
                ),
                priority=1,
            )

    @app_commands.command(name="查看排序配置", description="查看当前搜索排序算法配置")
    async def view_ranking_config(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        embed = RankingEmbedBuilder.build_view_config_embed()

        await self.bot.api_scheduler.submit(
            coro_factory=lambda: interaction.followup.send(embed=embed, ephemeral=True),
            priority=1,
        )