import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import and_, asc, desc, func, select

from api.v1.schemas.booklist import BooklistItemUpdateRequest
from api.v1.schemas.booklist.booklist_item_detail import BooklistItemDetail
from api.v1.schemas.search.author_detail import AuthorDetail
from models import Author, BooklistItem, Thread

logger = logging.getLogger(__name__)


class BooklistItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def update_booklist_item(
        self, booklist_id: int, thread_id: int, update_data: BooklistItemUpdateRequest
    ) -> Optional[BooklistItem]:
        """
        更新书单项信息（推荐语、排序权重）
        """
        statement = select(BooklistItem).where(
            and_(
                BooklistItem.booklist_id == booklist_id,
                BooklistItem.thread_id == thread_id,
            )
        )
        result = await self.session.execute(statement)
        item = result.scalar_one_or_none()

        if not item:
            return None

        update_fields = update_data.model_dump(exclude_unset=True)
        if not update_fields:
            return item  # 没有需要更新的字段

        for key, value in update_fields.items():
            setattr(item, key, value)

        self.session.add(item)
        try:
            await self.session.commit()
            await self.session.refresh(item)
            logger.info(f"书单项 (书单ID: {booklist_id}, 帖子ID: {thread_id}) 已更新")
            return item
        except Exception as e:
            logger.error(f"更新书单项失败: {e}", exc_info=True)
            await self.session.rollback()
            raise

    async def get_booklist_item_detail(
        self, booklist_id: int, thread_id: int
    ) -> Optional[BooklistItemDetail]:
        """
        获取单个书单项的详细信息
        """
        query = (
            select(BooklistItem, Thread, Author)
            .join(Thread, BooklistItem.thread_id == Thread.thread_id)  # type: ignore
            .join(Author, Thread.author_id == Author.id)  # type: ignore
            .where(
                and_(
                    BooklistItem.booklist_id == booklist_id,
                    BooklistItem.thread_id == thread_id,
                )
            )
            .options(selectinload(Thread.tags))  # type: ignore
        )
        result = await self.session.execute(query)
        row = result.one_or_none()

        if not row:
            return None

        item, thread, author = row
        author_detail = AuthorDetail.model_validate(author)
        item_detail = BooklistItemDetail(
            booklist_item_id=item.id,
            thread_id=thread.thread_id,
            guild_id=thread.guild_id,
            channel_id=thread.channel_id,
            title=thread.title,
            author=author_detail,
            created_at=thread.created_at,
            last_active_at=thread.last_active_at,
            reaction_count=thread.reaction_count,
            reply_count=thread.reply_count,
            display_count=thread.display_count,
            first_message_excerpt=thread.first_message_excerpt,
            latest_update_at=thread.latest_update_at,
            latest_update_link=thread.latest_update_link,
            collection_count=thread.collection_count,
            thumbnail_urls=thread.thumbnail_urls or [],
            tags=[tag.name for tag in thread.tags],
            virtual_tags=[],
            comment=item.comment,
            display_order=item.display_order,
            added_at=item.created_at,
            tournament_participated_at=item.tournament_participated_at,
            collected_flag=False,
        )
        return item_detail

    async def get_fallback_covers(self, booklist_ids: List[int]) -> Dict[int, str]:
        """
        为无封面书单批量取“最近加入的 item”所属 Thread 的第一张 thumbnail。

        统一按 BooklistItem.display_order DESC 取首条（等价加入时间倒序），不按 display_type 分支。
        当首条 item 的 thumbnail_urls 为空时，按排序顺序回退到下一条候选，
        直至找到带非空 thumbnail_urls 的 item 或候选耗尽。
        返回 {booklist_id: thumbnail_url}，找不到候选的书单不出现。
        """
        if not booklist_ids:
            return {}

        # 取每个书单前 N 条候选，首条无缩略图时可在 Python 端顺序回退
        FALLBACK_CANDIDATE_LIMIT = 10

        rn = (
            func.row_number()
            .over(
                partition_by=BooklistItem.booklist_id,
                order_by=(desc(BooklistItem.display_order), desc(BooklistItem.id)),
            )
            .label("rn")
        )

        ranked_subq = (
            select(
                BooklistItem.booklist_id.label("booklist_id"),
                Thread.thumbnail_urls.label("thumbnail_urls"),
                rn,
            )
            .join(Thread, BooklistItem.thread_id == Thread.thread_id)  # type: ignore
            .where(BooklistItem.booklist_id.in_(booklist_ids))  # type: ignore
            .subquery()
        )

        stmt = (
            select(
                ranked_subq.c.booklist_id,
                ranked_subq.c.thumbnail_urls,
                ranked_subq.c.rn,
            )
            .where(ranked_subq.c.rn <= FALLBACK_CANDIDATE_LIMIT)
            .order_by(ranked_subq.c.booklist_id, ranked_subq.c.rn)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        covers: Dict[int, str] = {}
        for booklist_id, thumbnail_urls, _ in rows:
            bid = int(booklist_id)
            if bid in covers:
                # 已经选定更靠前的候选，后续行跳过
                continue
            if not thumbnail_urls:
                continue
            if isinstance(thumbnail_urls, list) and thumbnail_urls:
                covers[bid] = thumbnail_urls[0]
        return covers

    async def get_booklist_items_with_details(
        self,
        booklist_id: int,
        display_type: int,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[BooklistItemDetail], int]:
        """
        分页获取书单内的帖子详情，并根据指定的排序方式排序
        """

        query = (
            select(BooklistItem, Thread, Author)
            .join(Thread, BooklistItem.thread_id == Thread.thread_id)  # type: ignore
            .join(Author, Thread.author_id == Author.id)  # type: ignore
            .where(BooklistItem.booklist_id == booklist_id)  # type: ignore
            .options(selectinload(Thread.tags))  # type: ignore
        )

        # 根据 display_type 应用不同的排序规则
        if display_type == 2:
            # 按 display_order 升序
            query = query.order_by(asc(BooklistItem.display_order))
        else:
            # 默认按加入时间倒序
            query = query.order_by(desc(BooklistItem.created_at))

        # 计数
        count_stmt = select(func.count()).select_from(query.alias("sub"))
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one_or_none() or 0

        # 获取数据
        data_stmt = query.offset(offset).limit(limit)
        result = await self.session.execute(data_stmt)
        rows = result.all()

        items = []
        for item, thread, author in rows:
            author_detail = AuthorDetail.model_validate(author)
            item_detail = BooklistItemDetail(
                booklist_item_id=item.id,
                thread_id=thread.thread_id,
                guild_id=thread.guild_id,
                channel_id=thread.channel_id,
                title=thread.title,
                author=author_detail,
                created_at=thread.created_at,
                last_active_at=thread.last_active_at,
                reaction_count=thread.reaction_count,
                reply_count=thread.reply_count,
                display_count=thread.display_count,
                first_message_excerpt=thread.first_message_excerpt,
                latest_update_at=thread.latest_update_at,
                latest_update_link=thread.latest_update_link,
                collection_count=thread.collection_count,
                thumbnail_urls=thread.thumbnail_urls or [],
                tags=[tag.name for tag in thread.tags],
                virtual_tags=[],
                comment=item.comment,
                display_order=item.display_order,
                added_at=item.created_at,
                tournament_participated_at=item.tournament_participated_at,
                collected_flag=False,
            )
            items.append(item_detail)

        return items, total
