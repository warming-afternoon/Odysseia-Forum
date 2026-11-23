"""平台作品业务事务及搜索筛选。"""

import json
import os
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select

from models.tag import Tag
from models.thread import Thread
from models.work import Work
from models.work_statistics import WorkStatistics
from platform_bus.outbox import enqueue_work
from platform_gateway.http import SORTS, cursor_offset, invalid, page_result, timestamp
from platform_gateway.policy import (
    RATINGS,
    rating_expression,
    tagged,
    valid_bigint,
    visible_conditions,
)
from platform_gateway.work_repository import WorkRepository, work_query
from platform_gateway.policy import RATING_IDS
from platform_gateway.profile_client import ensure_local_user
from shared.text_utils import build_search_vector_text
from shared.time_utils import utc_now
from work.work_growth_scores import growth_scores


class WorkApiService:
    """在平台 HTTP 与现有作品表之间协调读写。"""

    def __init__(self, session, principal):
        """共享请求会话和身份快照。"""
        self.session = session
        self.principal = principal
        self.repository = WorkRepository(session)

    async def visible(self, work_id, *, lock=False, own_draft=False, consumer=None):
        """按作品可见性过滤，写入路径加锁以序列化状态变化。"""
        if not valid_bigint(work_id):
            raise HTTPException(404, "资源不存在")
        statement = work_query().where(
            Work.id == int(work_id),
            *visible_conditions(self.principal, consumer=consumer, own_draft=own_draft),
        )
        if lock:
            # 等待行锁后以新快照读取作品，防止并发发布重复出箱。
            await self.session.execute(
                select(Work.id).where(Work.id == int(work_id)).with_for_update()
            )
        row = (
            await self.session.execute(
                statement.execution_options(populate_existing=lock)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "资源不存在")
        return row

    async def create(self, body):
        """创建独立草稿，发布前不会触发搜索或聊天同步。"""
        user_id = self.principal.require_user()
        await ensure_local_user(self.session, user_id)
        work = Work(
            title=body.title,
            author_id=user_id,
            source_platform="platform",
            source_visible=False,
            description="",
            summary=body.summary or None,
            rating="general",
            updated_at=utc_now(),
        )
        self.session.add(work)
        await self.session.flush()
        result = (await self.repository.project([work], self.principal))[0]
        await self.session.commit()
        return result

    async def patch(self, work_id, body):
        """更新平台作品的作者元信息，并对已发布作品持久化通知。"""
        user_id = self.principal.require_user()
        work = await self.visible(work_id, lock=True, own_draft=True)
        if work.author_id != user_id and not self.principal.may_moderate(
            work.author_id, {"works.manage_any"}
        ):
            raise HTTPException(403, "需要作者或站务权限")
        if work.source_platform != "platform":
            raise HTTPException(403, "来源作品由原平台维护")
        patch = body.model_dump(exclude_unset=True)
        for key in ("coverId", "galleryIds"):
            if patch.pop(key, None):
                raise invalid(key, "当前没有可用的平台上传附件")
        if (
            "layout" in patch
            and len(json.dumps(patch["layout"], ensure_ascii=False)) > 20000
        ):
            raise invalid("layout", "布局最多 20000 个字符")
        if "title" in patch and work.published_at and not patch["title"]:
            raise invalid("title", "请填写作品标题")
        if "summary" in patch:
            patch["summary"] = patch["summary"] or None
        for key, value in patch.items():
            setattr(work, key, value)
        if patch:
            work.updated_at = utc_now()
            if work.published_at:
                await enqueue_work(self.session, work, "work.updated", patch.keys())
        await self.session.flush()
        result = (await self.repository.project([work], self.principal))[0]
        await self.session.commit()
        return result

    async def publish(self, work_id, body):
        """验证发布承诺并在同一事务写入发布状态与同步任务。"""
        user_id = self.principal.require_user()
        work = await self.visible(work_id, lock=True, own_draft=True)
        if work.author_id != user_id:
            raise HTTPException(403, "只有作者能发布")
        if work.source_platform != "platform" or work.published_at:
            raise HTTPException(409, "作品已发布")
        self.principal.require_publication()
        if not work.title.strip():
            raise invalid("title", "请填写作品标题")
        if body.acknowledgedTermsVersion != os.getenv("TERMS_VERSION", "2026-10"):
            raise invalid(
                "acknowledgedTermsVersion", "发布规则已更新，请重新阅读并确认"
            )
        questions = {item.question for item in body.answers}
        if len(questions) != len(body.answers):
            raise invalid("answers", "同一个问题不能回答多次")
        if questions - {"jailbreak"}:
            raise invalid("answers", "未知发布问题")
        restricted = any(
            item.question == "jailbreak" and item.answer for item in body.answers
        )
        if restricted and self.principal.mode != "r18":
            raise invalid("answers", "发布受限内容需要 R18 模式")
        work.is_abyss = restricted
        now = utc_now()
        work.published_at = now
        work.updated_at = now
        if work.declarations is None:
            work.declarations = {
                "acknowledgedTermsVersion": body.acknowledgedTermsVersion,
                "at": timestamp(now),
                "userId": user_id,
                "answers": [
                    {**item.model_dump(), "versionId": None, "at": timestamp(now)}
                    for item in body.answers
                ],
            }
        work.source_visible = True
        await enqueue_work(self.session, work, "work.updated", ["status"])
        result = (await self.repository.project([work], self.principal))[0]
        await self.session.commit()
        return result

    async def delete(self, work_id, body):
        """软删除作品；管理删除必须提供原因及有效权限上限。"""
        user_id = self.principal.require_user()
        work = await self.visible(work_id, lock=True, own_draft=True)
        if work.author_id != user_id:
            if not self.principal.may_moderate(
                work.author_id, {"works.manage_any", "moderation.content"}
            ):
                raise HTTPException(403, "只能处置排在自己之下的人")
            if not body.reason:
                raise invalid("reason", "管理删除必须提供原因")
        if work.source_platform != "platform":
            raise HTTPException(403, "来源作品由原平台维护")
        work.deleted_at = utc_now()
        work.deletion_reason = body.reason or "self"
        work.deleted_user_id = user_id
        work.updated_at = work.deleted_at
        if work.author_id != user_id:
            from platform_gateway.work_trash_service import audit_work

            await audit_work(self.session, work, user_id, "work.delete", body.reason)
        if work.published_at:
            await enqueue_work(
                self.session,
                work,
                "work.deleted",
                deletion_source="self" if user_id == work.author_id else "admin",
            )
        await self.session.commit()

    async def update_chat(self, work_id, body):
        """保存交流区单独的标题并使聊天收到当前策略。"""
        user_id = self.principal.require_user()
        work = await self.visible(work_id, lock=True, own_draft=True)
        if work.author_id != user_id and not self.principal.may_moderate(
            work.author_id, {"works.manage_any"}
        ):
            raise HTTPException(403, "需要作者或站务权限")
        if work.source_platform != "platform":
            raise HTTPException(403, "来源作品由原平台维护")
        work.chat_title = body.title or None
        work.updated_at = utc_now()
        if work.published_at:
            await enqueue_work(self.session, work, "work.updated", ["chatTitle"])
        result = {
            "title": work.chat_title,
            "displayTitle": work.chat_title or work.title,
        }
        await self.session.commit()
        return result

    async def list(self, query, *, search=False):
        """在统一可见作品集合中筛选、排序和分页。"""
        conditions = visible_conditions(
            self.principal, consumer="search" if search else None
        )
        statement = work_query().outerjoin(
            WorkStatistics, WorkStatistics.work_id == Work.id
        )
        updated = func.coalesce(Work.updated_at, Work.last_active_at, Work.created_at)
        likes = func.coalesce(WorkStatistics.platform_like_count, 0) + case(
            (
                Work.source_platform == "discord",
                func.coalesce(WorkStatistics.reaction_count, 0),
            ),
            else_=0,
        )
        q = query.get("q", "").strip()
        score = None
        if q:
            if len(q) > 200:
                raise invalid("q", "搜索词最多 200 个字符")
            words = build_search_vector_text(q, None)
            match = or_(
                Work.title.icontains(q, autoescape=True),
                Work.description.icontains(q, autoescape=True),
                Work.summary.icontains(q, autoescape=True),
            )
            if words:
                tsquery = func.plainto_tsquery("simple", words)
                match = or_(match, Work.search_vector.bool_op("@@")(tsquery))
                score = func.ts_rank_cd(Work.search_vector, tsquery)
            conditions.append(match)
        for name, negate in (("tags", False), ("excludeTags", True)):
            filters = []
            for value in csv_values(query.get(name)):
                if value in RATING_IDS:
                    filters.append(rating_expression() == RATING_IDS[value])
                elif valid_bigint(value):
                    filters.append(tagged(Tag.id == int(value)))
                else:
                    raise invalid(name)
            if filters:
                logic = query.get("tagLogic", "and")
                if logic not in ("and", "or"):
                    raise invalid("tagLogic")
                conditions.append(
                    ~or_(*filters)
                    if negate
                    else (or_(*filters) if logic == "or" else and_(*filters))
                )
        ratings = csv_values(query.get("rating"))
        if ratings:
            if set(ratings) - set(RATINGS):
                raise invalid("rating")
            conditions.append(rating_expression().in_(ratings))
        if query.get("type"):
            conditions.append(tagged(Tag.name == query["type"]))
        if query.get("origin"):
            if query["origin"] != "discord":
                raise invalid("origin")
            conditions.append(Work.source_platform == "discord")
        for name, negate in (("authors", False), ("excludeAuthors", True)):
            values = csv_values(query.get(name))
            if values:
                match = Work.author_id.in_(values)
                conditions.append(
                    or_(Work.author_id.is_(None), ~match) if negate else match
                )
        for word in csv_values(query.get("excludeKeywords")):
            conditions.append(
                ~or_(
                    Work.title.icontains(word, autoescape=True),
                    func.coalesce(Work.description, "").icontains(
                        word, autoescape=True
                    ),
                )
            )
        for name, expression, after in (
            ("createdAfter", Work.created_at, True),
            ("createdBefore", Work.created_at, False),
            ("updatedAfter", updated, True),
            ("updatedBefore", updated, False),
        ):
            if query.get(name):
                try:
                    value = datetime.fromisoformat(query[name])
                    value = (
                        value.astimezone(UTC).replace(tzinfo=None)
                        if value.tzinfo
                        else value
                    )
                except ValueError as exc:
                    raise invalid(name) from exc
                conditions.append(expression >= value if after else expression <= value)
        if query.get("minFavorites") is not None:
            minimum = integer_arg(query["minFavorites"], "minFavorites", minimum=0)
            conditions.append(likes >= minimum)
        sort = query.get("sort", "relevance" if q else "hot_week")
        if sort not in SORTS:
            raise invalid("sort")
        if sort in {"rising", "rookie"}:
            raise HTTPException(501, "尚未接入此榜单排序")
        window = {"hot_day": 1, "hot_week": 7, "hot_month": 30}
        published = func.coalesce(Work.published_at, Work.created_at)
        now = utc_now()
        recent_score = None
        if sort in window or sort == "dc_reactions" or (sort == "new" and not search):
            scores = growth_scores("reaction", now.date(), window.get(sort, 7))
            statement = statement.outerjoin(scores, scores.c.work_id == Work.id)
            recent_score = func.coalesce(scores.c.score, 0)
        secondary = (published.desc(),)
        if sort == "dc_reactions":
            historical = (
                select(Thread.reaction_count)
                .where(
                    Thread.work_id == Work.id,
                    Thread.role == "main",
                    Thread.platform == "discord",
                )
                .correlate(Work)
                .scalar_subquery()
            )
            primary = case(
                (Work.source_platform == "discord", func.coalesce(historical, 0)),
                else_=None,
            )
            secondary = (recent_score.desc(), published.desc())
        elif sort in window:
            primary = recent_score
        elif sort == "relevance" and score is not None:
            primary = score
        elif sort == "favorites":
            primary = likes
        elif sort == "hall":
            conditions.append(published <= now - timedelta(days=90))
            primary = likes
        elif sort == "latest":
            primary = updated
        elif sort == "new" and not search:
            conditions.append(published >= now - timedelta(days=14))
            primary = recent_score
        else:
            primary = published
        offset = cursor_offset(query.get("cursor"))
        limit = min(integer_arg(query.get("limit", "20"), "limit", minimum=1), 100)
        statement = (
            statement.where(*conditions)
            .order_by(
                primary.desc().nullslast(),
                *secondary,
                Work.id.desc(),
            )
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.session.scalars(statement)).all()
        projected = await self.repository.project(rows[:limit], self.principal)
        if len(rows) > limit:
            projected.append(None)
        return page_result(projected, offset, limit)


def csv_values(value):
    """按协议解析有限数量的逗号分隔值。"""
    if not value:
        return []
    values = list(
        dict.fromkeys(part.strip() for part in value.split(",") if part.strip())
    )
    if len(values) > 100 or any(len(item) > 200 for item in values):
        raise invalid("query", "筛选条件过多或过长")
    return values


def integer_arg(value, field, *, minimum):
    """校验整数查询参数并限制数据库整数范围。"""
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise invalid(field) from exc
    if not minimum <= number < 2**31:
        raise invalid(field)
    return number
