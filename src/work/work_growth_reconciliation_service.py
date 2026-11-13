"""快照只校准独立增长基线，不把无法定位的差额写成日增长。"""

import logging
from datetime import datetime, timedelta, timezone
from models.work_growth_reaction_state import WorkGrowthReactionState
from work.work_growth_policy import WorkGrowthPolicy

logger = logging.getLogger(__name__)


class WorkGrowthReconciliationService:
    """静默与无可见积压是近似条件，不是源端强一致水位。"""

    def __init__(self, session):
        """与内容快照复用来源锁和作品事务。"""
        self.session = session
        self.policy = WorkGrowthPolicy()

    async def observe(self, thread, event, allow_rebase=False):
        """初始化或静默校准，并明确记录差异和迟到边界。"""
        if thread.platform != "fluxer" or thread.role != "main":
            return
        raw_time = event.get("occurred_at")
        if not raw_time or "reactions" not in event:
            return
        observed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        observed = observed.astimezone(timezone.utc).replace(tzinfo=None)
        if observed > now + timedelta(minutes=5):
            logger.warning("growth_snapshot_clock_ahead thread_id=%s", thread.thread_id)
            return
        counts = dict(event["reactions"])
        state = await self.session.get(WorkGrowthReactionState, ("fluxer", str(thread.thread_id)))
        if state is None:
            self.session.add(WorkGrowthReactionState(
                platform="fluxer", thread_id=str(thread.thread_id), counts=counts,
                snapshot_counts=counts, cutoff_at=observed, snapshot_at=observed,
            ))
            logger.info("growth_baseline_initialized thread_id=%s", thread.thread_id)
            return
        if observed <= state.snapshot_at:
            return
        state.snapshot_counts = counts
        state.snapshot_at = observed
        if counts == state.counts:
            return
        quiet = state.last_event_received_at is None or state.last_event_received_at <= now - timedelta(seconds=self.policy.quiet_seconds)
        # 快照至少静置一整个窗口；刚产生的快照可能抢先于尚在源端排队的活动。
        settled = observed <= now - timedelta(seconds=self.policy.quiet_seconds)
        covers = state.last_event_at is None or observed >= state.last_event_at
        if allow_rebase and quiet and settled and covers:
            logger.warning("growth_rebase thread_id=%s cutoff=%s old_max=%s snapshot_max=%s", thread.thread_id, observed, max(state.counts.values(), default=0), max(counts.values(), default=0))
            state.counts = counts
            state.cutoff_at = observed
        else:
            logger.warning("growth_snapshot_mismatch_pending thread_id=%s", thread.thread_id)
