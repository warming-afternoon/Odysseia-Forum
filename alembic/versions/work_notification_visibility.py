"""作品可见性变化时通知既有接收者重新校准。"""
from alembic import op

revision = "work_notification_visibility"
down_revision = "work_announcement_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX ix_notification_legacy_thread_lookup ON notification (thread_id, event_source_platform) WHERE work_id IS NULL AND thread_id IS NOT NULL")
    op.execute("CREATE INDEX ix_notification_legacy_target_lookup ON notification (target_type, target_id, event_source_platform) WHERE work_id IS NULL AND target_id IS NOT NULL")
    op.execute("""
    CREATE FUNCTION enqueue_work_visibility_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (OLD.source_visible, OLD.deleted_at, OLD.is_nsfw, OLD.is_abyss, OLD.source_channel_id)
         IS NOT DISTINCT FROM
         (NEW.source_visible, NEW.deleted_at, NEW.is_nsfw, NEW.is_abyss, NEW.source_channel_id) THEN
        RETURN NEW;
      END IF;
      INSERT INTO work_notification_outbox (recipient_id)
      WITH candidates AS (
        SELECT id FROM notification WHERE work_id = NEW.id
        UNION
        SELECT n.id FROM work_announcement a JOIN notification n
          ON n.event_type = 'work_announcement' AND n.event_source_id = a.id
          WHERE a.work_id = NEW.id AND n.work_id IS NULL
        UNION
        SELECT n.id FROM work_announcement a JOIN notification n
          ON n.event_type = 'thread_update' AND n.event_source_platform = 'discord'
          AND n.event_source_id = a.legacy_update_id
          WHERE a.work_id = NEW.id AND n.work_id IS NULL
        UNION
        SELECT n.id FROM thread_update t JOIN notification n
          ON n.event_type = 'thread_update' AND n.event_source_platform = 'discord'
          AND n.event_source_id = t.id
          WHERE t.work_id = NEW.id AND n.work_id IS NULL
        UNION
        SELECT n.id FROM thread th JOIN notification n
          ON n.thread_id = th.thread_id AND n.event_source_platform = th.platform
          WHERE th.work_id = NEW.id AND n.work_id IS NULL
        UNION
        SELECT n.id FROM thread th JOIN notification n
          ON n.target_type = 'thread' AND n.target_id = th.thread_id
          AND n.event_source_platform = th.platform
          WHERE th.work_id = NEW.id AND n.work_id IS NULL AND n.thread_id IS NULL
        UNION
        SELECT n.id FROM notification n
          WHERE n.target_type = 'work' AND n.target_id = NEW.id AND n.work_id IS NULL
      )
      SELECT DISTINCT coalesce(n.fluxer_user_id, d.user_id)
      FROM candidates c JOIN notification n ON n.id = c.id
      LEFT JOIN discord_identity d ON d.discord_user_id = n.user_id::text AND d.is_current IS TRUE
      LEFT JOIN work_announcement a ON
        (n.event_type = 'work_announcement' AND a.id = n.event_source_id)
        OR (n.event_type = 'thread_update' AND n.event_source_platform = 'discord'
            AND a.legacy_update_id = n.event_source_id)
      LEFT JOIN thread_update t ON n.event_type = 'thread_update'
        AND n.event_source_platform = 'discord' AND t.id = n.event_source_id
      WHERE coalesce(n.fluxer_user_id, d.user_id) IS NOT NULL
        AND coalesce(n.work_id, a.work_id, t.work_id,
          CASE WHEN n.target_type = 'work' THEN n.target_id END,
          (SELECT th.work_id FROM thread th
           WHERE th.thread_id = coalesce(n.thread_id,
             CASE WHEN n.target_type = 'thread' THEN n.target_id END)
             AND th.platform = n.event_source_platform
           ORDER BY th.id LIMIT 1)) = NEW.id;
      RETURN NEW;
    END $$
    """)
    op.execute("""
      CREATE TRIGGER trg_work_notification_visibility
      AFTER UPDATE OF source_visible, deleted_at, is_nsfw, is_abyss, source_channel_id ON work
      FOR EACH ROW EXECUTE FUNCTION enqueue_work_visibility_change()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_work_notification_visibility ON work")
    op.execute("DROP FUNCTION IF EXISTS enqueue_work_visibility_change()")
    op.execute("DROP INDEX IF EXISTS ix_notification_legacy_target_lookup")
    op.execute("DROP INDEX IF EXISTS ix_notification_legacy_thread_lookup")
