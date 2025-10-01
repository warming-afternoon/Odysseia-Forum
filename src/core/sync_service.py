import discord
import datetime
import logging
import re
from typing import Coroutine, Optional, Union, TYPE_CHECKING
from sqlalchemy.ext.asyncio import async_sessionmaker

# 导入 ThreadManagerRepository，因为 sync_thread 方法会用到它
from src.ThreadManager.repository import ThreadManagerRepository

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class SyncService:
    """
    负责将 Discord 帖子数据同步到数据库的服务
    """

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
    ):
        self.bot = bot
        self.session_factory = session_factory

    @staticmethod
    async def _fetch_message_wrapper(fetch_coro: Coroutine) -> discord.Message | None:
        """
        包装一个获取消息的协程
        如果协程成功，返回消息对象；如果抛出 NotFound，返回 None。
        """
        try:
            return await fetch_coro
        except discord.NotFound:
            return None

    async def _parse_thread_data(self, thread: discord.Thread) -> Optional[dict]:
        """
        解析一个帖子，根据其结构（普通或重建）返回标准化的数据字典。
        如果帖子无效或不满足索引条件，返回 None。
        """
        messages = [msg async for msg in thread.history(limit=2, oldest_first=True)]
        if not messages:
            return None

        first_msg = messages[0]
        second_msg = messages[1] if len(messages) > 1 else None

        # 默认值
        final_author_id = thread.owner_id or 0
        final_created_at = thread.created_at
        excerpt = ""
        thumbnail_url = ""

        reaction_count = (
            max([r.count for r in first_msg.reactions]) if first_msg.reactions else 0
        )

        first_msg_content = first_msg.content or ""

        # --- 检查是否为重建帖 ---
        match_id = re.search(r"发帖人[:：\s*]*<@(\d+)>", first_msg_content)
        match_time = re.search(
            r"原始创建时间[:：\s*]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日.*?(\d{2}):(\d{2})",
            first_msg_content,
        )

        if match_id:
            # --- 是重建帖，执行解析和检查 ---

            # 1. 解析作者和时间
            try:
                final_author_id = int(match_id.group(1))
                if match_time:
                    year, month, day, hour, minute = map(int, match_time.groups())
                    # 假设原始时间是 UTC+8 时区
                    local_tz = datetime.timezone(datetime.timedelta(hours=8))
                    local_dt = datetime.datetime(
                        year, month, day, hour, minute, tzinfo=local_tz
                    )
                    final_created_at = local_dt.astimezone(datetime.timezone.utc)
            except Exception as e:
                logger.warning(
                    f"重建帖 {thread.id} 解析作者或时间失败: {e}。中止对其的索引"
                )
                return None

            # 2. 检查并解析补档链接: https://discord.com/channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
            match_url = re.search(
                r"补档[:：\s*]*\d+\.\s*\[.*?\]\((https://discord.com/channels/(\d+)/(\d+)/(\d+))\)",
                first_msg_content,
            )
            if not match_url:
                logger.debug(
                    f"重建帖 {thread.id} 的补档图片链接提取失败。中止对其的索引"
                )
                return None

            # 3. 通过API获取补档消息的附件
            try:
                guild_id, channel_id, message_id = map(int, match_url.groups()[1:])
                target_channel = self.bot.get_channel(
                    channel_id
                ) or await self.bot.fetch_channel(channel_id)
                if not isinstance(target_channel, discord.abc.Messageable):
                    logger.warning(
                        f"重建帖 {thread.id} 的补档链接指向了一个非消息频道 (ID: {channel_id})。中止对其的索引"
                    )
                    return None

                # 使用调度器提交API请求
                target_message = await self.bot.api_scheduler.submit(
                    coro_factory=lambda: target_channel.fetch_message(message_id),
                    priority=5,  # 中等优先级
                )

                if not target_message or not target_message.attachments:
                    logger.debug(
                        f"重建帖 {thread.id} 的补档消息 ({message_id}) 不存在或没有附件。中止对其的索引"
                    )
                    return None

                thumbnail_url = target_message.attachments[0].url

            except (discord.NotFound, discord.Forbidden, Exception) as e:
                logger.error(
                    f"重建帖 {thread.id} 获取补档消息时出错: {e}。中止对其的索引",
                    exc_info=True,
                )
                return None

            # 4. 从第二楼获取内容摘要
            if second_msg:
                excerpt_ori = second_msg.content
                potential_reaction_numbers = []

                # 使用正则表达式查找所有“表情符号 + 空格 + 数字”的模式
                emoji_pattern = r"(?:<:\w+:\d+>|[\U00002600-\U000027BF\U0001F000-\U0001FFFF\U0001F900-\U0001F9FF])"
                matches = re.findall(
                    f"{emoji_pattern}\\s*(\\d+)(?=\\s|\\||$)", second_msg.content
                )

                for num_str in matches:
                    try:
                        potential_reaction_numbers.append(int(num_str))
                    except ValueError:
                        logger.warning(
                            f"无法将重建帖 {thread.id} 中的反应数 '{num_str}' 转换为整数",
                            exc_info=True,
                        )

                if potential_reaction_numbers:
                    reaction_count_ori = max(potential_reaction_numbers)
                    reaction_count += reaction_count_ori

                # 从摘要中删除元数据行
                reaction_line_pattern = f"^-#\\s*{emoji_pattern}\\s*\\d+.*$"
                attachment_line_pattern = r"^-#\s*📎\s+.*?\s*\(.*?\)$"
                edited_line_pattern = r"^-#\s*\(已编辑\)$"

                cleaning_pattern = re.compile(
                    f"(?:{reaction_line_pattern}|{attachment_line_pattern}|{edited_line_pattern})\\n?",
                    re.MULTILINE,
                )

                cleaned_excerpt = cleaning_pattern.sub("", excerpt_ori).strip()
                excerpt = cleaned_excerpt

        else:
            # --- 是普通帖 ---
            excerpt = first_msg.content
            if first_msg.attachments:
                thumbnail_url = first_msg.attachments[0].url

        return {
            "thread_id": thread.id,
            "channel_id": thread.parent_id,
            "title": thread.name,
            "author_id": final_author_id,
            "created_at": final_created_at,
            "last_active_at": discord.utils.snowflake_time(thread.last_message_id)
            if thread.last_message_id
            else thread.created_at,
            "reaction_count": reaction_count,
            "reply_count": thread.message_count,
            "not_found_count": 0,
            "first_message_excerpt": excerpt,
            "thumbnail_url": thumbnail_url,
        }

    async def sync_thread(
        self,
        thread: Union[discord.Thread, int],
        priority: int = 10,
        *,
        fetch_if_incomplete: bool = False,
    ):
        """
        同步一个帖子的数据到数据库，包括其标签。
        该方法可以接受一个完整的帖子对象，或者一个帖子ID。
        """
        if isinstance(thread, int):
            thread_id = thread
            try:
                fetched_channel = await self.bot.api_scheduler.submit(
                    coro_factory=lambda tid=thread_id: self.bot.fetch_channel(tid),
                    priority=priority,
                )
                if not isinstance(fetched_channel, discord.Thread):
                    logger.warning(
                        f"sync_thread: 获取到的 channel {thread_id} 不是一个帖子，将标记为未找到。"
                    )
                    async with self.session_factory() as session:
                        repo = ThreadManagerRepository(session=session)
                        await repo.increment_not_found_count(thread_id=thread_id)
                    return
                thread = fetched_channel
            except discord.NotFound:
                logger.warning(
                    f"sync_thread: 无法找到帖子 {thread_id}，可能已被删除。将增加其 not_found_count。"
                )
                async with self.session_factory() as session:
                    repo = ThreadManagerRepository(session=session)
                    await repo.increment_not_found_count(thread_id=thread_id)
                return
            except Exception as e:
                logger.error(
                    f"sync_thread: 通过ID {thread_id} 获取帖子时发生未知错误: {e}",
                    exc_info=True,
                )
                return

        elif fetch_if_incomplete:
            try:
                thread_id = thread.id
                thread = await self.bot.api_scheduler.submit(
                    coro_factory=lambda tid=thread_id: self.bot.fetch_channel(tid),
                    priority=priority,
                )
            except discord.NotFound:
                logger.warning(
                    f"sync_thread (fetch_if_incomplete): 无法找到帖子 {thread.id}，可能已被删除。将增加其 not_found_count。"
                )
                async with self.session_factory() as session:
                    repo = ThreadManagerRepository(session=session)
                    await repo.increment_not_found_count(thread_id=thread.id)
                return

        assert isinstance(thread, discord.Thread)

        # 调用辅助方法解析帖子数据
        thread_data = await self._parse_thread_data(thread)

        # 检查解析结果，如果为 None 则中止同步
        if thread_data is None:
            # logger.info(f"帖子 {thread.id} 不满足索引条件或无效，同步中止。")
            return

        # 准备标签数据并存入数据库
        tags_data = {t.id: t.name for t in thread.applied_tags or []}

        async with self.session_factory() as session:
            repo = ThreadManagerRepository(session=session)
            await repo.add_or_update_thread_with_tags(
                thread_data=thread_data, tags_data=tags_data
            )
