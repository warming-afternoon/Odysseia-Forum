"""完整作品计数由异步投影统一更新，避免主帖覆盖子卡聚合。"""

from alembic import op

revision = "work_reply_projection_safety"
down_revision = "work_discovery_growth"
branch_labels = None
depends_on = None

PROJECT_PREVIOUS = """CREATE OR REPLACE FUNCTION project_thread_work() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      -- Only an already-linked child's exposure/no-op update can skip projection.
      -- Comparing the rest of the row preserves future fields and mixed updates.
      IF TG_OP = 'UPDATE' AND NEW.role = 'child' AND NEW.work_id IS NOT NULL
         AND (to_jsonb(NEW) - 'display_count') IS NOT DISTINCT FROM
             (to_jsonb(OLD) - 'display_count') THEN
        RETURN NEW;
      END IF;
      IF NEW.work_id IS NULL THEN
        INSERT INTO work(title,description,images,search_vector,last_active_at,author_id,source_platform,source_guild_id,source_channel_id,source_thread_id,main_ref,created_at,source_visible,deleted_at,deletion_reason)
        VALUES(NEW.title,coalesce(nullif(NEW.body,''),NEW.first_message_excerpt),coalesce(NEW.thumbnail_urls,'[]'::json),NEW.search_vector,NEW.last_active_at,NEW.fluxer_author_id,NEW.platform,NEW.guild_id::text,NEW.channel_id::text,NEW.thread_id::text,
          CASE WHEN NEW.platform='discord' THEN 'https://discord.com/channels/'||NEW.guild_id||'/'||NEW.thread_id ELSE '' END,NEW.created_at,NEW.show_flag,NEW.deleted_at,NEW.deletion_reason) RETURNING id INTO NEW.work_id;
      ELSIF NEW.role='main' THEN
        UPDATE work SET title=CASE WHEN work.metadata_overrides::jsonb ? 'title' THEN work.title ELSE NEW.title END, description=CASE WHEN work.metadata_overrides::jsonb ? 'description' THEN work.description ELSE coalesce(nullif(NEW.body,''),NEW.first_message_excerpt) END, images=CASE WHEN work.metadata_overrides::jsonb ? 'images' THEN work.images ELSE coalesce(NEW.thumbnail_urls,'[]'::json) END,author_id=NEW.fluxer_author_id,
          source_visible=NEW.show_flag,source_channel_id=NEW.channel_id::text,search_vector=CASE WHEN work.metadata_overrides::jsonb ?| ARRAY['title','description'] THEN work.search_vector ELSE NEW.search_vector END,last_active_at=greatest(work.last_active_at,NEW.last_active_at) WHERE id=NEW.work_id AND deleted_at IS NULL;
      END IF;
      IF NEW.role='main' THEN
        INSERT INTO work_statistics(work_id,display_count,reaction_count,reply_count) VALUES(NEW.work_id,greatest(NEW.display_count,0),greatest(NEW.reaction_count,0),greatest(NEW.reply_count,0))
        ON CONFLICT(work_id) DO UPDATE SET display_count=greatest(work_statistics.display_count,excluded.display_count),reaction_count=excluded.reaction_count,reply_count=excluded.reply_count;
      ELSE
        INSERT INTO work_statistics(work_id) VALUES(NEW.work_id) ON CONFLICT(work_id) DO NOTHING;
      END IF;
      INSERT INTO work_task(work_id,kind,dedupe_key,payload) VALUES(NEW.work_id,'projection','thread:'||NEW.id||':'||txid_current(),json_build_object('thread_id',NEW.id)) ON CONFLICT(dedupe_key) DO NOTHING;
      RETURN NEW;
    END $$"""

