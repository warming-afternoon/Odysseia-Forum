import logging
from datetime import datetime
from typing import List, Optional, Sequence, cast

from sqlalchemy import ColumnElement, case, delete, func, literal_column, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select

from dto.meta import ChannelThreadCount
from dto.search.fts_result_dto import FTSResultDTO
from models import Tag, TagVote, Thread, ThreadFollow, ThreadTagLink
from models.booklist_item import BooklistItem
from models.user_collection import UserCollection
from shared.enum import CollectionType
from ThreadManager.update_data_dto import UpdateData

import asyncio
import hashlib
import json
import rjieba
import re
from shared.enum import CacheKeys, SearchTimeout

logger = logging.getLogger(__name__)


def _batch_cut(keywords: list[str]) -> list[list[str]]:
    """批量 jieba 分词"""
    return [list(rjieba.cut(kw)) for kw in keywords]


def _escape_tsquery_token(token: str) -> str:
    """转义 token 中可能破坏 PostgreSQL tsquery 语法的单引号和反斜杠。"""
    token = token.replace("\\", "\\\\")  # 先转义反斜杠
    return token.replace("'", "''")      # 再转义单引号


def _tokens_to_tsquery_and(tokens: list[str]) -> str:
    """将 token 列表转为 PostgreSQL tsquery AND 表达式。

    最后一个 token 带 :* 前缀匹配后缀。
    例如 ['搬运', '工'] → '搬运 & 工:*'
    """
    if not tokens:
        return ""
    escaped = [_escape_tsquery_token(t.lower()) for t in tokens]
    parts = [f"'{t}'" for t in escaped[:-1]]
    parts.append(f"'{escaped[-1]}':*")
    return " & ".join(parts)



def _build_exemption_tsquery(
    first_token: str, markers: list[str], proximity: int = 4
) -> str:
    """构建豁免标记 tsquery：检查豁免标记是否在排除词 ±N 词位内。

    使用双向 <d> 距离运算符组合（d ∈ [1, proximity]），
    限制豁免标记仅在排除关键词附近（N 个词位内）才生效。
    to_tsvector('simple', ...) 按词顺序分配位置，支持邻近搜索。
    所有字符（包括非 BMP emoji 如 🈲）均可通过 ::tsquery cast 保留。
    """
    first = _escape_tsquery_token(first_token)
    clauses = []
    for marker in markers:
        m = _escape_tsquery_token(marker)
        for d in range(1, proximity + 1):
            clauses.append(f"'{first}' <{d}> '{m}'")
            clauses.append(f"'{m}' <{d}> '{first}'")
    return " | ".join(clauses) if clauses else ""


