"""公告编辑版本与实际操作人。"""
from alembic import op

revision = "work_announcement_audit"
down_revision = "work_notification_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE work_announcement ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE work_announcement ADD COLUMN edited_by VARCHAR")
    op.execute("ALTER TABLE work_announcement ADD COLUMN edited_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("ALTER TABLE work_announcement ADD COLUMN withdrawn_by VARCHAR")


def downgrade():
    op.execute("ALTER TABLE work_announcement DROP COLUMN withdrawn_by")
    op.execute("ALTER TABLE work_announcement DROP COLUMN edited_at")
    op.execute("ALTER TABLE work_announcement DROP COLUMN edited_by")
    op.execute("ALTER TABLE work_announcement DROP COLUMN revision")
