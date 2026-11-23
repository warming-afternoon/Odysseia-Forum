"""平台作品互动及本人关注列表。"""

from sqlalchemy import func, select, update

from models.discord_identity import DiscordIdentity
from models.thread_follow import ThreadFollow
from models.work import Work
from models.work_announcement import WorkAnnouncement
from platform_bus.auto_follow import enqueue_auto_follow
from platform_gateway.http import cursor_offset, page_result, timestamp
from platform_gateway.policy import valid_bigint, visible_conditions
from platform_gateway.work_api_service import integer_arg
from shared.time_utils import utc_now
from work.work_interaction_repository import change_follow, change_like
from work.work_service import WorkService


class WorkInteractionService:
    """复用同一会话处理作品可见性和互动关系。"""

    def __init__(self, works):
        """通过现有作品服务校验可见性和批量投影。"""
        self.works = works
        self.session = works.session
        self.principal = works.principal

    async def follow(self, work_id, *, follow):
        """在已发布且可访问的作品上设置手动关注状态。"""
        user_id = self.principal.require_user()
        work = await self.works.visible(work_id, lock=True)
        await change_follow(self.session, work.id, user_id, follow=follow)
        await self.session.commit()

    async def like(self, work_id, *, like):
        """记录点赞变化，新增点赞与自动关注任务一起提交。"""
        user_id = self.principal.require_user()
        work = await self.works.visible(work_id, lock=True)
        changed = await change_like(self.session, work.id, user_id, like=like)
        if like and changed:
            await enqueue_auto_follow(self.session, work.id, user_id)
        await self.session.commit()

    async def favorite(self, work_id, *, favorite):
        """由现有收藏服务维护默认书单、条目和收藏计数。"""
        user_id = self.principal.require_user()
        work = await self.works.visible(work_id, consumer="collections")
        discord_id = await self.session.scalar(
            select(DiscordIdentity.discord_user_id).where(
                DiscordIdentity.user_id == user_id, DiscordIdentity.is_current.is_(True)
            )
        )
        # 书单和作品的咨询锁之后才加作品行锁，沿用已有锁顺序。
        await WorkService(self.session).collect(
            work,
            {"id": user_id, "discord_user_id": discord_id},
            favorite,
            verify_visible=lambda: self.works.visible(
                work_id, lock=True, consumer="collections"
            ),
        )
        await self.session.commit()

    async def follows(self, query, *, only_updated):
        """按本次可见性和最后查看时间分页，有更新的作品优先。"""
        user_id = self.principal.require_user()
        offset = cursor_offset(query.get("cursor"))
        limit = min(integer_arg(query.get("limit", "20"), "limit", minimum=1), 100)
        latest_announcement = (
            select(func.max(WorkAnnouncement.created_at))
            .where(
                WorkAnnouncement.work_id == Work.id,
                WorkAnnouncement.withdrawn_at.is_(None),
            )
            .correlate(Work)
            .scalar_subquery()
        )
        change = func.greatest(latest_announcement, Work.rules_updated_at)
        viewed = func.coalesce(ThreadFollow.last_viewed_at, ThreadFollow.followed_at)
        has_update = func.coalesce(change > viewed, False)
        statement = (
            select(Work, ThreadFollow.followed_at, viewed, has_update)
            .join(ThreadFollow, ThreadFollow.work_id == Work.id)
            .where(
                ThreadFollow.fluxer_user_id == user_id,
                ThreadFollow.active_flag.is_(True),
                *visible_conditions(self.principal),
            )
            .order_by(
                has_update.desc(),
                change.desc().nulls_last(),
                ThreadFollow.followed_at.desc(),
                Work.id.desc(),
            )
            .offset(offset)
            .limit(limit + 1)
        )
        if only_updated:
            statement = statement.where(has_update)
        rows = (await self.session.execute(statement)).all()
        works = await self.works.repository.project(
            [row[0] for row in rows], self.principal
        )
        result = [
            {
                "work": work,
                "followedAt": timestamp(row[1]),
                "lastViewedAt": timestamp(row[2]),
                "hasUpdate": row[3],
                "latestVersion": work["latestVersion"],
            }
            for work, row in zip(works, rows, strict=True)
        ]
        return page_result(result, offset, limit)

    async def mark_viewed(self, work_ids):
        """仅标记当前可见的活跃关注，无需改动公告或规则确认。"""
        user_id = self.principal.require_user()
        visible = select(Work.id).where(*visible_conditions(self.principal))
        if work_ids is not None:
            visible = visible.where(
                Work.id.in_([int(value) for value in work_ids if valid_bigint(value)])
            )
        await self.session.execute(
            update(ThreadFollow)
            .where(
                ThreadFollow.fluxer_user_id == user_id,
                ThreadFollow.active_flag.is_(True),
                ThreadFollow.work_id.in_(visible),
            )
            .values(
                last_viewed_at=func.greatest(ThreadFollow.last_viewed_at, utc_now())
            )
        )
        await self.session.commit()