class ThreadRepository:
    """封装与 Thread 表相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_or_update_thread_with_tags(self, thread_data: dict, tags: list[Tag]):
        """
        添加或更新一个帖子及其标签。
        """
        # 查找现有帖子
        statement = (
            select(Thread)
            .where(Thread.thread_id == thread_data["thread_id"])
            .options(selectinload(Thread.tags))  # type: ignore
        )
        result = await self.session.execute(statement)
        db_thread = result.scalars().first()

        if db_thread:
            # 更新帖子
            for key, value in thread_data.items():
                setattr(db_thread, key, value)

            # 非破坏性地更新标签，以保留 ThreadTagLink 中的投票数据
            current_tag_ids = {tag.id for tag in db_thread.tags}
            new_tag_ids = {tag.id for tag in tags if tag.id is not None}

            tags_to_add_ids = new_tag_ids - current_tag_ids
            tags_to_remove_ids = current_tag_ids - new_tag_ids

            # 移除不再需要的标签关联
            if tags_to_remove_ids:
                db_thread.tags = [
                    t for t in db_thread.tags if t.id not in tags_to_remove_ids
                ]

            # 添加新的标签关联
            if tags_to_add_ids:
                tags_to_add = [t for t in tags if t.id in tags_to_add_ids]
                db_thread.tags.extend(tags_to_add)

            self.session.add(db_thread)
        else:
            # 创建新帖子
            new_thread = Thread(**thread_data)
            new_thread.tags = tags
            self.session.add(new_thread)
        await self.session.commit()

    async def delete_thread_index(self, thread_id: int):
        """删除帖子记录"""
        statement = select(Thread).where(Thread.thread_id == thread_id)  # type: ignore
        result = await self.session.execute(statement)
        db_thread = result.scalars().first()
        if db_thread:
            await self.session.delete(db_thread)
            await self.session.commit()

    async def delete_channel_index(self, channel_id: int):
        """删除指定 Discord 频道下的所有帖子索引及相关关联数据。"""
        # 查找该频道下所有 Thread 的内部 ID 和 Discord ID
        statement = select(Thread.id, Thread.thread_id).where(
            Thread.channel_id == channel_id
        )
        result = await self.session.execute(statement)
        rows = result.all()

        if not rows:
            logger.info(f"频道 {channel_id} 没有需要删除的索引帖子。")
            return

        thread_ids = [row[0] for row in rows]       # 内部主键 ID
        discord_ids = [row[1] for row in rows]       # Discord 帖子 ID

        try:
            # 删除 ThreadTagLink 记录（依赖 thread internal id）
            delete_links = delete(ThreadTagLink).where(
                cast(ColumnElement, ThreadTagLink.thread_id).in_(thread_ids)
            )
            await self.session.execute(delete_links)

            # 删除 TagVote 记录
            delete_votes = delete(TagVote).where(
                cast(ColumnElement, TagVote.thread_id).in_(thread_ids)
            )
            await self.session.execute(delete_votes)

            # 删除 ThreadFollow 记录（依赖 Discord thread_id）
            delete_follows = delete(ThreadFollow).where(
                cast(ColumnElement, ThreadFollow.thread_id).in_(discord_ids)
            )
            await self.session.execute(delete_follows)

            # 删除 BooklistItem 记录（依赖 Discord thread_id）
            delete_items = delete(BooklistItem).where(
                cast(ColumnElement, BooklistItem.thread_id).in_(discord_ids)
            )
            await self.session.execute(delete_items)

            # 删除 UserCollection 记录（target_type=THREAD，依赖 Discord thread_id）
            delete_collections = delete(UserCollection).where(
                UserCollection.target_type == CollectionType.THREAD.value,
                cast(ColumnElement, UserCollection.target_id).in_(discord_ids),
            )
            await self.session.execute(delete_collections)

            # 删除 Thread 记录自身
            delete_threads = delete(Thread).where(
                cast(ColumnElement, Thread.id).in_(thread_ids)
            )
            await self.session.execute(delete_threads)

            await self.session.commit()
            logger.info(
                f"已删除频道 {channel_id} 的所有索引，"
                f"共 {len(thread_ids)} 个帖子及相关关联记录。"
            )
        except Exception:
            await self.session.rollback()
            logger.exception(f"删除频道 {channel_id} 的索引时发生数据库错误")
            raise

    async def update_thread_activity(
        self, thread_id: int, last_active_at: datetime, reply_count: int
    ):
        """更新帖子的活跃时间和回复数"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(
                last_active_at=last_active_at,
                reply_count=reply_count,
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def update_thread_last_active_at(
        self, thread_id: int, last_active_at: datetime
    ):
        """更新帖子的最后活跃时间"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(last_active_at=last_active_at)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def update_thread_reaction_count(
        self, thread_id: int, reaction_count: int
    ) -> bool:
        """更新帖子的反应数。如果更新成功（至少影响了一行），则返回 True，否则返回 False。"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(reaction_count=reaction_count)
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def record_tag_vote(
        self,
        user_id: int,
        thread_id: int,
        tag_id: int,
        vote_value: int,
        tag_map: dict[int, str],
    ) -> dict:
        """
        记录一次标签投票，并更新 ThreadTagLink 表中的 upvotes 和 downvotes。
        """
        # 查找帖子对象以获取其内部ID
        thread_statement = select(Thread).where(Thread.thread_id == thread_id)  # type: ignore
        thread_result = await self.session.execute(thread_statement)
        db_thread = thread_result.scalars().first()
        if not db_thread or not db_thread.id:
            logger.warning(f"record_tag_vote: 未找到 thread_id={thread_id} 的帖子。")
            return {}

        # 查找对应的 ThreadTagLink 记录
        link_stmt = select(ThreadTagLink).where(
            ThreadTagLink.thread_id == db_thread.id,
            ThreadTagLink.tag_id == tag_id,  # type: ignore
        )
        link_result = await self.session.execute(link_stmt)
        link_record = link_result.scalars().first()
        if not link_record:
            logger.warning(f"record_tag_vote: 帖子 {thread_id} 并未应用标签 {tag_id}。")
            return await self.get_tag_vote_stats(thread_id, tag_map)

        # 查找现有投票
        vote_stmt = select(TagVote).where(
            TagVote.user_id == user_id,  # type: ignore
            TagVote.thread_id == db_thread.id,  # type: ignore
            TagVote.tag_id == tag_id,  # type: ignore
        )
        vote_result = await self.session.execute(vote_stmt)
        existing_vote = vote_result.scalars().first()

        if existing_vote:
            previous_vote = existing_vote.vote
            if previous_vote == vote_value:  # 取消投票
                if previous_vote == 1:
                    link_record.upvotes -= 1
                else:
                    link_record.downvotes -= 1
                await self.session.delete(existing_vote)
            else:  # 更改投票
                if previous_vote == 1:
                    link_record.upvotes -= 1
                else:
                    link_record.downvotes -= 1

                if vote_value == 1:
                    link_record.upvotes += 1
                else:
                    link_record.downvotes += 1
                existing_vote.vote = vote_value
                self.session.add(existing_vote)
        else:  # 新投票
            if vote_value == 1:
                link_record.upvotes += 1
            else:
                link_record.downvotes += 1
            new_vote = TagVote(
                user_id=user_id, thread_id=db_thread.id, tag_id=tag_id, vote=vote_value
            )
            self.session.add(new_vote)

        self.session.add(link_record)
        await self.session.commit()

        # 返回该帖子的最新完整统计数据
        return await self.get_tag_vote_stats(thread_id, tag_map)

    async def get_tag_vote_stats(self, thread_id: int, tag_map: dict[int, str]) -> dict:
        """
        获取一个帖子的标签投票统计。
        从与该帖子关联的 ThreadTagLink 记录中聚合数据。
        """
        thread_stmt = select(Thread).where(Thread.thread_id == thread_id)  # type: ignore
        result = await self.session.execute(thread_stmt)
        db_thread = result.scalars().first()

        if not db_thread:
            return {}

        # 查询所有与该帖子相关的 ThreadTagLink 记录
        link_stmt = select(ThreadTagLink).where(ThreadTagLink.thread_id == db_thread.id)  # type: ignore
        link_results = await self.session.execute(link_stmt)
        link_records = link_results.scalars().all()

        stats = {}
        for record in link_records:
            tag_name = tag_map.get(record.tag_id)
            if tag_name:
                stats[tag_name] = {
                    "upvotes": record.upvotes,
                    "downvotes": record.downvotes,
                    "score": record.upvotes - record.downvotes,
                }

        return stats

    async def batch_update_thread_activity(self, updates: dict[int, UpdateData]) -> int:
        """
        批量更新多个帖子的活跃时间和回复数。

        Args:
            updates(dict[int, UpdateData]): {thread_id: {"increment": count, "last_active_at": datetime | None}}

        Returns:
            (int) 成功更新的行数。
        """
        if not updates:
            return 0

        thread_ids_to_update = list(updates.keys())

        # 构建 reply_count 的 CASE 表达式
        reply_count_case = case(
            {
                thread_id: Thread.reply_count + data["increment"]
                for thread_id, data in updates.items()
            },
            value=Thread.thread_id,
            else_=Thread.reply_count,  # 保持原值，如果ID不在case中
        )

        values_to_update = {"reply_count": reply_count_case}

        # 仅在存在有效 last_active_at 值时才构建和添加 case 表达式
        last_active_at_updates = {
            thread_id: data["last_active_at"]
            for thread_id, data in updates.items()
            if data["last_active_at"] is not None
        }

        if last_active_at_updates:
            last_active_at_case = case(
                last_active_at_updates,
                value=Thread.thread_id,
                else_=Thread.last_active_at,
            )
            values_to_update["last_active_at"] = last_active_at_case

        stmt = (
            update(Thread)
            .where(cast(ColumnElement, Thread.thread_id).in_(thread_ids_to_update))
            .values(**values_to_update)
            .execution_options(synchronize_session=False)
        )

        result = await self.session.execute(stmt)
        return result.rowcount

    async def get_existing_thread_ids(self, thread_ids: List[int]) -> List[int]:
        """
        从给定的ID列表中，查询并返回那些在数据库中真实存在的记录ID
        """
        if not thread_ids:
            return []

        stmt = select(Thread.thread_id).where(
            cast(ColumnElement, Thread.thread_id).in_(thread_ids)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def increment_not_found_count(self, thread_id: int) -> bool:
        """当找不到帖子时，将其 not_found_count 计数加一"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(not_found_count=Thread.not_found_count + 1)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def update_thread_update_info(
        self, thread_id: int, latest_update_link: str
    ) -> bool:
        """
        更新帖子的最新更新时间和链接

        Args:
            thread_id: 帖子Discord ID
            latest_update_link: 最新版消息链接

        Returns:
            是否更新成功
        """
        from datetime import datetime, timezone

        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(
                latest_update_at=datetime.now(timezone.utc).replace(tzinfo=None),
                latest_update_link=latest_update_link,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def update_collection_counts(self, thread_ids: List[int], delta: int) -> None:
        """批量更新帖子的被收藏次数"""
        if not thread_ids:
            return

        try:
            statement = (
                update(Thread)
                .where(Thread.thread_id.in_(thread_ids))  # type: ignore
                .values(collection_count=Thread.collection_count + delta)
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(statement)
            await self.session.commit()
        except Exception as e:
            logger.error(f"批量更新帖子收藏数失败: {e}", exc_info=True)
            await self.session.rollback()
            raise

    async def get_all_indexed_channel_ids(self) -> Sequence[int]:
        """从数据库获取所有已索引的频道ID"""
        statement = select(Thread.channel_id).distinct()
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_thread_count_by_channels(
        self, channel_ids: List[int]
    ) -> List[ChannelThreadCount]:
        """批量获取指定频道列表中的有效帖子总数"""
        if not channel_ids:
            return []

        # 使用 cast 将 SQLModel 字段转换为 ColumnElement，以便在聚合查询中使用
        channel_id_column = cast(ColumnElement, Thread.channel_id)
        id_column = cast(ColumnElement, Thread.id)
        not_found_count_column = cast(ColumnElement, Thread.not_found_count)

        # 构建聚合查询：统计每个频道中 not_found_count 为 0 的有效帖子数
        statement = (
            select(channel_id_column, func.count(id_column))
            .where(channel_id_column.in_(channel_ids))
            .where(not_found_count_column == 0)
            .group_by(channel_id_column)
        )
        result = await self.session.execute(statement)
        rows = result.all()

        # 将查询结果转换为 DTO 列表
        return [
            ChannelThreadCount(channel_id=int(row[0]), thread_count=int(row[1]))
            for row in rows
        ]

    async def get_total_thread_count_for_scope(
        self, guild_id: Optional[int], channel_ids: Optional[List[int]]
    ) -> int:
        """获取指定范围内的有效帖子去重总数"""
        thread_id_column = cast(ColumnElement, Thread.id)
        not_found_count_column = cast(ColumnElement, Thread.not_found_count)
        guild_id_column = cast(ColumnElement, Thread.guild_id)
        channel_id_column = cast(ColumnElement, Thread.channel_id)

        statement = select(func.count(thread_id_column.distinct())).where(
            not_found_count_column == 0
        )

        if guild_id is not None:
            statement = statement.where(guild_id_column == guild_id)
        if channel_ids:
            statement = statement.where(channel_id_column.in_(channel_ids))

        result = await self.session.execute(statement)
        return result.scalar_one_or_none() or 0

    async def get_random_threads(
        self,
        limit: int,
        channel_ids: Optional[List[int]] = None,
        exclude_channel_ids: Optional[List[int]] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        tag_logic: str = "and",
    ) -> List[Thread]:
        """随机获取满足条件的帖子"""
        # 构建基础查询条件排除软删除帖子
        stmt = select(Thread).where(Thread.not_found_count == 0)

        # 只搜索 show_flag == True 的帖子，避免显示被隐藏的帖子
        stmt = stmt.where(Thread.show_flag)

        # 频道筛选
        if channel_ids:
            stmt = stmt.where(cast(ColumnElement, Thread.channel_id).in_(channel_ids))

        # 必须排除的频道筛选
        if exclude_channel_ids:
            stmt = stmt.where(
                ~cast(ColumnElement, Thread.channel_id).in_(exclude_channel_ids)
            )

        # 包含的标签筛选
        if include_tags:
            if tag_logic == "or":
                stmt = stmt.where(Thread.tags.any(Tag.name.in_(include_tags)))  # type: ignore
            else:
                for tag_name in include_tags:
                    stmt = stmt.where(Thread.tags.any(Tag.name == tag_name))  # type: ignore

        # 必须排除的标签
        if exclude_tags:
            stmt = stmt.where(~Thread.tags.any(Tag.name.in_(exclude_tags)))  # type: ignore

        # 使用数据库随机函数排序并限制返回数量
        stmt = stmt.order_by(func.random()).limit(limit)

        # 预加载标签和作者关联数据避免懒加载报错
        stmt = stmt.options(
            selectinload(Thread.tags),  # type: ignore
            joinedload(Thread.author),  # type: ignore
        )

        # 执行查询
        result = await self.session.execute(stmt)

        # 返回结果列表
        return list(result.scalars().all())

    async def update_thread_visibility(self, thread_id: int, show_flag: bool) -> bool:
        """更新帖子的搜索可见性状态"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(show_flag=show_flag)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0

    async def update_thread_thumbnail_urls(
        self, thread_id: int, thumbnail_urls: List[str]
    ) -> bool:
        """更新帖子的缩略图 URL 列表"""
        stmt = (
            update(Thread)
            .where(Thread.thread_id == thread_id)  # type: ignore
            .values(thumbnail_urls=thumbnail_urls)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return bool(result.rowcount)

    async def get_thread_visibility(self, thread_id: int) -> Optional[bool]:
        """获取帖子的可见性状态"""
        stmt = select(Thread.show_flag).where(Thread.thread_id == thread_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_fts_matched_thread_ids(
        self,
        keywords: str | None,
        exclude_keywords: str | None,
        exemption_markers: list[str] | None = None,
        redis_client=None,
    ) -> "FTSResultDTO":
        """
        处理 FTS 关键词搜索，返回包含子查询对象的 DTO。

        使用 PostgreSQL 全文搜索（search_vector tsvector + @@ tsquery），
        Python 端通过 rjieba 分词后构建 tsquery 表达式，
        但不再执行查询将 ID 拉到 Python——改为返回 select() 子查询对象，
        由调用方嵌入 Thread.id.in_(stmt) / Thread.id.not_in(stmt)，
        让 PostgreSQL 内部完成过滤。
        注意：子查询中使用的是内部主键 `thread.id` 而不是 Discord 的 `thread_id`

        通过 redis_client 缓存分词后的 tsquery 字符串（TTL 1h），
        避免重复 jieba 分词开销。
        """

        # ── 尝试从 Redis 缓存读取已构建的 tsquery 字符串 ──
        cache_key = None
        if redis_client and (keywords or exclude_keywords):
            raw = (
                (keywords or "")
                + "|"
                + (exclude_keywords or "")
                + "|"
                + ",".join(exemption_markers or [])
            )
            prefix = (keywords or "none")[:5]
            cache_key = CacheKeys.FTS_TSQUERY_RESULT.format(
                prefix=prefix,
                hash=hashlib.md5(raw.encode()).hexdigest(),
            )
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    include_stmts: list = []
                    for tsq in data.get("ig", []):
                        stmt = select(Thread.id).where(
                            Thread.search_vector.op("@@")(
                                literal_column(f"$${tsq}$$::tsquery")
                            )
                        )
                        include_stmts.append(stmt)
                    exclude_stmt = None
                    if data.get("eg"):
                        exclude_stmt = select(Thread.id).where(
                            Thread.search_vector.op("@@")(
                                literal_column(f"$${data['eg']}$$::tsquery")
                            )
                        )
                    return FTSResultDTO(
                        include_stmts=include_stmts,
                        exclude_stmt=exclude_stmt,
                        has_include=bool(include_stmts),
                        has_exclude=exclude_stmt is not None,
                    )
            except Exception:
                pass  # 缓存失败不影响搜索，继续走正常流程

        loop = asyncio.get_running_loop()
        exclude_stmt = None
        include_stmts: list = []

        # 收集构建的 tsquery 字符串，用于回填缓存
        cached_include_tsqueries: list[str] = []
        cached_exclude_tsquery: str | None = None

        # ============ 反选关键词：构建排除子查询 ============
        if exclude_keywords:
            markers = (
                exemption_markers if exemption_markers is not None else ["禁", "🈲"]
            )

            exclude_keywords_list = [
                kw.strip()
                for kw in re.split(r"[,，/\\\s]+", exclude_keywords)
                if kw.strip()
            ]

            all_exclude_tsqueries: list[str] = []
            try:
                all_raw_tokens = await asyncio.wait_for(
                    loop.run_in_executor(None, _batch_cut, exclude_keywords_list),
                    timeout=SearchTimeout.FTS_TOKENIZE.value,
                )
            except asyncio.TimeoutError:
                logger.warning("jieba 分词超时（排除关键词）")
                all_raw_tokens = [[] for _ in exclude_keywords_list]

            for keyword, raw_tokens in zip(exclude_keywords_list, all_raw_tokens):
                tokens = [t.strip() for t in raw_tokens if t.strip()]
                if not tokens:
                    continue

                # 构建 PostgreSQL tsquery：tokens 用 & 连接，最后一个带 :* 前缀匹配
                tsquery_and = _tokens_to_tsquery_and(tokens)

                if markers:
                    # 构建豁免子句：token <4> marker（双向）
                    exemption_tsq = _build_exemption_tsquery(tokens[0], markers)
                    all_exclude_tsqueries.append(
                        f"({tsquery_and}) &! ({exemption_tsq})"
                    )
                else:
                    all_exclude_tsqueries.append(f"({tsquery_and})")

            # 所有排除词用 | 连接（命中任一即排除）
            if all_exclude_tsqueries:
                final_exclude_tsquery = " | ".join(all_exclude_tsqueries)
                cached_exclude_tsquery = final_exclude_tsquery
                exclude_stmt = select(Thread.id).where(
                    Thread.search_vector.op("@@")(
                        literal_column(f"$${final_exclude_tsquery}$$::tsquery")
                    )
                )

        # ============ 正选关键词：每个 AND 组构建一个子查询 ============
        has_any_include = False
        if keywords:
            # 按逗号拆分为多个 AND 组，各关键词组之间取交集
            keywords_str = keywords.replace("，", ",").replace("／", "/").replace("\\", "/")
            and_groups = [
                group.strip() for group in keywords_str.split(",") if group.strip()
            ]

            # 收集所有需要分词的普通关键词，一次批量提交到线程池
            _jieba_inputs = []
            for group in and_groups:
                for kw in group.split("/"):
                    kw = kw.strip()
                    if not kw:
                        continue
                    if not (kw.startswith('"') and kw.endswith('"') and len(kw) > 2):
                        _jieba_inputs.append(kw)

            _token_map = {}
            if _jieba_inputs:
                try:
                    _all_raw = await asyncio.wait_for(
                        loop.run_in_executor(None, _batch_cut, _jieba_inputs),
                        timeout=SearchTimeout.FTS_TOKENIZE.value,
                    )
                except asyncio.TimeoutError:
                    logger.warning("jieba 分词超时（正选关键词）")
                    _all_raw = [[] for _ in _jieba_inputs]

                for kw, raw_tokens in zip(_jieba_inputs, _all_raw):
                    tokens = [t.strip() for t in raw_tokens if t.strip()]
                    if tokens:
                        _token_map[kw] = tokens

            for group in and_groups:
                or_tsquery_parts = []
                for kw in group.split("/"):
                    kw = kw.strip()
                    if not kw:
                        continue

                    # 精确匹配语法：双引号包裹的关键词跳过 jieba 分词，所有 token 需同时存在
                    if kw.startswith('"') and kw.endswith('"') and len(kw) > 2:
                        exact_kw = kw[1:-1].strip().replace('"', "")
                        if exact_kw:
                            exact_tokens = list(rjieba.cut(exact_kw))
                            clean_tokens = [t.strip().lower() for t in exact_tokens if t.strip()]
                            if clean_tokens:
                                phrase = " & ".join(f"'{t}'" for t in clean_tokens)
                                or_tsquery_parts.append(f"({phrase})")
                    else:
                        tokens = _token_map.get(kw)
                        if tokens:
                            tsq = _tokens_to_tsquery_and(tokens)
                            or_tsquery_parts.append(f"({tsq})")

                if or_tsquery_parts:
                    group_tsquery = " | ".join(or_tsquery_parts)
                    cached_include_tsqueries.append(group_tsquery)
                    stmt = select(Thread.id).where(
                        Thread.search_vector.op("@@")(
                            literal_column(f"$${group_tsquery}$$::tsquery")
                        )
                    )
                    include_stmts.append(stmt)
                    has_any_include = True

        # ── 回填 Redis 缓存 ──
        if cache_key and redis_client:
            try:
                cache_data: dict = {"ig": cached_include_tsqueries}
                if cached_exclude_tsquery:
                    cache_data["eg"] = cached_exclude_tsquery
                await redis_client.setex(
                    cache_key, 3600, json.dumps(cache_data, ensure_ascii=False)
                )
            except Exception:
                pass

        return FTSResultDTO(
            include_stmts=include_stmts,
            exclude_stmt=exclude_stmt,
            has_include=has_any_include,
            has_exclude=exclude_stmt is not None,
        )
