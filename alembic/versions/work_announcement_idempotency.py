"""公告发布幂等标识。"""
from alembic import op

revision = "work_announcement_idempotency"
down_revision = "work_announcement_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE work_announcement_idempotency (
            actor_id VARCHAR(128) NOT NULL,
            work_id BIGINT NOT NULL,
            key_hash CHAR(64) NOT NULL,
            request_hash CHAR(64) NOT NULL,
            announcement_id BIGINT NOT NULL REFERENCES work_announcement(id),
            PRIMARY KEY (actor_id, work_id, key_hash)
        )
    """)


def downgrade():
    op.execute("DROP TABLE work_announcement_idempotency")
