import logging
from datetime import datetime
from typing import List, Optional, Sequence, cast

from sqlalchemy import ColumnElement, case, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select

from dto.meta import ChannelThreadCount
from dto.search.fts_result_dto import FTSResultDTO
from models import Tag, TagVote, Thread, ThreadTagLink
from ThreadManager.update_data_dto import UpdateData

import asyncio
from functools import partial
import rjieba
import re
from shared.database import thread_fts_table

logger = logging.getLogger(__name__)


class ThreadRepository:
    """封装与 Thread 表相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_or_update_thread_with_tags(
        self, thread_data: dict, tags: list[Tag]
    ):
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
        # 执行语句并获取结果对象
        result = await self.session.execute(stmt)
        await self.session.commit()
        # 返回 rowcount 是否大于 0
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
                latest_update_at=datetime.now(timezone.utc),
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
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        tag_logic: str = "and",
    ) -> List[Thread]:
        """随机获取满足条件的帖子"""
        # 构建基础查询条件排除软删除帖子
        stmt = select(Thread).where(Thread.not_found_count == 0)

        # 只搜索 show_flag == True 的帖子，避免显示被隐藏的帖子
        stmt = stmt.where(Thread.show_flag == True)

        # 增加频道筛选条件
        if channel_ids:
            stmt = stmt.where(cast(ColumnElement, Thread.channel_id).in_(channel_ids))

        # 增加包含的标签筛选条件
        if include_tags:
            if tag_logic == "or":
                stmt = stmt.where(Thread.tags.any(Tag.name.in_(include_tags)))  # type: ignore
            else:
                for tag_name in include_tags:
                    stmt = stmt.where(Thread.tags.any(Tag.name == tag_name))  # type: ignore

        # 增加必须排除的标签筛选条件
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
    ) -> "FTSResultDTO":
        """
        处理 FTS 关键词搜索，返回正选的 thread.id 集合和反选的 thread.id 集合。
        注意：返回的是内部主键 `thread.id` 而不是 Discord 的 `thread_id`
        """

        loop = asyncio.get_running_loop()
        fts_exclude_ids: set[int] = set()

        # ============ 反选关键词处理：找出所有包含排除词的帖子 ID ============
        if exclude_keywords:
            # 豁免标记：当排除词附近出现这些标记时，该排除词不生效
            markers = exemption_markers if exemption_markers is not None else ["禁", "🈲"]

            # 将排除关键词字符串按逗号/顿号/斜杠/空白拆分成多个独立关键词
            exclude_keywords_list = [
                kw.strip()
                for kw in re.split(r"[,，/\s]+", exclude_keywords)
                if kw.strip()
            ]

            # 逐个关键词构建 FTS5 MATCH 表达式
            all_exclude_parts = []
            for keyword in exclude_keywords_list:
                # 使用 jieba 对排除关键词进行中文分词
                raw_tokens = await loop.run_in_executor(
                    None, partial(rjieba.cut, keyword)
                )
                # 清理 token 内部的双引号，防止破坏 FTS5 语法
                tokens = []
                for tok in raw_tokens:
                    clean_tok = tok.strip().replace('"', '')
                    if clean_tok:
                        tokens.append(clean_tok)
                if not tokens:
                    continue
    
                # 构建 FTS5 MATCH 的匹配表达式：
                # - 前面的分词用精确匹配（双引号包裹），例如 "搬运"
                # - 最后一个分词用前缀匹配（* 在双引号外面），例如 "工"*
                # - 所有分词之间用 AND 连接，表示必须同时出现
                # 例如分词 ["搬运", "工"] → '"搬运" AND "工"*'
                match_parts = [f'"{tok}"' for tok in tokens[:-1]]
                match_parts.append(f'"{tokens[-1]}"*')
                match_expr = " AND ".join(match_parts)

                if markers:
                    # 构建豁免子句：检查排除词的第一个分词是否在 4 个词范围内靠近豁免标记
                    first_token = tokens[0]
                    exemption_clauses = [
                        f'NEAR("{first_token}" "{marker}", 4)'
                        for marker in markers
                    ]
                    exemption_match_str = f"({' OR '.join(exemption_clauses)})"

                    # 最终表达式形如：("搬运" AND "工"*) NOT (NEAR("搬运" "禁", 4) OR NEAR("搬运" "🈲", 4))
                    # 含义：匹配包含"搬运"和"工*"的帖子，但排除"搬运"附近有"禁"或"🈲"的帖子
                    all_exclude_parts.append(
                        f"({match_expr}) NOT {exemption_match_str}"
                    )
                else:
                    # 没有豁免标记时，直接用匹配表达式
                    all_exclude_parts.append(f"({match_expr})")

            # 将所有排除词的 MATCH 表达式用 OR 连接
            # 含义：命中"搬运"或"转载"任意一个的帖子都要排除
            if all_exclude_parts:
                final_exclude_expr = " OR ".join(all_exclude_parts)
                # 在 FTS 虚拟表中执行 MATCH 查询，获取所有命中排除词的帖子 rowid
                from sqlmodel import select
                
                exc_result = await self.session.execute(
                    select(thread_fts_table.c.rowid).where(
                        thread_fts_table.c.thread_fts.op("MATCH")(
                            final_exclude_expr
                        )
                    )
                )
                fts_exclude_ids = set(exc_result.scalars().all())

        # ============ 正选关键词处理：找出包含搜索词的帖子 ID ============
        fts_include_ids: set[int] | None = None
        if keywords:
            # 按逗号拆分为多个 AND 组，各关键词组之间取交集
            keywords_str = keywords.replace("，", ",").replace("／", "/")
            and_groups = [
                group.strip()
                for group in keywords_str.split(",")
                if group.strip()
            ]

            for group in and_groups:
                # 按斜杠拆分同一组内的 OR 关键词
                or_keywords = []
                for kw in group.split("/"):
                    kw = kw.strip()
                    if not kw:
                        continue

                    # 支持精确匹配语法：用双引号包裹的关键词不做分词，直接精确匹配
                    # 例如 '"原神启动"' → FTS5 精确匹配 "原神启动"（不分词）
                    if kw.startswith('"') and kw.endswith('"') and len(kw) > 2:
                        # 清理用户输入的非法内嵌双引号，防止破坏 FTS5 语法
                        exact_kw = kw[1:-1].strip().replace('"', '')
                        if exact_kw:
                            or_keywords.append(f'"{exact_kw}"')
                    else:
                        # 普通关键词：用 jieba 分词后，每个分词结果加 * 前缀匹配
                        # 例如 "原神启动" 分词为 ["原神", "启动"] → "原神"* "启动"*
                        # 多个分词时用括号包裹，FTS5 隐式 AND 连接
                        # 即 "原神"* AND "启动"*（帖子必须同时包含"原神*"和"启动*"）
                        raw_tokens = await loop.run_in_executor(
                            None, partial(rjieba.cut, kw)
                        )
                        # 清理 token 内部的双引号，防止破坏 FTS5 语法
                        tokens = []
                        for tok in raw_tokens:
                            clean_tok = tok.strip().replace('"', '')
                            if clean_tok:
                                tokens.append(clean_tok)
                        if tokens:
                            expr = " ".join(f'"{t}"*' for t in tokens)
                            or_keywords.append(
                                f"({expr})" if len(tokens) > 1 else expr
                            )

                # 同一组内的 OR 关键词用 OR 连接
                # 例如 "搬运" 和 "转载" → '"搬运"* OR "转载"*'
                # 含义：命中"搬运"或"转载"任意一个即可
                if or_keywords:
                    match_str = " OR ".join(or_keywords)
                    # 执行 FTS MATCH 查询，获取当前组匹配的帖子 ID 集合
                    from sqlmodel import select
                    grp_result = await self.session.execute(
                        select(thread_fts_table.c.rowid).where(
                            thread_fts_table.c.thread_fts.op("MATCH")(match_str)
                        )
                    )
                    group_ids = set(grp_result.scalars().all())

                    # 多个 AND 组之间取交集
                    if fts_include_ids is None:
                        fts_include_ids = group_ids
                    else:
                        fts_include_ids &= group_ids

            # 如果所有 AND 组的交集为空集，说明没有帖子能同时满足所有关键词条件
            if fts_include_ids is None:
                fts_include_ids = set()

        return FTSResultDTO(include_ids=fts_include_ids, exclude_ids=fts_exclude_ids)
