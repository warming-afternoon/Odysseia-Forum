"""接入平台身份、作品元信息及 Discord 来源事件总线。"""

from alembic import op

revision = "platform_integration"
down_revision = "work_notification_recipient_uq"
branch_labels = None
depends_on = None


def upgrade():
    """先建立平台表，再安装旧写入路径所需的投影触发器。"""
    statements = (
        """CREATE TABLE platform_outbox (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, work_id bigint NOT NULL,
            event_id text UNIQUE NOT NULL, kind text NOT NULL, payload jsonb NOT NULL,
            attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
            sent_at timestamptz, created_at timestamptz NOT NULL DEFAULT now())""",
        """CREATE INDEX platform_outbox_pending ON platform_outbox(available_at,id)
           WHERE sent_at IS NULL""",
        """CREATE TABLE platform_inbox (
            consumer text NOT NULL, event_id text NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(consumer,event_id))""",
        """CREATE TABLE platform_work_projection (
            consumer text NOT NULL, work_id bigint NOT NULL, state text NOT NULL,
            changed_at timestamptz NOT NULL, PRIMARY KEY(consumer,work_id))""",
        """CREATE TABLE platform_message_state (
            message_id text PRIMARY KEY, work_id bigint NOT NULL, visible boolean NOT NULL,
            changed_at timestamptz NOT NULL, rank integer NOT NULL)""",
        """CREATE TABLE platform_hidden_author (
            user_id text PRIMARY KEY, hidden boolean NOT NULL, changed_at timestamptz NOT NULL)""",
        """CREATE TABLE platform_binding_version (
            user_id text PRIMARY KEY, version bigint NOT NULL)""",
    )
    for statement in statements:
        op.execute(statement)

    op.execute(
        """ALTER TABLE work
            ADD COLUMN summary varchar(200), ADD COLUMN rating varchar,
            ADD COLUMN layout jsonb, ADD COLUMN declarations jsonb,
            ADD COLUMN published_at timestamp, ADD COLUMN updated_at timestamp,
            ADD COLUMN deleted_user_id varchar, ADD COLUMN chat_title varchar(60),
            ADD CONSTRAINT ck_work_rating CHECK (rating IN ('general','r18','r18g'))"""
    )
    op.execute(
        """ALTER TABLE booklist
            ADD COLUMN rating varchar NOT NULL DEFAULT 'general', ADD COLUMN deleted_at timestamp,
            ADD COLUMN deleted_user_id varchar, ADD COLUMN deletion_reason varchar,
            ADD CONSTRAINT ck_booklist_rating CHECK (rating IN ('general','r18','r18g'))"""
    )

    op.execute("""ALTER TABLE thread_follow
    ADD COLUMN manual_unfollowed_at timestamp without time zone""")
    op.execute("""COMMENT ON COLUMN thread_follow.manual_unfollowed_at IS '最近一次主动取消关注的 UTC 时间；手动重新关注后保留，自动关注据此跳过'""")
    op.execute("""CREATE TABLE work_like (
    work_id bigint NOT NULL,
    user_id varchar NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    PRIMARY KEY (work_id, user_id)
)""")
    op.execute("""COMMENT ON TABLE work_like IS '用户当前对作品的平台点赞；取消点赞时移除明细'""")
    op.execute("""ALTER TABLE work_statistics
    ADD COLUMN platform_like_count bigint NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_work_statistics_platform_likes CHECK (platform_like_count >= 0)""")
    op.execute("""ALTER TABLE work
    ADD COLUMN rules_body text NOT NULL DEFAULT '',
    ADD COLUMN rules_version bigint NOT NULL DEFAULT 0,
    ADD COLUMN rules_updated_at timestamp without time zone,
    ADD CONSTRAINT ck_work_rules_state CHECK (
        (rules_version = 0 AND rules_body = '' AND rules_updated_at IS NULL)
        OR (rules_version > 0 AND rules_updated_at IS NOT NULL)
    )""")
    op.execute("""COMMENT ON COLUMN work.rules_version IS '0 表示从未保存；之后每次成功保存作品规则递增一次'""")
    op.execute("""ALTER TABLE work_announcement ADD COLUMN version bigint""")
    op.execute("""WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY work_id ORDER BY created_at, id) AS seq
    FROM work_announcement
)
UPDATE work_announcement AS a SET version = n.seq
FROM numbered AS n WHERE a.id = n.id""")
    op.execute("""ALTER TABLE work_announcement
    ALTER COLUMN version SET NOT NULL,
    ADD CONSTRAINT ck_work_announcement_version CHECK (version > 0),
    ADD CONSTRAINT uq_work_announcement_version UNIQUE (work_id, version)""")
    op.execute("""CREATE FUNCTION assign_work_announcement_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM id FROM work WHERE id = NEW.work_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '作品 % 不存在，无法分配公告序号', NEW.work_id;
    END IF;
    SELECT coalesce(max(a.version), 0) + 1 INTO NEW.version
    FROM work_announcement AS a WHERE a.work_id = NEW.work_id;
    RETURN NEW;
END;
$$""")
    op.execute("""CREATE TRIGGER assign_work_announcement_version
    BEFORE INSERT ON work_announcement
    FOR EACH ROW EXECUTE FUNCTION assign_work_announcement_version()""")
    op.execute("""CREATE TABLE work_ack (
    user_id varchar NOT NULL,
    work_id bigint NOT NULL,
    kind varchar NOT NULL CHECK (kind IN ('announcement', 'rules')),
    confirmed_version bigint NOT NULL CHECK (confirmed_version > 0),
    PRIMARY KEY (work_id, user_id, kind)
)""")
    op.execute("""COMMENT ON COLUMN work_ack.confirmed_version IS '用户已确认的公告序号或规则版本；写入只能推进'""")
    op.execute("""ALTER TABLE notification DROP CONSTRAINT ck_notification_event_type""")
    op.execute("""ALTER TABLE notification ADD COLUMN rules_version bigint""")
    op.execute("""CREATE SEQUENCE work_rules_notice_source_seq AS bigint""")
    op.execute("""COMMENT ON SEQUENCE work_rules_notice_source_seq IS '每次规则保存取一个通知来源 ID，所有接收者共用；序号间隙无业务含义'""")
    op.execute("""CREATE INDEX ix_notification_unread_work_rules
    ON notification (fluxer_user_id, work_id, rules_version)
    WHERE event_type = 'work_rules' AND read_at IS NULL""")

    # Discord 投影通过 SQL 更新作品，使用数据库时间同时覆盖 ORM 和来源同步写入。
    op.execute("""CREATE FUNCTION touch_work_metadata_time() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := timezone('UTC', statement_timestamp());
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER touch_work_metadata_time BEFORE UPDATE OF
        title, description, summary, images, rating, layout, chat_title ON work
        FOR EACH ROW WHEN (NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at AND
            (NEW.title, NEW.description, NEW.summary, NEW.images::jsonb, NEW.rating, NEW.layout, NEW.chat_title)
            IS DISTINCT FROM
            (OLD.title, OLD.description, OLD.summary, OLD.images::jsonb, OLD.rating, OLD.layout, OLD.chat_title))
        EXECUTE FUNCTION touch_work_metadata_time()""")
    op.execute(
        """CREATE TABLE platform_work_audit (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, work_id bigint NOT NULL,
            author_id varchar, actor_id varchar NOT NULL, action varchar NOT NULL,
            reason varchar, created_at timestamp NOT NULL)"""
    )

    op.execute("""
        CREATE FUNCTION platform_resolve_thread_author() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.platform='discord' AND NEW.fluxer_author_id IS NULL THEN
                SELECT user_id INTO NEW.fluxer_author_id FROM discord_identity
                WHERE discord_user_id=NEW.author_id::text;
            END IF;
            RETURN NEW;
        END $$""")
    # PostgreSQL 按名称顺序执行同类触发器，先填作者，再由既有 project_thread_work 汇总。
    op.execute("""CREATE TRIGGER platform_discord_author BEFORE INSERT OR UPDATE ON thread
        FOR EACH ROW EXECUTE FUNCTION platform_resolve_thread_author()""")
    op.execute("""
        CREATE FUNCTION platform_emit_discord_work() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            wid bigint;
            kind text;
            payload jsonb;
            published timestamptz := clock_timestamp();
            identifier text := gen_random_uuid()::text;
            fields text[] := ARRAY[]::text[];
        BEGIN
            IF TG_OP='DELETE' THEN
                IF OLD.source_platform<>'discord' THEN RETURN NULL; END IF;
                wid := OLD.id;
                kind := 'work.purged';
                payload := jsonb_build_object('purgedAt',published);
            ELSE
                IF NEW.source_platform<>'discord' THEN RETURN NULL; END IF;
                wid := NEW.id;
                IF TG_OP='INSERT' AND NEW.deleted_at IS NULL THEN
                    fields := ARRAY['status'];
                ELSIF NEW.deleted_at IS NOT NULL AND (TG_OP='INSERT' OR OLD.deleted_at IS NULL) THEN
                    kind := 'work.deleted';
                    payload := jsonb_build_object('deletedAt',published);
                ELSIF NEW.deleted_at IS NULL AND OLD.deleted_at IS NOT NULL THEN
                    kind := 'work.restored';
                    payload := jsonb_build_object('restoredAt',published);
                ELSE
                    IF NEW.title IS DISTINCT FROM OLD.title THEN fields := array_append(fields,'title'); END IF;
                    IF NEW.description IS DISTINCT FROM OLD.description THEN fields := array_append(fields,'description'); END IF;
                    IF NEW.images::jsonb IS DISTINCT FROM OLD.images::jsonb THEN fields := array_append(fields,'images'); END IF;
                    IF NEW.author_id IS DISTINCT FROM OLD.author_id THEN fields := array_append(fields,'authorId'); END IF;
                    IF (NEW.source_visible, NEW.is_nsfw, NEW.is_abyss) IS DISTINCT FROM
                       (OLD.source_visible, OLD.is_nsfw, OLD.is_abyss) THEN fields := array_append(fields,'visibility'); END IF;
                    IF cardinality(fields)=0 THEN RETURN NULL; END IF;
                END IF;
                IF kind IS NULL THEN
                    kind := 'work.updated';
                    payload := jsonb_build_object('fields',fields,'updatedAt',published);
                END IF;
            END IF;
            INSERT INTO platform_outbox(work_id,event_id,kind,payload) VALUES (wid,identifier,kind,
                jsonb_build_object('eventId',identifier,'source','works','type',kind,'occurredAt',published,
                    'data',payload||jsonb_build_object('workId',wid::text)));
            IF kind='work.restored' THEN
                identifier := gen_random_uuid()::text;
                INSERT INTO platform_outbox(work_id,event_id,kind,payload) VALUES (wid,identifier,'work.updated',
                    jsonb_build_object('eventId',identifier,'source','works','type','work.updated','occurredAt',published,
                        'data',jsonb_build_object('workId',wid::text,'fields',jsonb_build_array('status'),'updatedAt',published)));
            END IF;
            RETURN NULL;
        END $$""")
    op.execute("""CREATE TRIGGER platform_discord_work AFTER INSERT OR UPDATE OR DELETE ON work
        FOR EACH ROW EXECUTE FUNCTION platform_emit_discord_work()""")
    op.execute("""
        CREATE FUNCTION platform_emit_discord_tags() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            changed bigint[];
            wid bigint;
            identifier text;
            stamp timestamptz := clock_timestamp();
        BEGIN
            IF TG_OP='DELETE' THEN changed := ARRAY[OLD.work_id];
            ELSIF TG_OP='INSERT' THEN changed := ARRAY[NEW.work_id];
            ELSIF (OLD.work_id,OLD.tag_id,OLD.ended_at) IS DISTINCT FROM (NEW.work_id,NEW.tag_id,NEW.ended_at)
                THEN changed := ARRAY[OLD.work_id,NEW.work_id];
            ELSE RETURN NULL;
            END IF;
            FOR wid IN SELECT id FROM work WHERE id=ANY(changed) AND source_platform='discord' LOOP
                identifier := gen_random_uuid()::text;
                INSERT INTO platform_outbox(work_id,event_id,kind,payload)
                VALUES(wid,identifier,'work.updated',jsonb_build_object('eventId',identifier,'type','work.updated',
                    'source','works','occurredAt',stamp,'data',jsonb_build_object('workId',wid::text,
                    'fields',jsonb_build_array('tags'),'updatedAt',stamp)));
            END LOOP;
            RETURN NULL;
        END $$""")
    op.execute("""CREATE TRIGGER platform_discord_tags AFTER INSERT OR UPDATE OR DELETE ON tag_binding
        FOR EACH ROW EXECUTE FUNCTION platform_emit_discord_tags()""")