PROJECT_AGGREGATED = """CREATE OR REPLACE FUNCTION project_thread_work() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      -- Only an already-linked child's exposure/no-op update can skip projection.
      -- Comparing the rest of the row preserves future fields and mixed updates.
      IF TG_OP = 'UPDATE' AND NEW.role = 'child' AND NEW.work_id IS NOT NULL
         AND (to_jsonb(NEW) - 'display_count') IS NOT DISTINCT FROM
             (to_jsonb(OLD) - 'display_count') THEN
        RETURN NEW;
      END IF;
      IF NEW.work_id IS NULL THEN
        INSERT INTO work(title,description,images,search_vector,last_active_at,author_id,source_platform,source_guild_id,source_channel_id,source_thread_id,main_ref,created_at,source_visible,deleted_at,deletion_reason)
        VALUES(NEW.title,coalesce(nullif(NEW.body,''),NEW.first_message_excerpt),coalesce(NEW.thumbnail_urls,'[]'::json),NEW.search_vector,NEW.last_active_at,NEW.fluxer_author_id,NEW.platform,NEW.guild_id::text,NEW.channel_id::text,NEW.thread_id::text,
          CASE WHEN NEW.platform='discord' THEN 'https://discord.com/channels/'||NEW.guild_id||'/'||NEW.thread_id ELSE '' END,NEW.created_at,NEW.show_flag,NEW.deleted_at,NEW.deletion_reason) RETURNING id INTO NEW.work_id;
      ELSIF NEW.role='main' THEN
        UPDATE work SET title=CASE WHEN work.metadata_overrides::jsonb ? 'title' THEN work.title ELSE NEW.title END, description=CASE WHEN work.metadata_overrides::jsonb ? 'description' THEN work.description ELSE coalesce(nullif(NEW.body,''),NEW.first_message_excerpt) END, images=CASE WHEN work.metadata_overrides::jsonb ? 'images' THEN work.images ELSE coalesce(NEW.thumbnail_urls,'[]'::json) END,author_id=NEW.fluxer_author_id,
          source_visible=NEW.show_flag,source_channel_id=NEW.channel_id::text,search_vector=CASE WHEN work.metadata_overrides::jsonb ?| ARRAY['title','description'] THEN work.search_vector ELSE NEW.search_vector END,last_active_at=greatest(work.last_active_at,NEW.last_active_at) WHERE id=NEW.work_id AND deleted_at IS NULL;
      END IF;
      IF NEW.role='main' THEN
        INSERT INTO work_statistics(work_id,display_count,reaction_count,reply_count) VALUES(NEW.work_id,greatest(NEW.display_count,0),greatest(NEW.reaction_count,0),greatest(NEW.reply_count,0))
        ON CONFLICT(work_id) DO UPDATE SET display_count=greatest(work_statistics.display_count,excluded.display_count);
      ELSE
        INSERT INTO work_statistics(work_id) VALUES(NEW.work_id) ON CONFLICT(work_id) DO NOTHING;
      END IF;
      INSERT INTO work_task(work_id,kind,dedupe_key,payload) VALUES(NEW.work_id,'projection','thread:'||NEW.id||':'||txid_current(),json_build_object('thread_id',NEW.id)) ON CONFLICT(dedupe_key) DO NOTHING;
      RETURN NEW;
    END $$"""


GROWTH_PREVIOUS = """CREATE OR REPLACE FUNCTION record_discord_work_growth() RETURNS trigger LANGUAGE plpgsql AS $$
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
    END $$"""

GROWTH_SAFE = """CREATE OR REPLACE FUNCTION record_discord_work_growth() RETURNS trigger LANGUAGE plpgsql AS $$
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
      -- 异常计数修正是未知历史的重新定基线，不能产生当天虚假增长。
      IF OLD.reply_count < 0 OR NEW.reply_count < 0 THEN
        RAISE WARNING '作品 % 回复计数异常，旧值 %，新值 %，本次仅重置增长基线', identifier, OLD.reply_count, NEW.reply_count;
        reply_delta := 0;
      ELSE
        reply_delta := coalesce(NEW.reply_count,0)-coalesce(OLD.reply_count,0);
      END IF;
      INSERT INTO work_growth_daily(work_id,day,metric,delta)
      SELECT identifier,(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date,metric,change
      FROM (VALUES ('reaction',reaction_delta),('reply',reply_delta)) AS changes(metric,change)
      WHERE change <> 0
      ON CONFLICT(work_id,day,metric) DO UPDATE SET delta=work_growth_daily.delta+excluded.delta;
      RETURN NULL;
    END $$"""

def upgrade():
    """保留主帖曝光同步和新行初始化，已有回复及反应等待完整投影。"""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(PROJECT_AGGREGATED)
    op.execute(GROWTH_SAFE)


def downgrade():
    """恢复原触发器，不修改历史帖子或增长数据。"""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(PROJECT_PREVIOUS)
    op.execute(GROWTH_PREVIOUS)
