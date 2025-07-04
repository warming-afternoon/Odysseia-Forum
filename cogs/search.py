import discord
from discord import app_commands
from discord.ext import commands
import datetime
import math
import re

import database
from ranking_config import RankingConfig

class Search(commands.Cog):
    """搜索相关命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """在Cog加载时注册持久化View"""
        # 注册持久化view，使其在bot重启后仍能响应
        self.bot.add_view(PersistentChannelSearchView(None))  # None作为占位符
        self.bot.add_view(PersistentGlobalSearchView())

    # ----- 用户偏好设置 -----
    @app_commands.command(name="每页结果数量", description="设置每页展示的搜索结果数量（3-10）")
    @app_commands.describe(num="数字 3-10")
    async def set_page_size(self, interaction: discord.Interaction, num: int):
        if not 3 <= num <= 10:
            await interaction.response.send_message("请输入 3-10 之间的数字。", ephemeral=True)
            return
        await database.set_results_per_page(interaction.user.id, num)
        await interaction.response.send_message(f"已将每页结果数量设置为 {num}。", ephemeral=True)

    # ----- 搜索偏好设置 -----
    @app_commands.command(name="搜索偏好", description="管理搜索偏好设置")
    @app_commands.describe(
        action="操作类型",
        user="要设置的用户（@用户 或 用户ID）",
        after_date="开始日期（格式：YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）",
        before_date="结束日期（格式：YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="只看作者", value="include_author"),
        app_commands.Choice(name="屏蔽作者", value="exclude_author"),
        app_commands.Choice(name="取消屏蔽", value="unblock_author"),
        app_commands.Choice(name="清空作者偏好", value="clear_authors"),
        app_commands.Choice(name="时间范围", value="time_range"),
        app_commands.Choice(name="查看当前设置", value="view_settings")
    ])
    async def search_preferences(
        self, 
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        user: discord.User = None,
        after_date: str = None,
        before_date: str = None
    ):
        user_id = interaction.user.id
        
        try:
            if action.value == "include_author":
                if not user:
                    await interaction.response.send_message("❌ 请指定要设置的用户。", ephemeral=True)
                    return
                
                prefs = await database.get_user_search_preferences(user_id)
                include_authors = set(prefs['include_authors'] or [])
                exclude_authors = set(prefs['exclude_authors'] or [])
                
                # 添加到只看作者列表，从屏蔽列表中移除
                include_authors.add(user.id)
                exclude_authors.discard(user.id)
                
                await database.save_user_search_preferences(
                    user_id, list(include_authors), list(exclude_authors),
                    prefs['after_date'], prefs['before_date']
                )
                
                await interaction.response.send_message(
                    f"✅ 已将 {user.mention} 添加到只看作者列表。", ephemeral=True
                )
            
            elif action.value == "exclude_author":
                if not user:
                    await interaction.response.send_message("❌ 请指定要屏蔽的用户。", ephemeral=True)
                    return
                
                prefs = await database.get_user_search_preferences(user_id)
                include_authors = set(prefs['include_authors'] or [])
                exclude_authors = set(prefs['exclude_authors'] or [])
                
                # 添加到屏蔽列表，从只看作者列表中移除
                exclude_authors.add(user.id)
                include_authors.discard(user.id)
                
                await database.save_user_search_preferences(
                    user_id, list(include_authors), list(exclude_authors),
                    prefs['after_date'], prefs['before_date']
                )
                
                await interaction.response.send_message(
                    f"✅ 已将 {user.mention} 添加到屏蔽作者列表。", ephemeral=True
                )
            
            elif action.value == "unblock_author":
                if not user:
                    await interaction.response.send_message("❌ 请指定要取消屏蔽的用户。", ephemeral=True)
                    return
                
                prefs = await database.get_user_search_preferences(user_id)
                include_authors = set(prefs['include_authors'] or [])
                exclude_authors = set(prefs['exclude_authors'] or [])
                
                # 从屏蔽列表中移除
                if user.id in exclude_authors:
                    exclude_authors.remove(user.id)
                    await database.save_user_search_preferences(
                        user_id, list(include_authors), list(exclude_authors),
                        prefs['after_date'], prefs['before_date']
                    )
                    await interaction.response.send_message(
                        f"✅ 已将 {user.mention} 从屏蔽列表中移除。", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"ℹ️ {user.mention} 不在屏蔽列表中。", ephemeral=True
                    )
            
            elif action.value == "clear_authors":
                prefs = await database.get_user_search_preferences(user_id)
                await database.save_user_search_preferences(
                    user_id, [], [], prefs['after_date'], prefs['before_date']
                )
                await interaction.response.send_message("✅ 已清空所有作者偏好设置。", ephemeral=True)
            
            elif action.value == "time_range":
                # 解析时间
                parsed_after = None
                parsed_before = None
                
                if after_date:
                    try:
                        date_str = after_date.strip()
                        if len(date_str) == 10:  # YYYY-MM-DD
                            date_str += " 00:00:00"
                        parsed_after = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").isoformat()
                    except ValueError:
                        await interaction.response.send_message(
                            "❌ 开始日期格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS 格式。", ephemeral=True
                        )
                        return
                
                if before_date:
                    try:
                        date_str = before_date.strip()
                        if len(date_str) == 10:  # YYYY-MM-DD
                            date_str += " 23:59:59"
                        parsed_before = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").isoformat()
                    except ValueError:
                        await interaction.response.send_message(
                            "❌ 结束日期格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS 格式。", ephemeral=True
                        )
                        return
                
                # 检查日期逻辑
                if parsed_after and parsed_before and parsed_after > parsed_before:
                    await interaction.response.send_message("❌ 开始日期不能晚于结束日期。", ephemeral=True)
                    return
                
                prefs = await database.get_user_search_preferences(user_id)
                await database.save_user_search_preferences(
                    user_id, prefs['include_authors'], prefs['exclude_authors'],
                    parsed_after, parsed_before
                )
                
                # 根据参数情况给出不同的反馈
                if not after_date and not before_date:
                    # 没有填任何参数，清空时间范围设置
                    await interaction.response.send_message("✅ 已清空时间范围设置。", ephemeral=True)
                else:
                    # 设置了时间参数
                    time_info = []
                    if parsed_after:
                        time_info.append(f"开始时间：{after_date}")
                    if parsed_before:
                        time_info.append(f"结束时间：{before_date}")
                    
                    await interaction.response.send_message(
                        f"✅ 已设置时间范围：\n" + "\n".join(time_info), ephemeral=True
                    )
            
            elif action.value == "view_settings":
                prefs = await database.get_user_search_preferences(user_id)
                
                embed = discord.Embed(
                    title="🔍 当前搜索偏好设置",
                    color=0x3498db
                )
                
                # 作者偏好
                author_info = []
                if prefs['include_authors']:
                    authors = [f"<@{uid}>" for uid in prefs['include_authors']]
                    author_info.append(f"**只看作者：** {', '.join(authors)}")
                
                if prefs['exclude_authors']:
                    authors = [f"<@{uid}>" for uid in prefs['exclude_authors']]
                    author_info.append(f"**屏蔽作者：** {', '.join(authors)}")
                
                if not author_info:
                    author_info.append("**作者偏好：** 无限制")
                
                embed.add_field(
                    name="作者设置",
                    value="\n".join(author_info),
                    inline=False
                )
                
                # 时间偏好
                time_info = []
                if prefs['after_date']:
                    after_dt = datetime.datetime.fromisoformat(prefs['after_date'])
                    time_info.append(f"**开始时间：** {after_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                
                if prefs['before_date']:
                    before_dt = datetime.datetime.fromisoformat(prefs['before_date'])
                    time_info.append(f"**结束时间：** {before_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                
                if not time_info:
                    time_info.append("**时间范围：** 无限制")
                
                embed.add_field(
                    name="时间设置",
                    value="\n".join(time_info),
                    inline=False
                )
                
                embed.set_footer(text="使用 /搜索偏好 命令来修改这些设置")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
        
        except Exception as e:
            await interaction.response.send_message(f"❌ 操作失败：{e}", ephemeral=True)

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
        mild_penalty="轻度惩罚阈值 (0.0-1.0)"
    )
    @app_commands.choices(preset=[
        app_commands.Choice(name="平衡配置 (默认)", value="balanced"),
        app_commands.Choice(name="偏重时间新鲜度", value="time_focused"),
        app_commands.Choice(name="偏重内容质量", value="quality_focused"),
        app_commands.Choice(name="偏重受欢迎程度", value="popularity_focused"),
        app_commands.Choice(name="严格质量控制", value="strict_quality")
    ])
    async def configure_ranking(
        self, 
        interaction: discord.Interaction,
        preset: app_commands.Choice[str] = None,
        time_weight: float = None,
        tag_weight: float = None,
        reaction_weight: float = None,
        time_decay: float = None,
        reaction_log_base: int = None,
        severe_penalty: float = None,
        mild_penalty: float = None
    ):
        # 检查权限 (需要管理员权限)
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("此命令需要管理员权限。", ephemeral=True)
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
                if time_weight is not None or tag_weight is not None or reaction_weight is not None:
                    # 计算当前权重总和
                    current_total = RankingConfig.TIME_WEIGHT_FACTOR + RankingConfig.TAG_WEIGHT_FACTOR + RankingConfig.REACTION_WEIGHT_FACTOR
                    
                    # 如果权重和不为1，按比例重新分配
                    if abs(current_total - 1.0) > 0.001:
                        RankingConfig.TIME_WEIGHT_FACTOR = RankingConfig.TIME_WEIGHT_FACTOR / current_total
                        RankingConfig.TAG_WEIGHT_FACTOR = RankingConfig.TAG_WEIGHT_FACTOR / current_total
                        RankingConfig.REACTION_WEIGHT_FACTOR = RankingConfig.REACTION_WEIGHT_FACTOR / current_total
                
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
                color=0x00ff00
            )
            
            embed.add_field(
                name="权重配置",
                value=f"• 时间权重：**{RankingConfig.TIME_WEIGHT_FACTOR:.1%}**\n"
                      f"• 标签权重：**{RankingConfig.TAG_WEIGHT_FACTOR:.1%}**\n"
                      f"• 反应权重：**{RankingConfig.REACTION_WEIGHT_FACTOR:.1%}**\n"
                      f"• 时间衰减率：**{RankingConfig.TIME_DECAY_RATE}**\n"
                      f"• 反应对数基数：**{RankingConfig.REACTION_LOG_BASE}**",
                inline=True
            )
            
            embed.add_field(
                name="惩罚机制",
                value=f"• 严重惩罚阈值：**{RankingConfig.SEVERE_PENALTY_THRESHOLD}**\n"
                      f"• 轻度惩罚阈值：**{RankingConfig.MILD_PENALTY_THRESHOLD}**\n"
                      f"• 严重惩罚系数：**{RankingConfig.SEVERE_PENALTY_FACTOR}**",
                inline=True
            )
            
            # 添加算法说明
            embed.add_field(
                name="算法说明",
                value="新的排序算法将立即生效，影响所有后续搜索结果。\n"
                      "时间权重基于指数衰减，标签权重基于Wilson Score算法。",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError as e:
            await interaction.response.send_message(f"❌ 配置错误：{e}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 配置失败：{e}", ephemeral=True)

    @app_commands.command(name="查看排序配置", description="查看当前搜索排序算法配置")
    async def view_ranking_config(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔧 当前排序算法配置",
            description="智能混合权重排序算法参数",
            color=0x3498db
        )
        
        embed.add_field(
            name="权重配置",
            value=f"• 时间权重：**{RankingConfig.TIME_WEIGHT_FACTOR:.1%}**\n"
                  f"• 标签权重：**{RankingConfig.TAG_WEIGHT_FACTOR:.1%}**\n"
                  f"• 反应权重：**{RankingConfig.REACTION_WEIGHT_FACTOR:.1%}**\n"
                  f"• 时间衰减率：**{RankingConfig.TIME_DECAY_RATE}**\n"
                  f"• 反应对数基数：**{RankingConfig.REACTION_LOG_BASE}**",
            inline=True
        )
        
        embed.add_field(
            name="惩罚机制",
            value=f"• 严重惩罚阈值：**{RankingConfig.SEVERE_PENALTY_THRESHOLD}**\n"
                  f"• 轻度惩罚阈值：**{RankingConfig.MILD_PENALTY_THRESHOLD}**\n"
                  f"• 严重惩罚系数：**{RankingConfig.SEVERE_PENALTY_FACTOR:.1%}**\n"
                  f"• 轻度惩罚系数：**{RankingConfig.MILD_PENALTY_FACTOR:.1%}**",
            inline=True
        )
        
        embed.add_field(
            name="算法特性",
            value="• **Wilson Score**：置信度评估标签质量\n"
                  "• **指数衰减**：时间新鲜度自然衰减\n"
                  "• **智能惩罚**：差评内容自动降权\n"
                  "• **可配置权重**：灵活调整排序偏好",
            inline=False
        )
        
        embed.set_footer(text="管理员可使用 /排序算法配置 命令调整参数")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----- 创建搜索按钮 -----
    @app_commands.command(name="创建频道搜索", description="在当前帖子内创建频道搜索按钮")
    async def create_channel_search(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在帖子内使用此命令。", ephemeral=True)
            return
        
        # 获取父频道ID用于搜索
        channel_id = interaction.channel.parent_id
        view = PersistentChannelSearchView(channel_id)
        
        # 创建美观的embed
        embed = discord.Embed(
            title="🔍 频道搜索",
            description=f"搜索 <#{channel_id}> 频道中的所有帖子",
            color=0x3498db
        )
        embed.add_field(
            name="使用方法",
            value="点击下方按钮开始搜索，可以按标签、关键词等条件筛选帖子",
            inline=False
        )
        
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 已创建频道搜索按钮。", ephemeral=True)

    @app_commands.command(name="创建全局搜索", description="在当前频道创建全局搜索按钮")
    async def create_global_search(self, interaction: discord.Interaction):
        view = PersistentGlobalSearchView()
        
        # 创建美观的embed
        embed = discord.Embed(
            title="🌐 全局搜索",
            description="搜索服务器内所有论坛频道的帖子",
            color=0x2ecc71
        )
        embed.add_field(
            name="使用方法",
            value="1. 点击下方按钮选择要搜索的论坛频道\n2. 设置搜索条件（标签、关键词等）\n3. 查看搜索结果",
            inline=False
        )
        
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 已创建全局搜索按钮。", ephemeral=True)

    # ----- Embed 构造 -----
    def _build_thread_embed(self, thread_row: dict, guild: discord.Guild):
        thread_id = thread_row['thread_id']
        title = thread_row['title']
        original_poster_id = thread_row['author_id']
        created_time = datetime.datetime.fromisoformat(thread_row['created_at'])
        last_active_time = datetime.datetime.fromisoformat(thread_row['last_active_at'])
        reaction_count = thread_row['reaction_count']
        reply_count = thread_row['reply_count']
        tags_str = thread_row.get('tags', '') or ''
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        first_message_excerpt = thread_row['first_message_excerpt'] or ''
        attachment_url = thread_row['thumbnail_url']

        embed = discord.Embed(title=title, description=f"作者 <@{original_poster_id}>")
        
        # 基础统计信息
        basic_stats = (
            f"发帖日期: **{created_time.strftime('%Y-%m-%d %H:%M:%S')}** | "
            f"最近活跃: **{last_active_time.strftime('%Y-%m-%d %H:%M:%S')}**\n"
            f"最高反应数: **{reaction_count}** | 总回复数: **{reply_count}**\n"
            f"标签: **{', '.join(tags) if tags else '无'}**"
        )
        
        embed.add_field(
            name="统计",
            value=basic_stats,
            inline=False,
        )
        
        excerpt_display = first_message_excerpt[:200] + "..." if len(first_message_excerpt) > 200 else (first_message_excerpt or "无内容")
        embed.add_field(name="首楼摘要", value=excerpt_display, inline=False)
        
        if attachment_url:
            embed.set_thumbnail(url=attachment_url)
        
        embed.url = f"https://discord.com/channels/{guild.id}/{thread_row['channel_id']}/threads/{thread_id}"
        return embed

# ----- 持久化搜索按钮 -----
class PersistentChannelSearchView(discord.ui.View):
    def __init__(self, channel_id: int, thread_id: int = None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.thread_id = thread_id

    @discord.ui.button(label="🔍 搜索本频道", style=discord.ButtonStyle.primary, custom_id="persistent_channel_search")
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 从按钮所在的消息中获取embed，从embed中提取channel_id
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            # 从description中提取channel_id
            import re
            match = re.search(r'<#(\d+)>', embed.description or "")
            if match:
                channel_id = int(match.group(1))
            else:
                # 如果无法从embed中提取，使用默认值
                channel_id = self.channel_id
        else:
            channel_id = self.channel_id
            
        view = TagSelectionView(channel_id)
        await view.setup(interaction.guild, interaction.user.id)
        await interaction.response.send_message("选择要搜索的标签：", view=view, ephemeral=True)

class PersistentGlobalSearchView(discord.ui.View):
    def __init__(self, message_id: str = None):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🌐 选择频道搜索", style=discord.ButtonStyle.success, custom_id="persistent_global_search")
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 获取所有论坛频道
        all_forum_channels = [ch for ch in interaction.guild.channels if isinstance(ch, discord.ForumChannel)]
        
        # 从TagSystem获取已索引的频道ID（使用缓存）
        tag_system = interaction.client.get_cog("TagSystem")
        if tag_system:
            indexed_channel_ids = tag_system.indexed_channel_ids
        else:
            # 如果TagSystem不可用，回退到数据库查询
            indexed_channel_ids = set(await database.get_indexed_channel_ids())
        
        # 只保留已索引的论坛频道
        forum_channels = [ch for ch in all_forum_channels if ch.id in indexed_channel_ids]
        
        if not forum_channels:
            await interaction.response.send_message("暂无已索引的论坛频道。请先使用 `/构建索引` 命令对频道进行索引。", ephemeral=True)
            return
        
        view = ChannelSelectionView(forum_channels)
        await interaction.response.send_message("选择要搜索的频道：", view=view, ephemeral=True)

class ChannelSelectionView(discord.ui.View):
    def __init__(self, channels: list[discord.ForumChannel]):
        super().__init__(timeout=300)
        
        # 如果频道太多，分批处理
        options = []
        for channel in channels[:25]:  # Discord限制25个选项
            options.append(discord.SelectOption(
                label=channel.name,
                value=str(channel.id),
                description=f"ID: {channel.id}"
            ))
        
        self.channel_select = discord.ui.Select(
            placeholder="选择论坛频道...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.channel_select.callback = self.channel_selected
        self.add_item(self.channel_select)

    async def channel_selected(self, interaction: discord.Interaction):
        channel_id = int(self.channel_select.values[0])
        view = TagSelectionView(channel_id)
        await view.setup(interaction.guild, interaction.user.id)
        await interaction.response.edit_message(content="选择要搜索的标签：", view=view)

# ----- 标签选择界面 -----
class TagSelectionView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.include_tags = set()
        self.exclude_tags = set()
        self.include_keywords = []
        self.exclude_keywords = []
        self.exclude_mode = False  # False=正选模式, True=反选模式
        self.search_cog = None  # 将在setup中设置
        self.user_id = None  # 将在setup中设置
        self.sort_method = "comprehensive"  # 默认使用综合排序
        self.sort_order = "desc"  # 默认降序排序
        self.tag_page = 0  # 当前标签页
        self.tags_per_page = 10  # 每页显示的标签数
        self.all_tags = []  # 所有标签列表
        
    async def setup(self, guild: discord.Guild, user_id: int = None):
        """获取标签并设置UI"""
        self.user_id = user_id
        
        # 直接从Discord频道获取标签
        channel = guild.get_channel(self.channel_id)
        if isinstance(channel, discord.ForumChannel):
            # 获取频道的所有可用标签
            self.all_tags = [(tag.id, tag.name) for tag in channel.available_tags]
        else:
            self.all_tags = []
        
        # 清空现有items
        self.clear_items()
        
        # 计算当前页的标签
        start_idx = self.tag_page * self.tags_per_page
        end_idx = start_idx + self.tags_per_page
        current_page_tags = self.all_tags[start_idx:end_idx]
        
        # 添加标签按钮 (第0-1行，每行5个)
        for i, (tag_id, tag_name) in enumerate(current_page_tags):
            style = discord.ButtonStyle.secondary
            
            # 优化：无论在哪种模式下，都显示已选择的标签状态
            if tag_name in self.include_tags:
                style = discord.ButtonStyle.green  # 正选标签始终显示绿色
            elif tag_name in self.exclude_tags:
                style = discord.ButtonStyle.red    # 反选标签始终显示红色
                
            button = TagButton(tag_name, style)
            button.row = i // 5  # 每行5个按钮，分配到第0-1行
            self.add_item(button)
        
        # 添加第2行按钮：上一页 + 控制按钮 + 下一页
        if len(self.all_tags) > self.tags_per_page:
            self.add_item(TagPageButton("◀️ 上一页", "prev"))
        
        # 控制按钮放在中间 (第2行)
        mode_button = ModeToggleButton(self.exclude_mode)
        mode_button.row = 2
        self.add_item(mode_button)
        
        keyword_button = KeywordButton()
        keyword_button.row = 2
        self.add_item(keyword_button)
        
        # 添加升序/降序按钮
        sort_order_button = SortOrderButton(self.sort_order)
        sort_order_button.row = 2
        self.add_item(sort_order_button)
        
        if len(self.all_tags) > self.tags_per_page:
            self.add_item(TagPageButton("▶️ 下一页", "next"))
        
        # 添加排序选择器 (第3行)
        sort_select = SortMethodSelect(self.sort_method)
        sort_select.row = 3
        self.add_item(sort_select)

    async def update_search_results(self, interaction: discord.Interaction, *, edit_original: bool = True):
        """更新搜索结果"""
        try:
            # 获取用户搜索偏好
            prefs = await database.get_user_search_preferences(self.user_id)
            
            include_tags = list(self.include_tags)
            exclude_tags = list(self.exclude_tags)
            
            # 处理关键词
            keywords_parts = []
            if self.include_keywords:
                keywords_parts.append(" ".join(self.include_keywords))
            
            include_keywords = " ".join(keywords_parts) if keywords_parts else ""
            
            per_page = await database.get_results_per_page(self.user_id)
            
            # 应用用户偏好
            include_authors = prefs['include_authors'] if prefs['include_authors'] else None
            exclude_authors = prefs['exclude_authors'] if prefs['exclude_authors'] else None
            after_ts = prefs['after_date']
            before_ts = prefs['before_date']
            
            total = await database.count_threads_for_search(
                include_tags, exclude_tags, include_keywords, 
                [self.channel_id], include_authors, exclude_authors, after_ts, before_ts
            )
            
            mode_text = "反选模式 (选择要排除的标签)" if self.exclude_mode else "正选模式 (选择要包含的标签)"
            
            if total == 0:
                # 没有结果时只更新标签选择界面
                content = f"选择要搜索的标签 - {mode_text}：\n\n🔍 **搜索结果：** 未找到符合条件的帖子"
                if edit_original:
                    await interaction.response.edit_message(content=content, view=self, embeds=[])
                else:
                    await interaction.edit_original_response(content=content, view=self, embeds=[])
                return
            
            threads = await database.search_threads(
                include_tags, exclude_tags, include_keywords,
                [self.channel_id], include_authors, exclude_authors, after_ts, before_ts,
                0, per_page, self.sort_method, self.sort_order
            )
            
            # 获取搜索cog来构建embed
            if not self.search_cog:
                self.search_cog = interaction.client.get_cog("Search")
            
            embeds = [self.search_cog._build_thread_embed(t, interaction.guild) for t in threads]
            
            # 创建搜索结果view
            results_view = SearchResultsView(
                self.search_cog, self.user_id,
                include_tags, exclude_tags, include_keywords,
                [self.channel_id], include_authors, exclude_authors, after_ts, before_ts,
                1, per_page, total, self.sort_method, self.sort_order
            )
            
            # 合并两个view的按钮
            combined_view = CombinedSearchView(self, results_view)
            
            content = f"选择要搜索的标签 - {mode_text}：\n\n🔍 **搜索结果：** 找到 {total} 个帖子 (第1/{results_view.max_page}页)"
            
            if edit_original:
                await interaction.response.edit_message(content=content, view=combined_view, embeds=embeds)
            else:
                await interaction.edit_original_response(content=content, view=combined_view, embeds=embeds)
            
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"搜索出错: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"搜索出错: {e}", ephemeral=True)

class TagPageButton(discord.ui.Button):
    def __init__(self, label: str, action: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=2)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
        
        max_page = (len(tag_view.all_tags) - 1) // tag_view.tags_per_page
        
        if self.action == "prev":
            tag_view.tag_page = max(0, tag_view.tag_page - 1)
        elif self.action == "next":
            tag_view.tag_page = min(max_page, tag_view.tag_page + 1)
        
        # 重新设置UI，保持当前状态
        await tag_view.setup(interaction.guild, tag_view.user_id)
        
        # 如果在CombinedSearchView中，需要重新执行搜索以保持搜索结果
        if hasattr(self.view, 'tag_view'):
            await tag_view.update_search_results(interaction, edit_original=True)
        else:
            mode_text = "反选模式 (选择要排除的标签)" if tag_view.exclude_mode else "正选模式 (选择要包含的标签)"
            await interaction.response.edit_message(content=f"选择要搜索的标签 - {mode_text}：", view=tag_view)

class TagButton(discord.ui.Button):
    def __init__(self, tag_name: str, style: discord.ButtonStyle):
        super().__init__(label=tag_name, style=style)
        self.tag_name = tag_name

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
        
        if not tag_view.exclude_mode:  # 正选模式
            if self.tag_name in tag_view.include_tags:
                tag_view.include_tags.remove(self.tag_name)
            else:
                tag_view.include_tags.add(self.tag_name)
                # 如果之前在反选中，移除
                if self.tag_name in tag_view.exclude_tags:
                    tag_view.exclude_tags.remove(self.tag_name)
        else:  # 反选模式
            if self.tag_name in tag_view.exclude_tags:
                tag_view.exclude_tags.remove(self.tag_name)
            else:
                tag_view.exclude_tags.add(self.tag_name)
                # 如果之前在正选中，移除
                if self.tag_name in tag_view.include_tags:
                    tag_view.include_tags.remove(self.tag_name)
        
        # 更新按钮样式（与setup方法保持一致）
        if self.tag_name in tag_view.include_tags:
            self.style = discord.ButtonStyle.green
        elif self.tag_name in tag_view.exclude_tags:
            self.style = discord.ButtonStyle.red
        else:
            self.style = discord.ButtonStyle.secondary
        
        # 立即更新搜索结果
        await tag_view.update_search_results(interaction, edit_original=True)

class ModeToggleButton(discord.ui.Button):
    def __init__(self, exclude_mode: bool):
        label = "🔄 切换到正选" if exclude_mode else "🔄 切换到反选"
        style = discord.ButtonStyle.danger if exclude_mode else discord.ButtonStyle.primary
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
            is_combined = True
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
            is_combined = False
            
        tag_view.exclude_mode = not tag_view.exclude_mode
        
        # 先更新标签按钮样式
        await tag_view.setup(interaction.guild, tag_view.user_id)
        
        if is_combined:
            # 在CombinedSearchView中，重新执行搜索以保持搜索结果
            await tag_view.update_search_results(interaction, edit_original=True)
        else:
            # 在单独的TagSelectionView中
            mode_text = "反选模式 (选择要排除的标签)" if tag_view.exclude_mode else "正选模式 (选择要包含的标签)"
            await interaction.response.edit_message(content=f"选择要搜索的标签 - {mode_text}：", view=tag_view)

class SortMethodSelect(discord.ui.Select):
    def __init__(self, current_sort: str):
        options = [
            discord.SelectOption(
                label="🧠 综合排序",
                value="comprehensive",
                description="智能混合权重算法（时间+标签+反应）",
                default=(current_sort == "comprehensive")
            ),
            discord.SelectOption(
                label="🕐 按发帖时间",
                value="created_time", 
                description="按帖子创建时间倒序排列",
                default=(current_sort == "created_time")
            ),
            discord.SelectOption(
                label="⏰ 按活跃时间",
                value="active_time",
                description="按最近活跃时间倒序排列", 
                default=(current_sort == "active_time")
            ),
            discord.SelectOption(
                label="🎉 按反应数",
                value="reaction_count",
                description="按最高反应数倒序排列",
                default=(current_sort == "reaction_count")
            )
        ]
        super().__init__(placeholder="选择排序方式...", options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
            
        tag_view.sort_method = self.values[0]
        
        # 更新选择器的选中状态
        for option in self.options:
            option.default = (option.value == self.values[0])
        
        # 立即更新搜索结果
        await tag_view.update_search_results(interaction, edit_original=True)

class SortOrderButton(discord.ui.Button):
    def __init__(self, sort_order: str):
        label = "📉 降序" if sort_order == "desc" else "📈 升序"
        super().__init__(label=label, style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
        
        # 切换排序方向
        tag_view.sort_order = "asc" if tag_view.sort_order == "desc" else "desc"
        
        # 更新按钮标签
        self.label = "📉 降序" if tag_view.sort_order == "desc" else "📈 升序"
        
        # 立即更新搜索结果
        await tag_view.update_search_results(interaction, edit_original=True)

class KeywordButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📝 关键词", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是TagSelectionView
        if hasattr(self.view, 'tag_view'):
            # 在CombinedSearchView中
            tag_view = self.view.tag_view  # type: ignore
        else:
            # 在TagSelectionView中
            tag_view = self.view  # type: ignore
        
        await interaction.response.send_modal(KeywordModal(tag_view))

class KeywordModal(discord.ui.Modal, title="设置关键词过滤"):
    def __init__(self, parent_view: TagSelectionView):
        super().__init__()
        self.parent_view = parent_view
        
        self.include_input = discord.ui.TextInput(
            label="包含关键词 (逗号分隔)",
            placeholder="在标题或首楼中必须包含的关键词",
            required=False,
            default=", ".join(self.parent_view.include_keywords)
        )
        self.exclude_input = discord.ui.TextInput(
            label="排除关键词 (逗号分隔)", 
            placeholder="在标题或首楼中不能包含的关键词",
            required=False,
            default=", ".join(self.parent_view.exclude_keywords)
        )
        self.add_item(self.include_input)
        self.add_item(self.exclude_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.include_keywords = [k.strip() for k in self.include_input.value.split(',') if k.strip()]
        self.parent_view.exclude_keywords = [k.strip() for k in self.exclude_input.value.split(',') if k.strip()]
        
        # 关键词更新后立即更新搜索结果
        await self.parent_view.update_search_results(interaction, edit_original=True)

# ----- 搜索结果分页 -----
class SearchResultsView(discord.ui.View):
    def __init__(self, cog: Search, user_id: int, include_tags, exclude_tags, keywords, channel_ids, include_authors, exclude_authors, after_ts, before_ts, current_page, per_page, total, sort_method: str = "comprehensive", sort_order: str = "desc"):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.include_tags = include_tags
        self.exclude_tags = exclude_tags
        self.keywords = keywords
        self.channel_ids = channel_ids
        self.include_authors = include_authors
        self.exclude_authors = exclude_authors
        self.after_ts = after_ts
        self.before_ts = before_ts
        self.per_page = per_page
        self.total = total
        self.max_page = max(1, math.ceil(total / per_page))
        self.current_page = current_page
        self.sort_method = sort_method
        self.sort_order = sort_order
        
        # 添加分页按钮
        self.add_item(PageButton("⏮️", "first"))
        self.add_item(PageButton("◀️", "prev"))
        self.add_item(CurrentPageButton(self.current_page, self.max_page))
        self.add_item(PageButton("▶️", "next"))
        self.add_item(PageButton("⏭️", "last"))

    async def go_to_page(self, interaction: discord.Interaction, target_page: int):
        if target_page < 1 or target_page > self.max_page:
            await interaction.response.send_message("页码超出范围。", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        offset = (target_page - 1) * self.per_page
        threads = await database.search_threads(
            self.include_tags, self.exclude_tags, self.keywords,
            self.channel_ids, self.include_authors, self.exclude_authors, self.after_ts, self.before_ts,
            offset, self.per_page, self.sort_method, self.sort_order
        )
        
        embeds = [self.cog._build_thread_embed(t, interaction.guild) for t in threads]
        self.current_page = target_page
        
        # 更新当前页按钮
        for item in self.children:
            if isinstance(item, CurrentPageButton):
                item.label = f"{self.current_page}/{self.max_page}"
        
        await interaction.edit_original_response(embeds=embeds, view=self)

class PageButton(discord.ui.Button):
    def __init__(self, label: str, action: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是SearchResultsView
        if hasattr(self.view, 'results_view'):
            # 在CombinedSearchView中
            results_view = self.view.results_view  # type: ignore
        else:
            # 在独立的SearchResultsView中
            results_view = self.view  # type: ignore
            
        page = results_view.current_page
        
        if self.action == "first":
            page = 1
        elif self.action == "prev":
            page = max(1, results_view.current_page - 1)
        elif self.action == "next":
            page = min(results_view.max_page, results_view.current_page + 1)
        elif self.action == "last":
            page = results_view.max_page
        
        await self.go_to_page_combined(interaction, page, results_view)
    
    async def go_to_page_combined(self, interaction: discord.Interaction, target_page: int, results_view):
        if target_page < 1 or target_page > results_view.max_page:
            await interaction.response.send_message("页码超出范围。", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        offset = (target_page - 1) * results_view.per_page
        threads = await database.search_threads(
            results_view.include_tags, results_view.exclude_tags, results_view.keywords,
            results_view.channel_ids, results_view.include_authors, results_view.exclude_authors, 
            results_view.after_ts, results_view.before_ts,
            offset, results_view.per_page, results_view.sort_method, results_view.sort_order
        )
        
        embeds = [results_view.cog._build_thread_embed(t, interaction.guild) for t in threads]
        results_view.current_page = target_page
        
        # 更新当前页按钮
        for item in self.view.children:
            if isinstance(item, CurrentPageButton):
                item.label = f"{results_view.current_page}/{results_view.max_page}"
        
        # 如果在CombinedSearchView中，更新内容
        if hasattr(self.view, 'tag_view'):
            tag_view = self.view.tag_view  # type: ignore
            mode_text = "反选模式 (选择要排除的标签)" if tag_view.exclude_mode else "正选模式 (选择要包含的标签)"
            content = f"选择要搜索的标签 - {mode_text}：\n\n🔍 **搜索结果：** 找到 {results_view.total} 个帖子 (第{results_view.current_page}/{results_view.max_page}页)"
            await interaction.edit_original_response(content=content, embeds=embeds, view=self.view)
        else:
            await interaction.edit_original_response(embeds=embeds, view=self.view)

class CurrentPageButton(discord.ui.Button):
    def __init__(self, current: int, total: int):
        super().__init__(label=f"{current}/{total}", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        # 检查当前view是CombinedSearchView还是SearchResultsView
        if hasattr(self.view, 'results_view'):
            # 在CombinedSearchView中
            await interaction.response.send_modal(GotoPageModal(self.view.results_view, self.view))  # type: ignore
        else:
            # 在独立的SearchResultsView中
            await interaction.response.send_modal(GotoPageModal(self.view, None))  # type: ignore

class GotoPageModal(discord.ui.Modal, title="跳转页码"):
    def __init__(self, search_view: SearchResultsView, combined_view=None):
        super().__init__()
        self.search_view = search_view
        self.combined_view = combined_view
        
        self.page_input = discord.ui.TextInput(
            label="页码",
            placeholder=f"输入要跳转的页码 (1-{search_view.max_page})",
            required=True
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_input.value)
            if self.combined_view:
                # 在CombinedSearchView中，使用go_to_page_combined
                page_button = PageButton("", "")  # 临时创建一个button
                page_button.view = self.combined_view
                await page_button.go_to_page_combined(interaction, page, self.search_view)
            else:
                # 在独立的SearchResultsView中
                await self.search_view.go_to_page(interaction, page)
        except ValueError:
            await interaction.response.send_message("请输入有效的数字。", ephemeral=True)

# ----- 合并视图：标签选择 + 搜索结果分页 -----
class CombinedSearchView(discord.ui.View):
    def __init__(self, tag_view: TagSelectionView, results_view: SearchResultsView):
        super().__init__(timeout=600)
        self.tag_view = tag_view
        self.results_view = results_view
        
        # 添加标签按钮 (第0-1行，每页最多10个)
        tag_buttons = [item for item in tag_view.children if isinstance(item, TagButton)]
        for button in tag_buttons:
            # 保持原有的row设置（在setup中已经设置为0-1行）
            self.add_item(button)
        
        # 添加第2行所有按钮：标签翻页 + 控制按钮 (按添加顺序：上一页 + 控制按钮 + 下一页)
        second_row_buttons = [item for item in tag_view.children if isinstance(item, (TagPageButton, ModeToggleButton, KeywordButton, SortOrderButton))]
        for button in second_row_buttons:
            self.add_item(button)
        
        # 添加排序选择器 (第3行)
        sort_select = [item for item in tag_view.children if isinstance(item, SortMethodSelect)]
        for select in sort_select:
            self.add_item(select)
        
        # 添加搜索结果分页按钮 (第4行，最多5个)
        page_buttons = [item for item in results_view.children if isinstance(item, (PageButton, CurrentPageButton))]
        for button in page_buttons[:5]:  # 最多5个按钮
            button.row = 4
            self.add_item(button) 