def downgrade():
    """先撤下来源触发器，再移除平台业务表及事件记录。"""
    op.execute("DROP TRIGGER platform_discord_work ON work")
    op.execute("DROP FUNCTION platform_emit_discord_work()")
    op.execute("DROP TRIGGER platform_discord_author ON thread")
    op.execute("DROP FUNCTION platform_resolve_thread_author()")
    op.execute("DROP TRIGGER IF EXISTS platform_discord_tags ON tag_binding")
    op.execute("DROP FUNCTION IF EXISTS platform_emit_discord_tags()")

    op.execute("""DELETE FROM platform_outbox WHERE kind = 'auto_follow'""")
    op.execute("""DELETE FROM notification WHERE event_type = 'work_rules'""")
    op.execute("""DROP INDEX ix_notification_unread_work_rules""")
    op.execute("""ALTER TABLE notification DROP COLUMN rules_version""")
    op.execute("""ALTER TABLE notification ADD CONSTRAINT ck_notification_event_type CHECK (
    event_type IN ('thread_update','author_new_thread','tag_review','work_announcement')
)""")
    op.execute("""DROP SEQUENCE work_rules_notice_source_seq""")
    op.execute("""DROP TABLE work_ack""")
    op.execute("""DROP TRIGGER assign_work_announcement_version ON work_announcement""")
    op.execute("""DROP FUNCTION assign_work_announcement_version()""")
    op.execute("""ALTER TABLE work_announcement DROP COLUMN version""")
    op.execute("""ALTER TABLE work DROP CONSTRAINT ck_work_rules_state,
    DROP COLUMN rules_body, DROP COLUMN rules_version, DROP COLUMN rules_updated_at""")
    op.execute("""ALTER TABLE work_statistics DROP COLUMN platform_like_count""")
    op.execute("""DROP TABLE work_like""")
    op.execute("""ALTER TABLE thread_follow DROP COLUMN manual_unfollowed_at""")

    op.drop_table("platform_work_audit")
    op.execute("DROP TRIGGER touch_work_metadata_time ON work")
    op.execute("DROP FUNCTION touch_work_metadata_time()")
    op.execute("""ALTER TABLE booklist DROP COLUMN rating, DROP COLUMN deleted_at,
        DROP COLUMN deleted_user_id, DROP COLUMN deletion_reason""")
    op.execute("""ALTER TABLE work DROP COLUMN summary, DROP COLUMN rating, DROP COLUMN layout,
        DROP COLUMN declarations, DROP COLUMN published_at, DROP COLUMN updated_at,
        DROP COLUMN deleted_user_id, DROP COLUMN chat_title""")

    for name in (
        "platform_binding_version",
        "platform_hidden_author",
        "platform_message_state",
        "platform_work_projection",
        "platform_inbox",
        "platform_outbox",
    ):
        op.execute(f"DROP TABLE {name}")
