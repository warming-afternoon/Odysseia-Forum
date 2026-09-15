from models.tag import Tag
from models import Thread, Booklist, Notification
from sqlalchemy import delete, select
from sqlmodel import col

from models.tag_binding import TagBinding
from models.tag_vote import TagVote
from models.tag_proposal import TagProposal
from models.tag_proposal_block import TagProposalBlock
from models.tag_notification_task import TagNotificationTask
from shared.time_utils import utc_now


class TagBindingRepository:
    """统一绑定写入与无外键目标清理入口，历史操作日志独立保留。"""

    def __init__(self, session):
        """复用调用方事务中的数据库会话。"""
        self.session = session

    async def sync_native(self, thread_id, tags):
        """只同步 DC 管理的有效绑定，不触碰本地绑定或迁移备份。"""
        rows = list(
            (
                await self.session.execute(
                    select(TagBinding).where(
                        col(TagBinding.target_type) == "thread",
                        col(TagBinding.target_id) == thread_id,
                        col(TagBinding.ended_at).is_(None),
                    )
                )
            ).scalars()
        )
        current = {b.tag_id: b for b in rows if b.binding_source == "discord_sync"}
        occupied = {b.tag_id for b in rows}
        desired = set(
            (
                await self.session.execute(
                    select(col(Tag.id)).where(
                        col(Tag.id).in_([t.id for t in tags]),
                        col(Tag.source) == "discord",
                        col(Tag.deleted_at).is_(None),
                    )
                )
            ).scalars()
        )
        changed = False
        for tag_id in set(current) - desired:
            current[tag_id].ended_at = utc_now()
            current[tag_id].end_reason = "discord_sync"
            changed = True
        for tag_id in desired - occupied:
            self.session.add(
                TagBinding(
                    target_type="thread",
                    target_id=thread_id,
                    tag_id=tag_id,
                    binding_source="discord_sync",
                )
            )
            changed = True
        return changed

    async def delete_targets(self, kind, ids):
        """删除目标的依赖数据，保留独立审计及废弃备份表。"""
        if not ids:
            return
        model = Thread if kind == "thread" else Booklist
        await self.session.execute(
            select(col(model.id)).where(col(model.id).in_(ids)).with_for_update()
        )
        bindings = select(col(TagBinding.id)).where(
            col(TagBinding.target_type) == kind, col(TagBinding.target_id).in_(ids)
        )
        proposals = select(col(TagProposal.id)).where(
            col(TagProposal.target_type) == kind, col(TagProposal.target_id).in_(ids)
        )
        await self.session.execute(
            delete(TagVote).where(col(TagVote.binding_id).in_(bindings))
        )
        await self.session.execute(
            delete(Notification).where(
                col(Notification.event_type) == "tag_review",
                col(Notification.event_source_id).in_(proposals),
            )
        )
        await self.session.execute(
            delete(TagNotificationTask).where(
                col(TagNotificationTask.proposal_id).in_(proposals)
            )
        )
        for model in (TagBinding, TagProposal, TagProposalBlock):
            await self.session.execute(
                delete(model).where(
                    col(model.target_type) == kind, col(model.target_id).in_(ids)
                )
            )
