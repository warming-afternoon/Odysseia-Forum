import logging
import discord
from typing import TYPE_CHECKING
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from shared.models.author import Author

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class AuthorService:
    """
    负责获取和缓存作者信息到数据库的服务。
    """

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker):
        self.bot = bot
        self.session_factory = session_factory

    async def get_or_fetch_author(
        self,
        author_id: int,
        guild: discord.Guild,
        source_member: discord.Member | discord.User | None = None,
    ):
        """
        获取或抓取作者信息并存入数据库。
        策略：传入对象 -> 服务器缓存 -> 机器人全局缓存 -> API 调用。
        """
        user_obj: discord.User | discord.Member | None = None

        # 优先使用直接传入的 source_member/user 对象
        if source_member and source_member.id == author_id:
            user_obj = source_member

        # 尝试从服务器成员缓存中获取
        if not user_obj:
            user_obj = guild.get_member(author_id)

        # 尝试从机器人全局用户缓存中获取
        if not user_obj:
            user_obj = self.bot.get_user(author_id)

        # 最后手段：API 调用
        if not user_obj:
            try:
                # 使用调度器来避免速率限制
                user_obj = await self.bot.api_scheduler.submit(
                    coro_factory=lambda: self.bot.fetch_user(author_id),
                    priority=8,  # 优先级略低于帖子同步
                )
            except discord.NotFound:
                logger.warning(f"无法通过 API找到 ID 为 {author_id} 的用户。")
                return
            except Exception as e:
                logger.error(
                    f"通过API获取用户 {author_id} 信息时出错: {e}", exc_info=True
                )
                return

        if not user_obj:
            logger.warning(f"所有方法都无法获取 ID 为 {author_id} 的用户对象。")
            return

        # 准备要插入或更新的数据
        author_data = {
            "id": user_obj.id,
            "name": user_obj.name,
            "global_name": user_obj.global_name,
            "display_name": user_obj.display_name,
            "avatar_url": user_obj.display_avatar.url,
        }

        # 使用 INSERT ... ON CONFLICT DO UPDATE (Upsert)
        stmt = sqlite_insert(Author).values(author_data)
        update_stmt = stmt.on_conflict_do_update(
            index_elements=["id"], set_=author_data
        )

        try:
            async with self.session_factory() as session:
                await session.execute(update_stmt)
                await session.commit()
        except Exception as e:
            logger.error(f"更新作者 {author_id} 信息到数据库时失败: {e}", exc_info=True)
