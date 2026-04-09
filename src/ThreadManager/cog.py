import asyncio
import datetime
from typing import TYPE_CHECKING, Any, Dict, List

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker

from config.config_service import ConfigService
from core.cache_service import CacheService
from shared.enum.search_config_type import SearchConfigType
from shared.safe_defer import safe_defer

if TYPE_CHECKING:
    from bot_main import MyBot
    from core.sync_service import SyncService
import logging

from core.tag_repository import TagRepository
from core.thread_repository import ThreadRepository
from ThreadManager.batch_update_service import BatchUpdateService
from core.follow_repository import ThreadFollowRepository
from ThreadManager.views.vote_view import TagVoteView

logger = logging.getLogger(__name__)


class ThreadManager(commands.Cog):
    """处理标签同步与评价"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        config: dict,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.cache_service = bot.cache_service
        self.sync_service = bot.sync_service

        # 从配置中读取更新间隔，如果未配置则默认为30秒
        update_interval = self.config.get("performance", {}).get(
            "batch_update_interval", 30
        )
        self.batch_update_service = BatchUpdateService(
            session_factory, sync_service=self.sync_service, interval=update_interval
        )

        logger.info("ThreadManager 模块已加载")

    # 3. 添加 cog_load 和 cog_unload 生命周期方法
    async def cog_load(self):
        """当 Cog 加载时，启动后台任务。"""
        self.batch_update_service.start()

    async def cog_unload(self):
        """当 Cog 卸载时（例如机器人关闭），确保所有数据都被写入。"""
        await self.batch_update_service.stop()

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道是否已索引"""
        return self.cache_service.is_channel_indexed(channel_id)

    async def _notify_user_of_mutex_removal(
        self, thread: discord.Thread, conflicts: List[Dict[str, Any]]
    ) -> bool:
        """通知用户他们的帖子因为互斥规则被修改了。如果发送了公开通知，则返回 True。"""
        if not thread.owner:
            logger.warning(f"无法获取帖子 {thread.id} 的作者，无法发送通知。")
            return False

        author = thread.owner

        parent_channel_str = "未知频道"
        if thread.parent:
            parent_channel_str = f"[{thread.parent.name}]({thread.parent.jump_url})"

        embed = discord.Embed(
            title="🏷️ 帖子标签自动修改通知",
            description=f"您发表在 {thread.guild.name} > {parent_channel_str} 的帖子 "
            f"[{thread.name}]({thread.jump_url})\n"
            f"其标签已被自动修改",
            color=discord.Color.orange(),
        )
        embed.add_field(name="原因", value="触发了互斥标签规则", inline=False)

        for i, conflict_info in enumerate(conflicts):
            group = conflict_info["group"]
            removed_tags = conflict_info["removed"]
            added_tag = conflict_info["added"]

            sorted_rules = sorted(group.rules, key=lambda r: r.priority)

            group_tags_list = []
            if group.override_tag_name:
                group_tags_list.append(f"覆盖标签: **{group.override_tag_name}**")

            priority_rules_list = [
                f"优先级 {j + 1}: **{rule.tag_name}**"
                for j, rule in enumerate(sorted_rules)
            ]
            group_tags_list.extend(priority_rules_list)

            group_tags_str = "\n".join(group_tags_list)

            removed_tags_str = ", ".join(f"{t}" for t in removed_tags)
            value_parts = [f"**规则**:\n{group_tags_str}"]

            if added_tag:
                value_parts.append(f"**结果**: \n应用覆盖标签: **{added_tag}**")
                value_parts.append(f"移除标签: **{removed_tags_str}**")
            else:
                value_parts.append("**结果**: \n保留最高优先级标签")
                value_parts.append(f"移除标签: **{removed_tags_str}**")

            embed.add_field(
                name=f"冲突组 {i + 1}",
                value="\n".join(value_parts),
                inline=False,
            )

        footer_text = (
            "如需修改，请右键点击左侧频道列表中的帖子名，\n选择'编辑帖子'来调整标签"
        )
        embed.set_footer(text=footer_text)

        try:
            # 尝试通过调度器发送私信
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: author.send(embed=embed), priority=5
            )
            logger.debug(f"已向用户 {author.id} 发送互斥标签移除私信通知")
            return False
        except discord.Forbidden:
            logger.warning(f"无法向用户 {author.id} 发送私信，将在原帖中发送公开通知")
            # 发送备用公开通知
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: thread.send(
                    content=f":crying_cat_face: \n{author.mention}，您的帖子标签将被修改，详情请见下方解释",
                    embed=embed,
                ),
                priority=5,
            )
            return True
        except Exception as e:
            logger.error(f"向用户 {author.id} 发送私信时发生未知错误。", exc_info=e)
            return False

    async def _notify_management_of_mutex_conflict(
        self,
        thread: discord.Thread,
        conflicts: List[Dict[str, Any]],
        user_notified_publicly: bool,
    ):
        """在帖子中通知管理组发生了互斥标签冲突。"""
        management_role_id = self.bot.config.get("management_role_id")
        if not management_role_id:
            logger.warning(
                "未在 config.json 中配置 management_role_id，无法发送管理通知。"
            )
            return

        content = f"<@&{management_role_id}>"
        embed = None

        # 仅当没有在帖子内公开通知用户时，才创建 embed
        if not user_notified_publicly:
            embed = discord.Embed(
                title="检测到互斥标签",
                description=f"帖子 [{thread.name}]({thread.jump_url}) 存在互斥标签",
                color=discord.Color.greyple(),
            )

            for i, conflict_info in enumerate(conflicts):
                group = conflict_info["group"]
                removed_tags = conflict_info["removed"]
                added_tag = conflict_info["added"]

                sorted_rules = sorted(group.rules, key=lambda r: r.priority)

                group_tags_list = []
                if group.override_tag_name:
                    group_tags_list.append(f"覆盖标签: **{group.override_tag_name}**")

                priority_rules_list = [
                    f"优先级 {j + 1} : **{rule.tag_name}**"
                    for j, rule in enumerate(sorted_rules)
                ]
                group_tags_list.extend(priority_rules_list)

                group_tags_str = "\n".join(group_tags_list)

                removed_tags_str = ", ".join(f"{t}" for t in removed_tags)
                value_parts = [f"**规则**:\n{group_tags_str}"]

                if added_tag:
                    value_parts.append(f"**结果**: \n应用覆盖标签: **{added_tag}**")
                    value_parts.append(f"移除标签: **{removed_tags_str}**")
                else:
                    value_parts.append("**结果**: \n保留最高优先级标签")
                    value_parts.append(f"移除标签: **{removed_tags_str}**")

                embed.add_field(
                    name=f"冲突组 {i + 1}",
                    value="\n".join(value_parts),
                    inline=False,
                )

        async def send_notification():
            if embed:
                await thread.send(content=content, embed=embed)
            else:
                await thread.send(content=content)

        await self.bot.api_scheduler.submit(
            coro_factory=send_notification,
            priority=3,
        )
        logger.debug(f"已在帖子 {thread.id} 中发送互斥标签管理通知。")

    async def apply_mutex_tag_rules(self, thread: discord.Thread) -> bool:
        """检查并应用互斥标签规则。如果进行了修改，则返回 True。"""
        applied_tags = thread.applied_tags
        if (
            not applied_tags
            or len(applied_tags) < 2
            or not thread.parent
            or not isinstance(thread.parent, discord.ForumChannel)
        ):
            return False

        post_tag_name_to_obj = {tag.name: tag for tag in applied_tags}
        post_tag_names = set(post_tag_name_to_obj.keys())

        async with self.session_factory() as session:
            repo = ConfigService(session)
            groups = await repo.get_all_mutex_groups_with_rules()
            notify_config = await repo.get_search_config(
                SearchConfigType.NOTIFY_ON_MUTEX_CONFLICT
            )
            should_notify_management = notify_config and notify_config.value_int == 1

        tags_to_remove = set()
        tags_to_add = set()
        all_conflicts = []

        for group in groups:
            sorted_rules = sorted(group.rules, key=lambda r: r.priority)
            group_tag_names = {rule.tag_name for rule in sorted_rules}

            conflicting_names_in_post = post_tag_names.intersection(group_tag_names)

            if len(conflicting_names_in_post) > 1:
                override_tag_obj = None
                # --- 检查覆盖标签 ---
                if group.override_tag_name:
                    # 检查覆盖标签是否在当前频道的可用标签中
                    override_tag_obj = discord.utils.get(
                        thread.parent.available_tags, name=group.override_tag_name
                    )

                if override_tag_obj:
                    # 应用覆盖逻辑
                    # 移除所有与本组冲突的标签
                    for name in conflicting_names_in_post:
                        tags_to_remove.add(post_tag_name_to_obj[name])
                    # 添加覆盖标签
                    tags_to_add.add(override_tag_obj)

                    # 记录冲突信息用于通知
                    all_conflicts.append(
                        {
                            "group": group,
                            "removed": conflicting_names_in_post,
                            "added": override_tag_obj.name,
                        }
                    )
                else:
                    # 回退到原始优先级逻辑
                    # 找到帖子中优先级最高的那个冲突标签
                    highest_priority_tag_name = ""
                    for rule in sorted_rules:
                        if rule.tag_name in conflicting_names_in_post:
                            highest_priority_tag_name = rule.tag_name
                            break

                    # 移除除了最高优先级之外的其他冲突标签
                    tags_to_remove_from_group = {
                        post_tag_name_to_obj[name]
                        for name in conflicting_names_in_post
                        if name != highest_priority_tag_name
                    }
                    tags_to_remove.update(tags_to_remove_from_group)

                    all_conflicts.append(
                        {
                            "group": group,
                            "removed": {t.name for t in tags_to_remove_from_group},
                            "added": None,
                        }
                    )

        if tags_to_remove or tags_to_add:
            # 发送通知
            if all_conflicts:
                # 通知发帖人 (私信)，并检查是否在帖子内发送了公开通知
                user_notified_publicly = await self._notify_user_of_mutex_removal(
                    thread, all_conflicts
                )
                # 如果配置开启，通知管理组 (在帖子内)
                if should_notify_management:
                    await self._notify_management_of_mutex_conflict(
                        thread, all_conflicts, user_notified_publicly
                    )

            # 计算最终标签
            final_tags = list((set(applied_tags) - tags_to_remove) | tags_to_add)

            removed_names = {t.name for t in tags_to_remove}
            added_names = {t.name for t in tags_to_add}
            logger.info(
                f"帖子 {thread.id} 发现互斥标签，将移除: {', '.join(removed_names) if removed_names else '无'}，将添加: {', '.join(added_names) if added_names else '无'}"
            )

            try:
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: thread.edit(applied_tags=final_tags),
                    priority=2,
                )
                return True
            except Exception as e:
                logger.error(f"自动修改帖子 {thread.id} 的标签时失败", exc_info=e)

        return False

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self.is_channel_indexed(channel_id=thread.parent_id):
            # 延时 5s 再进行同步。减小请求失败概率
            await asyncio.sleep(5)
            modified = await self.apply_mutex_tag_rules(thread)
            if modified:
                # 标签被修改，on_thread_update会被触发，届时再同步
                return
            await self.sync_service.sync_thread(thread=thread)

            # 新帖子发布时，只为贴主添加关注
            if thread.owner_id and not thread.owner.bot if thread.owner else True:
                async with self.session_factory() as session:
                    follow_service = ThreadFollowRepository(session)
                    await follow_service.add_follow(
                        user_id=thread.owner_id,
                        thread_id=thread.id,
                        auto_view=False,  # 贴主发布时不标记为已查看
                    )
                    logger.debug(
                        f"新帖子 {thread.id}，已为贴主 {thread.owner_id} 添加关注"
                    )

    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember):
        """监听用户加入帖子事件，自动添加关注"""
        try:
            thread = member.thread
            if not thread or not self.is_channel_indexed(thread.parent_id):
                return

            # 获取用户对象
            user = await thread.guild.fetch_member(member.id)
            if user.bot:
                return

            async with self.session_factory() as session:
                follow_service = ThreadFollowRepository(session)
                # 用户主动加入时，标记为已查看
                await follow_service.add_follow(
                    user_id=member.id, thread_id=thread.id, auto_view=True
                )
                logger.debug(f"用户 {member.id} 加入帖子 {thread.id}，已自动添加关注")
        except Exception as e:
            logger.error(f"用户加入帖子自动关注失败: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if self.is_channel_indexed(channel_id=after.parent_id) and (
            before.applied_tags != after.applied_tags or before.name != after.name
        ):
            modified = await self.apply_mutex_tag_rules(after)
            if modified:
                # 标签被修改，会再次触发 on_thread_update，届时再同步
                return
            await self.sync_service.sync_thread(thread=after)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        if self.is_channel_indexed(thread.parent_id):
            async with self.session_factory() as session:
                repo = ThreadRepository(session=session)
                await repo.delete_thread_index(thread_id=thread.id)
            # 缓存现在由全局事件处理，此处不再需要手动刷新

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            not message.guild
            or not isinstance(message.channel, discord.Thread)
            or message.author.bot
        ):
            return

        thread = message.channel
        if thread.id == message.id:
            return

        if self.is_channel_indexed(thread.parent_id):
            await self.batch_update_service.add_update(thread.id, message.created_at)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 如果是首楼消息被编辑，需要重新同步整个帖子
                if payload.message_id == channel.id:
                    # 因为这是 raw 事件，缓存的 channel 对象可能不是最新的
                    # 我们需要确保同步的是最完整的数据
                    await self.sync_service.sync_thread(
                        thread=channel, fetch_if_incomplete=True
                    )
                else:
                    # 普通消息编辑只更新活跃时间
                    async with self.session_factory() as session:
                        repo = ThreadRepository(session)
                        # payload 中没有编辑时间，所以我们用当前时间
                        await repo.update_thread_last_active_at(
                            channel.id, datetime.datetime.now(datetime.timezone.utc)
                        )
        except Exception:
            logger.warning("处理消息编辑事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 如果首楼被删除，删除整个索引
                if payload.message_id == channel.id:
                    async with self.session_factory() as session:
                        repo = ThreadRepository(session=session)
                        await repo.delete_thread_index(thread_id=channel.id)
                    # 缓存现在由全局事件处理，此处不再需要手动刷新
                else:
                    # 普通消息删除
                    await self.batch_update_service.add_deletion(channel.id)
        except Exception:
            logger.warning("处理消息删除事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 只有对首楼消息的反应才更新统计
                if payload.message_id == channel.id:
                    await self.bot.api_scheduler.submit(
                        coro_factory=lambda: self._update_reaction_count(channel),
                        priority=5,
                    )
        except Exception:
            logger.warning("处理反应添加事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 只有对首楼消息的反应才更新统计
                if payload.message_id == channel.id:
                    await self.bot.api_scheduler.submit(
                        coro_factory=lambda: self._update_reaction_count(channel),
                        priority=5,
                    )
        except Exception:
            logger.warning("处理反应移除事件失败", exc_info=True)

    async def _update_reaction_count(self, thread: discord.Thread):
        """(协程) 更新帖子的反应数。如果记录不存在，则触发一次完整的同步进行补录。"""
        try:
            # 优先从缓存获取，失败则API调用
            first_msg = thread.get_partial_message(thread.id)
            first_msg = await first_msg.fetch()

            reaction_count = (
                max([r.count for r in first_msg.reactions])
                if first_msg.reactions
                else 0
            )

            async with self.session_factory() as session:
                repo = ThreadRepository(session)

                update_succeeded = await repo.update_thread_reaction_count(
                    thread.id, reaction_count
                )

                # 如果更新失败，说明数据库中可能没有这条记录
                if not update_succeeded:
                    logger.warning(
                        f"帖子 {thread.id} 的反应数更新失败（记录可能不存在），触发一次完整的同步进行补录。"
                    )
                    # 调用 sync_service 进行补录
                    await self.sync_service.sync_thread(thread=thread)

        except discord.NotFound:
            # 如果在 fetch() 过程中帖子被删除，这是一种正常情况，记录一下并忽略
            logger.info(f"尝试更新反应数时，帖子 {thread.id} 已被删除，操作中止。")
        except Exception:
            logger.warning(
                f"更新或补录反应数时失败 (帖子ID: {thread.id})", exc_info=True
            )

    async def pre_sync_forum_tags(self, channel: discord.ForumChannel):
        """
        预同步一个论坛频道的所有可用标签，确保它们都存在于数据库中。
        """
        logger.debug(
            f"开始为频道 '{channel.name}' (ID: {channel.id}) 预同步所有可用标签..."
        )
        if not channel.available_tags:
            logger.debug(
                f"频道 '{channel.name}' (ID: {channel.id}) 没有任何可用标签，跳过同步。"
            )
            return

        tags_data = {tag.id: tag.name for tag in channel.available_tags}

        try:
            async with self.session_factory() as session:
                tag_service = TagRepository(session=session)
                await tag_service.get_or_create_tags(tags_data)
            logger.debug(
                f"为频道 '{channel.name}' (ID: {channel.id}) 预同步了 {len(tags_data)} 个标签。"
            )
        except Exception as e:
            logger.error(
                f"为频道 '{channel.name}' (ID: {channel.id}) 预同步标签时出错: {e}",
                exc_info=True,
            )
            # 即使这里失败，我们也不应该中断整个索引过程，
            # 因为后续的 consumer 仍然有机会（虽然有风险）去创建标签。
            # 抛出异常让调用者决定如何处理。
            raise

    @app_commands.command(name="发布更新", description="发布帖子更新（仅贴主可用）")
    @app_commands.describe(消息链接="更新消息的Discord链接")
    async def publish_update(self, interaction: discord.Interaction, 消息链接: str):
        """发布帖子更新，刷新最后更新时间和链接"""
        await safe_defer(interaction)

        try:
            # 检查是否在帖子中
            if not isinstance(interaction.channel, discord.Thread):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
                        "❌ 此命令只能在帖子中使用", ephemeral=True
                    ),
                    priority=1,
                )
                return

            thread = interaction.channel

            # 检查是否是贴主
            if thread.owner_id != interaction.user.id:
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
                        "❌ 只有贴主才能发布更新", ephemeral=True
                    ),
                    priority=1,
                )
                return

            # 验证消息链接格式
            import re

            link_pattern = r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)"
            match = re.match(link_pattern, 消息链接)

            if not match:
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
                        "❌ 无效的消息链接格式\n"
                        "正确格式: https://discord.com/channels/服务器ID/频道ID/消息ID",
                        ephemeral=True,
                    ),
                    priority=1,
                )
                return

            # 更新数据库
            async with self.session_factory() as session:
                repo = ThreadRepository(session)
                success = await repo.update_thread_update_info(
                    thread_id=thread.id, latest_update_link=消息链接
                )

                if success:
                    await self.bot.api_scheduler.submit(
                        coro_factory=lambda: interaction.followup.send(
                            "✅ 已发布更新！关注此帖的用户将收到通知", ephemeral=False
                        ),
                        priority=1,
                    )
                    logger.info(f"帖子 {thread.id} 发布了更新: {消息链接}")
                else:
                    await self.bot.api_scheduler.submit(
                        coro_factory=lambda: interaction.followup.send(
                            "❌ 更新失败，帖子可能未被索引", ephemeral=True
                        ),
                        priority=1,
                    )
        except Exception as e:
            logger.error(f"发布更新失败: {e}", exc_info=True)
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    f"❌ 发布更新时出错: {str(e)}", ephemeral=True
                ),
                priority=1,
            )

    @app_commands.command(
        name="标签评价", description="对当前帖子的标签进行评价（赞或踩）"
    )
    async def tag_rate(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        try:
            if not isinstance(interaction.channel, discord.Thread):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
                        "此命令只能在帖子中使用。", ephemeral=True
                    ),
                    priority=1,
                )
                return

            if not interaction.channel.applied_tags:
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: interaction.followup.send(
                        "该帖子没有应用任何标签。", ephemeral=True
                    ),
                    priority=1,
                )
                return

            tag_map = {tag.id: tag.name for tag in interaction.channel.applied_tags}

            view = TagVoteView(
                thread_id=interaction.channel.id,
                thread_name=interaction.channel.name,
                tag_map=tag_map,
                session_factory=self.session_factory,
                api_scheduler=self.bot.api_scheduler,
            )
            # 获取初始统计数据
            async with self.session_factory() as session:
                repo = ThreadRepository(session)
                initial_stats = await repo.get_tag_vote_stats(
                    interaction.channel.id, tag_map
                )

            # 使用初始统计数据创建嵌入
            embed = view.create_embed(initial_stats)

            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    embed=embed, view=view, ephemeral=True
                ),
                priority=1,
            )
        except Exception as e:
            error_message = f"❌ 命令执行失败: {e}"
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    error_message, ephemeral=True
                ),
                priority=1,
            )
