"""平台书单的权限、聚合和条目事务。"""

from collections import defaultdict

from sqlalchemy import and_, func, or_, select, text
from fastapi import HTTPException

from core.tag_binding_repository import TagBindingRepository
from core.tag_query import tag_filters
from models.booklist import Booklist
from models.booklist_item import BooklistItem
from models.tag import Tag
from models.tag_binding import TagBinding
from models.user import User
from models.work import Work
from platform_gateway.http import cursor_offset, invalid, page_result, timestamp
from platform_gateway.profile_client import core_profiles, ensure_local_user
from platform_gateway.policy import (
    RATING_IDS,
    valid_bigint,
    visible_booklist_conditions,
    visible_conditions,
)
from platform_gateway.work_api_service import WorkApiService, csv_values, integer_arg
from platform_gateway.work_repository import (
    public_user,
    rating_tag,
    tag_ref,
)
from shared.time_utils import utc_now


SORT_TO_FORUM = {
    "order": "display_order",
    "added": "join_time",
    "updated": "last_active_at",
}
SORT_FROM_FORUM = {value: key for key, value in SORT_TO_FORUM.items()}


class CollectionApiService:
    """与已有 Forum 书单共用数据，通过平台账号执行所有权检查。"""

    def __init__(self, session, principal):
        """使用请求会话和已验证的平台身份。"""
        self.session = session
        self.principal = principal

    def _visible_items(self):
        """构造完整的可见条目关系供详情计数、广场和条目分页使用。"""
        return (
            select(BooklistItem, Work)
            .join(Work, Work.id == BooklistItem.work_id)
            .where(*visible_conditions(self.principal, consumer="collections"))
        )

    def _read(self):
        """先按分级、控制状态和所有权限制书单集合，再做分页。"""
        statement = (
            select(Booklist, User)
            .outerjoin(
                User,
                and_(
                    User.id == Booklist.fluxer_owner_id, User.merged_into_id.is_(None)
                ),
            )
            .where(*visible_booklist_conditions(self.principal))
        )
        return statement

    async def _get(self, collection_id, *, edit=False):
        """私密书单对非所有者隐藏；更新时锁定书单避免条目竞态。"""
        if not valid_bigint(collection_id) or int(collection_id) >= 2**31:
            raise HTTPException(404, "资源不存在")
        statement = self._read().where(Booklist.id == int(collection_id))
        if edit:
            actor = self.principal.require_user()
            # 默认书单更新触发器也使用相同的所有者锁。
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "default-booklist:" + actor},
            )
            await self.session.execute(
                select(Booklist.id)
                .where(Booklist.id == int(collection_id))
                .with_for_update()
            )
        row = (
            await self.session.execute(
                statement.execution_options(populate_existing=edit)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(404, "资源不存在")
        if edit and row[0].fluxer_owner_id != self.principal.userId:
            raise HTTPException(403, "只有书单所有者能修改")
        return row

    async def _project(self, rows, *, contains_work=None):
        """对可见书单批量计算可见条目计数和标签。"""
        if not rows:
            return []
        ids = [booklist.id for booklist, _ in rows]
        hits = self._visible_items().where(BooklistItem.booklist_id.in_(ids)).subquery()
        query = select(hits.c.booklist_id, func.count()).group_by(hits.c.booklist_id)
        counts = dict((await self.session.execute(query)).tuples().all())
        # 按书单批量读取生效标签，并保留平台原有的标签 ID 排序。
        tag_query = (
            select(TagBinding.target_id, Tag)
            .join(Tag, Tag.id == TagBinding.tag_id)
            .where(
                TagBinding.target_type == "booklist",
                TagBinding.target_id.in_(ids),
                TagBinding.ended_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(Tag.id)
        )
        if self.principal.mode != "r18":
            tag_query = tag_query.where(Tag.is_abyss.is_(False))
        tags = defaultdict(list)
        for booklist_id, tag in await self.session.execute(tag_query):
            tags[booklist_id].append(
                tag_ref(
                    {"id": str(tag.id), "name": tag.name}, tag.category, tag.is_abyss
                )
            )
        contains = set()
        if contains_work and valid_bigint(contains_work):
            contains = set(
                (
                    await self.session.scalars(
                        select(hits.c.booklist_id).where(
                            hits.c.work_id == int(contains_work)
                        )
                    )
                ).all()
            )
        profiles = await core_profiles(
            booklist.fluxer_owner_id
            for booklist, _ in rows
            if not booklist.is_anonymous
            or booklist.fluxer_owner_id == self.principal.userId
        )
        result = []
        for booklist, user in rows:
            own = (
                self.principal.userId is not None
                and booklist.fluxer_owner_id == self.principal.userId
            )
            collection = {
                "id": str(booklist.id),
                "owner": None
                if booklist.is_anonymous and not own
                else {
                    **public_user(booklist.fluxer_owner_id, user, booklist.created_at),
                    **profiles.get(booklist.fluxer_owner_id, {}),
                },
                "title": booklist.title,
                "description": booklist.description or "",
                "cover": None,
                "tags": [rating_tag(booklist.rating), *tags[booklist.id]],
                "isPublic": booklist.is_public,
                "isAnonymous": booklist.is_anonymous,
                "isDefault": booklist.is_default,
                "defaultSort": SORT_FROM_FORUM.get(
                    booklist.default_sort_method, "order"
                ),
                "itemCount": counts.get(booklist.id, 0),
                "favoriteCount": booklist.collection_count,
                "createdAt": timestamp(booklist.created_at),
                "updatedAt": timestamp(booklist.updated_at),
                "viewer": {"favorited": False, "canEdit": own},
            }
            if contains_work is not None:
                collection["containsWork"] = booklist.id in contains
            result.append(collection)
        return result

    async def list(self, query, *, own=False):
        """先过滤列表的隐私和当前可见条目，再按公开或本人分页。"""
        statement = self._read()
        if own:
            statement = statement.where(
                Booklist.fluxer_owner_id == self.principal.require_user()
            )
        else:
            statement = statement.where(Booklist.is_public.is_(True))
            if self.principal.mode == "general":
                visible_items = (
                    self._visible_items()
                    .with_only_columns(BooklistItem.id)
                    .where(BooklistItem.booklist_id == Booklist.id)
                )
                statement = statement.where(visible_items.exists())
        if query.get("q"):
            keyword = query["q"]
            if len(keyword) > 200:
                raise invalid("q")
            statement = statement.where(
                or_(
                    Booklist.title.icontains(keyword, autoescape=True),
                    Booklist.description.icontains(keyword, autoescape=True),
                )
            )
        for value in csv_values(query.get("tags")):
            if value in RATING_IDS:
                statement = statement.where(Booklist.rating == RATING_IDS[value])
            elif valid_bigint(value):
                statement = statement.where(
                    *tag_filters("booklist", Booklist.id, [value], None)
                )
            else:
                raise invalid("tags")
        sort = query.get("sort", "new")
        if sort not in ("new", "popular"):
            raise invalid("sort")
        if sort == "popular":
            statement = statement.order_by(Booklist.collection_count.desc())
        offset = cursor_offset(query.get("cursor"))
        limit = min(integer_arg(query.get("limit", "20"), "limit", minimum=1), 100)
        statement = (
            statement.order_by(Booklist.created_at.desc(), Booklist.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.session.execute(statement)).all()
        return page_result(
            await self._project(
                rows, contains_work=query.get("containsWork") if own else None
            ),
            offset,
            limit,
        )

    async def detail(self, collection_id):
        """仅返回当前可访问书单的详情。"""
        row = await self._get(collection_id)
        return (await self._project([row]))[0]

    async def save(self, body, collection_id=None):
        """创建或修改书单，并保持与已有排序和所有者字段兼容。"""
        user_id = self.principal.require_user()
        if "tags" in body.model_fields_set:
            # 与标签治理端使用相同的锁顺序，避免写入已删除的标签。
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock_shared(73902141)")
            )
        if collection_id is None:
            booklist = Booklist(
                title=body.title,
                fluxer_owner_id=user_id,
                owner_id=None,
                is_public=False,
            )
            self.session.add(booklist)
            await self.session.flush()
            profile = await ensure_local_user(self.session, user_id)
        else:
            booklist, profile = await self._get(collection_id, edit=True)
        if not body.title and (
            collection_id is None or "title" in body.model_fields_set
        ):
            raise invalid("title", "请填写书单标题")
        if body.coverUploadId:
            raise invalid("coverUploadId", "当前没有可用的平台上传附件")
        if "tags" in body.model_fields_set:
            ratings = [RATING_IDS[tag] for tag in body.tags if tag in RATING_IDS]
            ids = [tag for tag in body.tags if tag not in RATING_IDS]
            if (
                len(ratings) > 1
                or len(ids) > 8
                or not all(valid_bigint(tag) for tag in ids)
            ):
                raise invalid("tags")
            ratings = ratings or ["general"]
            if ratings[0] != "general" and self.principal.mode != "r18":
                raise invalid("tags", "受限书单需要 R18 模式")
            if ratings[0] == "r18g" and not self.principal.showR18G:
                raise invalid("tags", "R18G 书单需要开启 R18G 显示")
            tag_ids = {int(tag) for tag in ids}
            if tag_ids:
                tags = (
                    await self.session.scalars(
                        select(Tag).where(
                            Tag.id.in_(tag_ids),
                            Tag.deleted_at.is_(None),
                            Tag.enabled.is_(True),
                        )
                    )
                ).all()
                if len(tags) != len(tag_ids) or any(
                    tag.is_abyss and ratings[0] == "general" for tag in tags
                ):
                    raise invalid("tags", "标签不可用或需要受限分级")
            booklist.rating = ratings[0]
            await TagBindingRepository(self.session).replace_booklist_tags(
                booklist.id, tag_ids, user_id
            )
        for wire, field in (
            ("title", "title"),
            ("description", "description"),
            ("isPublic", "is_public"),
            ("isAnonymous", "is_anonymous"),
        ):
            if wire in body.model_fields_set:
                setattr(booklist, field, getattr(body, wire))
        if "defaultSort" in body.model_fields_set:
            booklist.default_sort_method = SORT_TO_FORUM[body.defaultSort]
        booklist.updated_at = utc_now()
        await self.session.flush()
        result = (await self._project([(booklist, profile)]))[0]
        await self.session.commit()
        return result

    async def remove(self, collection_id, body):
        """软删除书单但保留关联；私密书单仅由本人管理。"""
        user_id = self.principal.require_user()
        booklist, _ = await self._get(collection_id, edit=True)
        if booklist.is_default:
            raise HTTPException(403, "不能删除默认书单")
        booklist.deleted_at = utc_now()
        booklist.deleted_user_id = user_id
        booklist.deletion_reason = body.reason or None
        await self.session.commit()

    async def items(self, collection_id, query):
        """按可见作品筛选整页书单条目，始终保持有效排序。"""
        booklist, _ = await self._get(collection_id)
        sort = query.get(
            "sort", SORT_FROM_FORUM.get(booklist.default_sort_method, "order")
        )
        sorts = {
            "order": BooklistItem.display_order.asc(),
            "added": BooklistItem.created_at.desc(),
            "updated": func.coalesce(
                Work.updated_at, Work.last_active_at, Work.created_at
            ).desc(),
        }
        if sort not in sorts:
            raise invalid("sort")
        offset = cursor_offset(query.get("cursor"))
        limit = min(integer_arg(query.get("limit", "20"), "limit", minimum=1), 100)
        statement = (
            self._visible_items()
            .where(BooklistItem.booklist_id == booklist.id)
            .order_by(
                sorts[sort],
                BooklistItem.id.desc(),
            )
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.session.execute(statement)).all()
        return page_result(await self._project_items(rows), offset, limit)

    async def _project_items(self, rows):
        """复用作品序列化，将条目属性与作品合并。"""
        works = await WorkApiService(self.session, self.principal).repository.project(
            [work for _, work in rows], self.principal
        )
        return [
            {
                "work": work,
                "note": item.comment or "",
                "order": item.display_order,
                "addedAt": timestamp(item.created_at),
            }
            for (item, _), work in zip(rows, works, strict=True)
        ]

    async def change_item(self, collection_id, work_id, operation, body=None):
        """在相同书单锁内更新条目与统计，重复添加保持幂等。"""
        # 保持与旧入口及收藏统计触发器一致的书单、作品加锁顺序。
        booklist, _ = await self._get(collection_id, edit=True)
        if not valid_bigint(work_id):
            raise HTTPException(404, "资源不存在")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(work_id)}
        )
        work = None
        if operation != "remove":
            work = await WorkApiService(self.session, self.principal).visible(
                work_id, lock=True, consumer="collections"
            )
        query = select(BooklistItem).where(
            BooklistItem.booklist_id == booklist.id,
            BooklistItem.work_id == int(work_id),
        )
        item = (await self.session.execute(query)).scalar_one_or_none()
        if operation == "remove":
            if item is not None:
                await self.session.delete(item)
                booklist.item_count = max(0, booklist.item_count - 1)
            await self.session.commit()
            return None
        if item is None:
            if operation == "edit":
                raise HTTPException(404, "资源不存在")
            item = BooklistItem(
                booklist_id=booklist.id,
                work_id=work.id,
                fluxer_owner_id=booklist.fluxer_owner_id,
                owner_id=booklist.owner_id,
            )
            self.session.add(item)
            booklist.item_count += 1
        if "note" in body.model_fields_set:
            item.comment = body.note
        if "order" in body.model_fields_set:
            item.display_order = body.order
        await self.session.flush()
        result = (await self._project_items([(item, work)]))[0]
        await self.session.commit()
        return result
