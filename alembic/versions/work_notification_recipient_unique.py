"""阻止同一 V2 接收者重复收到同一事件。"""
from alembic import op

revision = "work_notification_recipient_uq"
down_revision = "work_notification_visibility"
branch_labels = None
depends_on = None


def upgrade():
    # 不自动删除已有记录；有冲突时中止并由部署前检查报告。
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM notification WHERE fluxer_user_id IS NOT NULL
        GROUP BY fluxer_user_id, event_type, event_source_id, event_source_platform
        HAVING count(*) > 1
      ) THEN
        RAISE EXCEPTION 'duplicate V2 notification recipients require preflight review';
      END IF;
    END $$
    """)
    op.execute("""
      CREATE UNIQUE INDEX uq_notification_fluxer_event
      ON notification (fluxer_user_id, event_type, event_source_id, event_source_platform)
      WHERE fluxer_user_id IS NOT NULL
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_notification_fluxer_event")
