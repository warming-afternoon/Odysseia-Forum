"""保存作品日增长并在收藏变更事务内累计。"""

from alembic import op

revision = "work_discovery_growth"
down_revision = "work_tag_local_governance"
branch_labels = None
depends_on = None


def upgrade():
    """建表后仅记录新变化，不回填无法确定日期的历史总量。"""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""CREATE TABLE work_growth_receipt (
      event_id VARCHAR PRIMARY KEY, occurred_at TIMESTAMP NOT NULL,
      received_at TIMESTAMP NOT NULL, payload JSON, outcome VARCHAR NOT NULL DEFAULT 'accepted'
    )""")
    op.execute("CREATE INDEX ix_growth_receipt_received ON work_growth_receipt(received_at)")
    op.execute("CREATE INDEX ix_growth_receipt_payload ON work_growth_receipt(received_at) WHERE payload IS NOT NULL")
    op.execute("""CREATE TABLE work_growth_reaction_state (
      platform VARCHAR NOT NULL, thread_id VARCHAR NOT NULL,
      counts JSON NOT NULL, cutoff_at TIMESTAMP NOT NULL,
      snapshot_counts JSON NOT NULL, snapshot_at TIMESTAMP NOT NULL,
      last_event_received_at TIMESTAMP, last_event_at TIMESTAMP,
      PRIMARY KEY(platform,thread_id)
    )""")
    op.execute("CREATE INDEX ix_growth_state_snapshot ON work_growth_reaction_state(snapshot_at)")
    # 初次基线不产生增长；旧列表未知时等待新的权威快照。
    op.execute("""INSERT INTO work_growth_reaction_state(platform,thread_id,counts,cutoff_at,snapshot_counts,snapshot_at)
      SELECT platform,thread_id::text,reactions,CURRENT_TIMESTAMP AT TIME ZONE 'UTC',reactions,CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
      FROM thread WHERE platform='fluxer' AND role='main' AND deleted_at IS NULL
      AND (reactions::jsonb <> '{}'::jsonb OR reaction_count=0)""")
    op.execute("""UPDATE thread SET reaction_count=(SELECT coalesce(max(value::bigint),0) FROM jsonb_each_text(reactions::jsonb))
      WHERE platform='fluxer' AND reactions::jsonb <> '{}'::jsonb""")

    op.execute("""CREATE TABLE work_growth_daily (
        work_id BIGINT NOT NULL REFERENCES work(id) ON DELETE CASCADE,
        day DATE NOT NULL,
        metric VARCHAR(16) NOT NULL CHECK (metric IN ('reaction','reply','collection')),
        delta BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (work_id, day, metric)
    )""")
    op.execute("CREATE INDEX ix_work_growth_day ON work_growth_daily(day)")
    op.execute("CREATE INDEX ix_work_growth_window ON work_growth_daily(metric, day, work_id) INCLUDE (delta)")
    op.execute("""CREATE FUNCTION record_work_collection_growth() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE change BIGINT;
    BEGIN
      IF current_setting('odysseia.growth_rebaseline',true) = 'on' THEN RETURN NULL; END IF;
      IF TG_OP = 'INSERT' THEN change := NEW.collection_count;
      ELSE change := NEW.collection_count - OLD.collection_count; END IF;
      IF change = 0 THEN RETURN NULL; END IF;
      INSERT INTO work_growth_daily(work_id,day,metric,delta)
      SELECT NEW.work_id,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,'collection',change
      FROM work w WHERE w.id=NEW.work_id
        AND w.created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '60 days'
      ON CONFLICT(work_id,day,metric) DO UPDATE
        SET delta=work_growth_daily.delta+excluded.delta;
      RETURN NULL;
    END $$""")
    op.execute("""CREATE TRIGGER work_collection_growth AFTER INSERT OR UPDATE OF collection_count
        ON work_statistics FOR EACH ROW EXECUTE FUNCTION record_work_collection_growth()""")

    # Discord 沿用批次实际计数，落库日是观察日而非逐事件原始日期。
    op.execute("""CREATE FUNCTION record_discord_work_growth() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifier BIGINT; reaction_delta BIGINT; reply_delta BIGINT;
    BEGIN
      IF current_setting('odysseia.growth_rebaseline',true) = 'on' THEN RETURN NULL; END IF;
      IF NEW.platform <> 'discord' OR NEW.work_id IS NULL THEN RETURN NULL; END IF;
      IF NEW.work_id IS DISTINCT FROM OLD.work_id THEN RETURN NULL; END IF;
      identifier := NEW.work_id;
      IF NOT EXISTS (SELECT 1 FROM work WHERE id=identifier
        AND created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '60 days') THEN RETURN NULL; END IF;
      -- 旧帖首次补齐列表属于口径基线，不能把历史摘要修正当当天取消。
      IF OLD.reactions::jsonb = '{}'::jsonb AND OLD.reaction_count > 0
        AND OLD.reaction_emojis::jsonb = '{}'::jsonb THEN
        reaction_delta := 0;
      ELSE
      reaction_delta := CASE WHEN NEW.role='main' THEN coalesce(NEW.reaction_count,0)-coalesce(OLD.reaction_count,0) ELSE 0 END;
      END IF;
      reply_delta := coalesce(NEW.reply_count,0)-coalesce(OLD.reply_count,0);
      INSERT INTO work_growth_daily(work_id,day,metric,delta)
      SELECT identifier,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,metric,change
      FROM (VALUES ('reaction',reaction_delta),('reply',reply_delta)) AS changes(metric,change)
      WHERE change <> 0
      ON CONFLICT(work_id,day,metric) DO UPDATE SET delta=work_growth_daily.delta+excluded.delta;
      RETURN NULL;
    END $$""")
    op.execute("""CREATE TRIGGER discord_work_growth AFTER UPDATE OF reaction_count,reply_count
      ON thread FOR EACH ROW EXECUTE FUNCTION record_discord_work_growth()""")


def downgrade():
    """移除新增统计，不修改现有收藏及作品。"""
    op.execute("DROP TRIGGER discord_work_growth ON thread")
    op.execute("DROP FUNCTION record_discord_work_growth()")
    op.execute("DROP TRIGGER work_collection_growth ON work_statistics")
    op.execute("DROP FUNCTION record_work_collection_growth()")
    op.execute("DROP TABLE work_growth_daily")
    op.execute("DROP TABLE work_growth_reaction_state")
    op.execute("DROP TABLE work_growth_receipt")
