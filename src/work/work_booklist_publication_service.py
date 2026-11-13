"""从统一作品构造书单的 Discord 发布内容。"""

from urllib.parse import urlsplit

from sqlalchemy import Float, case, cast, func, select

from api.v2.config import get_work_config
from identity.identity_repository import IdentityRepository
from core.booklist_repository import BooklistRepository
from models.booklist_item import BooklistItem
from models.work import Work
from models.work_statistics import WorkStatistics
from shared.enum.search_config_type import SearchConfigDefaults
from work.work_repository import WorkRepository


class WorkBooklistPublicationService:
    """保留外部 Bot 协议，同时按作品来源生成正确的公开链接。"""

    def __init__(self, session):
        """复用后台任务会话，网络调用在退出会话后进行。"""
        self.session = session

    async def build(self, booklist_id, guild_id, thread_id, discord_user_id):
        """发布前重新验证公开范围与作者绑定，禁止导出隐藏作品。"""
        booklist = await BooklistRepository(self.session).get_booklist(booklist_id)
        if booklist is None or not booklist.is_public or booklist.is_default:
            raise ValueError("书单不存在或不可公开发布")
        mapping = await IdentityRepository(self.session).fluxer_to_discord(
            [booklist.fluxer_owner_id] if booklist.fluxer_owner_id else []
        )
        if mapping.get(booklist.fluxer_owner_id) != str(discord_user_id):
            raise ValueError("书单作者的 Discord 绑定已变化，请重新发布")
        config = get_work_config()
        frontend_url = config.get("auth", {}).get("frontend_url", "").rstrip("/")
        parsed = urlsplit(frontend_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("作品网页地址未正确配置")
        # Discord 发布消息没有当前网页访问者，深渊及隐藏内容不能写入公共预览。
        filters, _, _ = WorkRepository.visibility_filters(
            {"show_nsfw": True, "can_view_abyss": False, "config": config}
        )
        statement = (
            select(BooklistItem, Work)
            .join(Work, BooklistItem.work_id == Work.id)
            .outerjoin(WorkStatistics, WorkStatistics.work_id == Work.id)
            .where(BooklistItem.booklist_id == booklist_id, *filters)
        )
        sort = {
            "display_order": BooklistItem.display_order,
            "join_time": BooklistItem.created_at,
            "created_at": Work.created_at,
            "last_active_at": Work.last_active_at,
            "reaction_count": func.coalesce(WorkStatistics.reaction_count, 0),
            "reply_count": func.coalesce(WorkStatistics.reply_count, 0),
            "collection_count": func.coalesce(WorkStatistics.collection_count, 0),
        }.get(booklist.default_sort_method, BooklistItem.created_at)
        if booklist.default_sort_method == "hot":
            reactions = func.coalesce(WorkStatistics.reaction_count, 0)
            sort = func.log(
                case((reactions > 1, cast(reactions, Float)), else_=1.0)
            ) / 2.302585092994046
            sort += func.extract("epoch", Work.created_at).cast(Float) / float(
                SearchConfigDefaults.REDDIT_HOT_TIME_DECAY.value
            )
        statement = statement.order_by(
            sort.asc()
            if booklist.default_sort_order == "asc"
            and booklist.default_sort_method != "hot"
            else sort.desc(),
            BooklistItem.id.desc(),
        ).limit(1000)
        pairs = (await self.session.execute(statement)).all()
        items = []
        for item, work in pairs:
            # 来源标识只由数据库读取，不能把 Fluxer snowflake 拼成 Discord 链接。
            url = f"{frontend_url}/works/{work.id}"
            if (
                work.source_platform == "discord"
                and work.source_guild_id.isdecimal()
                and work.source_thread_id.isdecimal()
            ):
                url = f"https://discord.com/channels/{work.source_guild_id}/{work.source_thread_id}"
            items.append({"title": work.title, "url": url, "review": item.comment or ""})
        return {
            "booklist_id": booklist_id,
            "thread_url": f"https://discord.com/channels/{guild_id}/{thread_id}",
            "discord_user_id": str(discord_user_id),
            "title": booklist.title,
            "description": booklist.description or "",
            "cover_image_url": booklist.cover_image_url or "",
            "items": items,
        }
