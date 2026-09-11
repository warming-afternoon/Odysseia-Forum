"""统一标签内部 ID 与自定义标签治理，部署前停止旧版本写入。"""

from alembic import op

revision = "add_custom_tag_governance"
down_revision = "optimize_notification_fanout"
branch_labels = None
depends_on = None


def upgrade():
    """保留旧 ID，新增内部序列和治理数据。"""
    op.execute(
        "ALTER TABLE thread ADD COLUMN native_tag_revision INTEGER NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE tag ADD COLUMN source VARCHAR NOT NULL DEFAULT 'discord'")
    op.execute("ALTER TABLE tag ADD COLUMN discord_tag_id BIGINT")
    op.execute("ALTER TABLE tag ADD COLUMN category INTEGER")
    op.execute("ALTER TABLE tag ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE tag ADD COLUMN deleted_at TIMESTAMP WITHOUT TIME ZONE")
    op.execute("UPDATE tag SET discord_tag_id = id")
    op.execute(
        "CREATE UNIQUE INDEX ix_tag_discord_tag_id_unique ON tag (discord_tag_id)"
    )
    op.execute("CREATE INDEX ix_tag_source ON tag (source)")
    op.execute(
        "CREATE UNIQUE INDEX uq_custom_tag_category_name ON tag (category, name) WHERE source = 'custom'"
    )
    op.execute(
        "ALTER TABLE tag ADD CONSTRAINT ck_tag_source_category CHECK ((source = 'custom' AND category IS NOT NULL AND category BETWEEN 1 AND 7 AND discord_tag_id IS NULL) OR (source = 'discord' AND category IS NULL))"
    )
    op.execute("CREATE SEQUENCE tag_internal_id_seq AS BIGINT OWNED BY tag.id")
    op.execute(
        "SELECT setval('tag_internal_id_seq', GREATEST(COALESCE((SELECT MAX(id) FROM tag), 0), 1), EXISTS(SELECT 1 FROM tag))"
    )
    op.execute(
        "ALTER TABLE tag ALTER COLUMN id SET DEFAULT nextval('tag_internal_id_seq')"
    )
    op.execute("ALTER TABLE notification ALTER COLUMN thread_id DROP NOT NULL")
    op.execute("ALTER TABLE notification ADD COLUMN target_type VARCHAR")
    op.execute("ALTER TABLE notification ADD COLUMN target_id BIGINT")
    op.execute("ALTER TABLE notification DROP CONSTRAINT ck_notification_event_type")
    op.execute(
        "ALTER TABLE notification ADD CONSTRAINT ck_notification_event_type CHECK (event_type IN ('thread_update', 'author_new_thread', 'tag_review'))"
    )
    op.execute(
        "CREATE TABLE tag_alias (\n\ttag_id BIGINT NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tPRIMARY KEY (tag_id, name), \n\tFOREIGN KEY(tag_id) REFERENCES tag (id)\n)"
    )
    op.execute(
        "CREATE TABLE tag_relation (\n\tsource_id BIGINT NOT NULL, \n\ttarget_id BIGINT NOT NULL, \n\tkind VARCHAR NOT NULL, \n\tPRIMARY KEY (source_id, target_id, kind), \n\tCONSTRAINT ck_tag_relation CHECK (kind IN ('implies', 'excludes') AND source_id <> target_id), \n\tFOREIGN KEY(source_id) REFERENCES tag (id), \n\tFOREIGN KEY(target_id) REFERENCES tag (id)\n)"
    )
    op.execute(
        "CREATE TABLE custom_tag_binding (\n\tid SERIAL NOT NULL, \n\ttarget_type VARCHAR NOT NULL, \n\ttarget_id BIGINT NOT NULL, \n\ttag_id BIGINT NOT NULL, \n\tactor_id BIGINT NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tended_at TIMESTAMP WITHOUT TIME ZONE, \n\tend_reason VARCHAR, \n\tupvotes INTEGER NOT NULL, \n\tdownvotes INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_custom_tag_binding CHECK (target_type IN ('thread', 'booklist') AND upvotes >= 0 AND downvotes >= 0), \n\tFOREIGN KEY(tag_id) REFERENCES tag (id)\n)"
    )
    op.execute(
        "CREATE INDEX ix_custom_tag_binding_target_type ON custom_tag_binding (target_type)"
    )
    op.execute(
        "CREATE INDEX ix_custom_tag_binding_target_id ON custom_tag_binding (target_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_active_custom_tag ON custom_tag_binding (target_type, target_id, tag_id) WHERE ended_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_custom_tag_binding_tag_id ON custom_tag_binding (tag_id)"
    )
    op.execute(
        "CREATE TABLE custom_tag_vote (\n\tbinding_id INTEGER NOT NULL, \n\tuser_id BIGINT NOT NULL, \n\tvote INTEGER NOT NULL, \n\tPRIMARY KEY (binding_id, user_id), \n\tCONSTRAINT ck_custom_tag_vote CHECK (vote IN (-1, 1)), \n\tFOREIGN KEY(binding_id) REFERENCES custom_tag_binding (id)\n)"
    )
    op.execute(
        "CREATE TABLE tag_proposal (\n\tid SERIAL NOT NULL, \n\ttarget_type VARCHAR NOT NULL, \n\ttarget_id BIGINT NOT NULL, \n\ttag_id BIGINT NOT NULL, \n\tapplicant_id BIGINT NOT NULL, \n\towner_id BIGINT NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tdue_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\treason VARCHAR, \n\tresolved_at TIMESTAMP WITHOUT TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tag_id) REFERENCES tag (id)\n)"
    )
    op.execute(
        "CREATE INDEX ix_tag_proposal_applicant_id ON tag_proposal (applicant_id)"
    )
    op.execute("CREATE INDEX ix_tag_proposal_target_id ON tag_proposal (target_id)")
    op.execute("CREATE INDEX ix_tag_proposal_target_type ON tag_proposal (target_type)")
    op.execute("CREATE INDEX ix_tag_proposal_status ON tag_proposal (status)")
    op.execute(
        "CREATE UNIQUE INDEX uq_pending_tag_proposal ON tag_proposal (target_type, target_id, tag_id) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX ix_tag_proposal_due_at ON tag_proposal (due_at)")
    op.execute(
        "CREATE TABLE tag_proposal_block (\n\ttarget_type VARCHAR NOT NULL, \n\ttarget_id BIGINT NOT NULL, \n\ttag_id BIGINT NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (target_type, target_id, tag_id), \n\tFOREIGN KEY(tag_id) REFERENCES tag (id)\n)"
    )
    op.execute(
        "CREATE TABLE operation_log (\n\tid SERIAL NOT NULL, \n\ttype VARCHAR NOT NULL, \n\tactor_id BIGINT, \n\ttarget_type VARCHAR NOT NULL, \n\ttarget_id BIGINT NOT NULL, \n\ttag_id BIGINT, \n\tdetail JSON NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)"
    )
    op.execute("CREATE INDEX ix_operation_log_type ON operation_log (type)")
    op.execute("CREATE INDEX ix_operation_log_target_id ON operation_log (target_id)")
    op.execute(
        "CREATE INDEX ix_operation_log_target_type ON operation_log (target_type)"
    )
    op.execute(
        "CREATE TABLE tag_notification_task (\n\tid SERIAL NOT NULL, \n\tproposal_id INTEGER NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tattempts INTEGER NOT NULL, \n\terror VARCHAR, \n\tavailable_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (proposal_id)\n)"
    )
    op.execute(
        "CREATE INDEX ix_tag_notification_task_available_at ON tag_notification_task (available_at)"
    )
    op.execute(
        "CREATE INDEX ix_tag_notification_task_status ON tag_notification_task (status)"
    )


def downgrade():
    """拒绝丢失标签治理历史的自动降级。"""
    raise RuntimeError("此迁移包含审计历史及新内部 ID；请使用备份恢复，禁止破坏性降级")
