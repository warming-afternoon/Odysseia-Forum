"""将自动关注偏好查询和写入作为 Forum 本地发件箱任务。"""

import os
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import or_, select, text

from models.thread_follow import ThreadFollow
from models.work import Work
from platform_gateway.policy import author_hidden
from work.work_interaction_repository import change_follow


async def candidate(session, work_id, user_id, *, lock=False):
    """检查作品与既有关注；最终写入前在作品锁内重新检查。"""
    if lock:
        await session.execute(
            select(Work.id).where(Work.id == work_id).with_for_update()
        )
    excluded = (
        select(ThreadFollow.id)
        .where(
            ThreadFollow.work_id == Work.id,
            ThreadFollow.fluxer_user_id == user_id,
            or_(
                ThreadFollow.active_flag.is_(True),
                ThreadFollow.manual_unfollowed_at.is_not(None),
            ),
        )
        .exists()
    )
    return await session.scalar(
        select(Work.id).where(
            Work.id == work_id,
            Work.source_visible.is_(True),
            Work.deleted_at.is_(None),
            or_(Work.source_platform != "platform", Work.published_at.is_not(None)),
            Work.author_id.is_distinct_from(user_id),
            ~author_hidden(Work.author_id),
            ~excluded,
        )
    )


async def enqueue_auto_follow(session, work_id, user_id):
    """在触发动作的事务内为候选关系记录本地任务。"""
    if not isinstance(user_id, str) or not user_id:
        return
    if await candidate(session, work_id, user_id) is None:
        return
    await session.execute(
        text("""
        INSERT INTO platform_outbox(work_id,event_id,kind,payload)
        VALUES(:work,:event,'auto_follow',jsonb_build_object('userId',CAST(:user AS text)))
        """),
        {"work": work_id, "event": str(uuid4()), "user": user_id},
    )


async def apply_auto_follow(session, client, work_id, user_id):
    """查询权威偏好，明确关闭或账号不存在时结束，临时故障交给重试。"""
    if await candidate(session, work_id, user_id) is None:
        return
    response = await client.get(
        "/internal/users/" + quote(user_id, safe="") + "/follow-settings",
        headers={"x-ody-internal-token": os.environ["FORUM_CORE_PROFILE_TOKEN"]},
    )
    if response.status_code == 404:
        return
    response.raise_for_status()
    settings = response.json()
    if settings["userId"] != user_id or type(settings["autoFollow"]) is not bool:
        raise ValueError("自动关注偏好响应无效")
    if settings["autoFollow"] and await candidate(session, work_id, user_id, lock=True):
        await change_follow(session, work_id, user_id, follow=True, automatic=True)
