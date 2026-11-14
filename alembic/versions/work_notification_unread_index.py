"""为 V2 接收者未读查询增加部分索引。"""
from alembic import op

revision = "work_notification_unread_index"
down_revision = "work_reply_projection_safety"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_notification_fluxer_unread_created "
        "ON notification (fluxer_user_id, created_at DESC, id DESC) "
        "WHERE read_at IS NULL AND fluxer_user_id IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_notification_fluxer_unread_created")
