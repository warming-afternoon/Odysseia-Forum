"""消费逐次净变化，快照只校验总量而不伪造活动日期。"""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from models.thread import Thread
from models.work import Work
from models.work_growth_receipt import WorkGrowthReceipt
from models.work_growth_reaction_state import WorkGrowthReactionState
from work.work_growth_policy import WorkGrowthPolicy
import logging

logger = logging.getLogger(__name__)
from models.work_growth_daily import WorkGrowthDaily
from models.work_source_state import WorkSourceState
from shared.time_utils import utc_now


class WorkGrowthEventService:
    """收件去重、来源检查和日桶累加在调用方同一事务中完成。"""

    def __init__(self, session, forums=None):
        """复用消费者事务及可信来源配置。"""
        self.session = session
        self.forums = forums or {}
        self.policy = WorkGrowthPolicy()

    async def apply(self, event):
        """保留负值和迟到事件，未建立映射时重试而非静默丢弃。"""
        metric = event.get("metric")
        delta = event.get("delta")
        if (
            event.get("kind") != "activity.delta"
            or event.get("schema_version") != 1
            or event.get("platform") != "fluxer"
            or metric not in ("reaction", "reply")
            or type(delta) is not int
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]
            or event.get("source_time_quality") != "operation"
        ):
            raise ValueError("无效作品增长事件")
        if metric == "reaction":
            emoji = event.get("emoji")
            if not isinstance(emoji, dict) or not isinstance(emoji.get("name"), str):
                raise ValueError("反应增长缺少表情身份")
        occurred = datetime.fromisoformat(str(event.get("occurred_at", "")).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            raise ValueError("增长事件必须包含发生时区")
        occurred = occurred.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if occurred < now - timedelta(days=self.policy.accept_days):
            logger.warning("growth_expired event_id=%s occurred_at=%s", event["event_id"], occurred)
            return False
        if occurred > now + timedelta(minutes=5):
            raise ValueError("增长事件发生时间超前")
        if str(event.get("guild_id")) != str(self.forums.get("guild_id")):
            raise ValueError("增长事件服务器不匹配")
        channels = {str(self.forums[key]) for key in (
            "normal", "normal_nsfw", "abyss", "abyss_nsfw"
        ) if self.forums.get(key)}
        if str(event.get("channel_id")) not in channels:
            raise ValueError("增长事件频道不匹配")
        # 与内容映射使用相同来源锁，避免首次快照和活动同时建立状态。
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "source:fluxer:" + str(event["thread_id"])},
        )
        thread = (await self.session.execute(select(Thread).where(
            Thread.platform == "fluxer", Thread.thread_id == int(event["thread_id"]),
        ).execution_options(all_platforms=True))).scalars().first()
        if thread is None or thread.work_id is None:
            source = await self.session.get(
                WorkSourceState, ("fluxer", str(event["thread_id"]))
            )
            if source and source.deleted:
                # 删除墓碑已确认的未知来源无需永久重试，也不能虚构作品。
                await self.session.execute(insert(WorkGrowthReceipt).values(
                    event_id=event["event_id"], payload=event, received_at=utc_now(), occurred_at=occurred.replace(tzinfo=None), outcome="accepted",
                ).on_conflict_do_nothing())
                return False
            raise ValueError("增长事件等待作品映射")
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": thread.work_id})
        accepted = (await self.session.execute(insert(WorkGrowthReceipt).values(
            event_id=event["event_id"], payload=event, received_at=utc_now(), occurred_at=occurred.replace(tzinfo=None), outcome="accepted",
        ).on_conflict_do_nothing().returning(WorkGrowthReceipt.event_id))).scalar_one_or_none()
        if accepted is None:
            return False
        if metric == "reaction" and thread.role == "main":
            emoji = event.get("emoji")
            if not isinstance(emoji, dict) or not isinstance(emoji.get("name"), str):
                raise ValueError("反应增长缺少表情身份")
            key = "custom:" + str(emoji["id"]) if emoji.get("id") else "unicode:" + emoji["name"]
            state = await self.session.get(WorkGrowthReactionState, ("fluxer", str(thread.thread_id)))
            if state is None or occurred.replace(tzinfo=None) <= state.cutoff_at:
                reason = "missing_baseline" if state is None else "before_cutoff"
                receipt = await self.session.get(WorkGrowthReceipt, event["event_id"])
                receipt.outcome = reason
                logger.warning("growth_%s event_id=%s thread_id=%s", reason, event["event_id"], thread.thread_id)
                return False
            counts = dict(state.counts)
            before = max(0, max(counts.values(), default=0))
            counts[key] = counts.get(key, 0) + delta
            if counts[key] < 0:
                logger.warning("growth_negative_state thread_id=%s emoji=%s", thread.thread_id, key)
            if counts[key] == 0:
                counts.pop(key)
            state.counts = counts
            state.last_event_received_at = now.replace(tzinfo=None)
            state.last_event_at = max(state.last_event_at or occurred.replace(tzinfo=None), occurred.replace(tzinfo=None))
            delta = max(0, max(counts.values(), default=0)) - before
        work = await self.session.get(Work, thread.work_id)
        created = work.created_at.replace(tzinfo=timezone.utc) if work.created_at.tzinfo is None else work.created_at.astimezone(timezone.utc)
        # 点赞仅主帖，讨论包含所有子卡；资格按作品年龄而非子卡年龄判定。
        eligible = metric != "reaction" or thread.role == "main"
        if eligible and delta and created <= occurred <= created + timedelta(days=60):
            statement = insert(WorkGrowthDaily).values(
                work_id=thread.work_id, day=occurred.date(), metric=metric, delta=delta,
            )
            await self.session.execute(statement.on_conflict_do_update(
                index_elements=["work_id", "day", "metric"],
                set_={"delta": WorkGrowthDaily.delta + statement.excluded.delta},
            ))
        return True
