"""增长数据的有界保留清理和静默近似校准。"""

from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import text
from work.work_growth_policy import WorkGrowthPolicy

logger = logging.getLogger(__name__)


class WorkGrowthMaintenanceService:
    """按小批处理，不删除身份认证或内容快照的永久收件箱。"""

    def __init__(self, session):
        """调用方每分钟至多启动一次，并独立提交事务。"""
        self.session = session
        self.policy = WorkGrowthPolicy()

    async def run(self, allow_rebase=False):
        """清理过期载荷及幂等键，静默重置仅修复基线不补造历史。"""
        owned = (await self.session.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended('work-growth-maintenance',0))"))).scalar_one()
        if not owned:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        params = {"receipts": now-timedelta(days=self.policy.receipt_days), "payload": now-timedelta(days=self.policy.payload_days), "days": (now-timedelta(days=120)).date()}
        for _ in range(self.policy.cleanup_batches):
            await self.session.execute(text("DELETE FROM work_growth_receipt WHERE event_id IN (SELECT event_id FROM work_growth_receipt WHERE received_at < :receipts ORDER BY received_at LIMIT 2000)"), params)
            await self.session.execute(text("UPDATE work_growth_receipt SET payload=NULL WHERE event_id IN (SELECT event_id FROM work_growth_receipt WHERE received_at < :payload AND payload IS NOT NULL ORDER BY received_at LIMIT 2000)"), params)
            await self.session.execute(text("DELETE FROM work_growth_daily WHERE (work_id,day,metric) IN (SELECT work_id,day,metric FROM work_growth_daily WHERE day < :days ORDER BY day LIMIT 2000)"), params)
        oldest = (await self.session.execute(text("SELECT min(received_at) FROM work_growth_receipt"))).scalar_one_or_none()
        if oldest is not None and oldest < params["receipts"]:
            logger.warning("growth_cleanup_lag oldest_receipt=%s cutoff=%s", oldest, params["receipts"])
        # 资格期及最大接受窗口均结束后，不再需要增长分组状态。
        await self.session.execute(text("DELETE FROM work_growth_reaction_state WHERE (platform,thread_id) IN (SELECT s.platform,s.thread_id FROM work_growth_reaction_state s JOIN thread t ON t.platform=s.platform AND t.thread_id::text=s.thread_id JOIN work w ON w.id=t.work_id WHERE w.created_at < :old LIMIT 2000)"), {"old": now-timedelta(days=60+self.policy.accept_days+8)})
        if not allow_rebase:
            return
        quiet = now-timedelta(seconds=self.policy.quiet_seconds)
        # 与活动相同的来源锁逐项获取，跳过忙碌来源，不在持有状态行锁后反向等待来源。
        rows = (await self.session.execute(text("SELECT platform,thread_id FROM work_growth_reaction_state WHERE snapshot_at <= :quiet AND (last_event_received_at IS NULL OR last_event_received_at <= :quiet) AND snapshot_counts::jsonb <> counts::jsonb ORDER BY snapshot_at LIMIT 100"), {"quiet": quiet})).all()
        for platform, thread_id in rows:
            locked = (await self.session.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "source:"+platform+":"+thread_id})).scalar_one()
            if not locked:
                continue
            changed = await self.session.execute(text("UPDATE work_growth_reaction_state SET counts=snapshot_counts,cutoff_at=snapshot_at WHERE platform=:platform AND thread_id=:thread AND snapshot_at <= :quiet AND (last_event_received_at IS NULL OR last_event_received_at <= :quiet) AND (last_event_at IS NULL OR snapshot_at >= last_event_at) AND snapshot_counts::jsonb <> counts::jsonb RETURNING cutoff_at"), {"platform": platform, "thread": thread_id, "quiet": quiet})
            cutoff = changed.scalar_one_or_none()
            if cutoff is not None:
                logger.warning("growth_rebase thread_id=%s cutoff=%s approximate=true", thread_id, cutoff)
