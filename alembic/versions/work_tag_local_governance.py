"""将 Fluxer 作品旧原生挂标接入本地治理，保留来源映射及既有轮次。"""

from alembic import op

revision = "work_tag_local_governance"
down_revision = "work_hot_path_indexes"
branch_labels = None
depends_on = None


def upgrade():
    """维护窗口内导入当前原生绑定，不合并同名概念或重置已有票数。"""
    # 与标签池维护互斥；部署流程仍须先停止旧 API、BOT 与任务消费者。
    op.execute("SELECT pg_advisory_xact_lock(73902141)")
    op.execute("""
        WITH imported AS (
            INSERT INTO tag_binding (
                target_type, target_id, work_id, tag_id, binding_source,
                actor_id, fluxer_actor_id, created_at, upvotes, downvotes
            )
            SELECT DISTINCT 'work', b.work_id, b.work_id, s.tag_id, 'local',
                NULL::bigint, NULL::varchar, timezone('utc', now()), 0, 0
            FROM work_native_tag_binding b
            JOIN work_native_tag_source s ON s.id = b.source_id
            JOIN work w ON w.id = b.work_id
            JOIN tag t ON t.id = s.tag_id
            WHERE s.platform = 'fluxer' AND w.source_platform = 'fluxer'
                AND t.deleted_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM tag_binding existing
                    WHERE existing.work_id = b.work_id
                        AND existing.tag_id = s.tag_id
                        AND existing.ended_at IS NULL
                )
            RETURNING id, work_id, tag_id
        )
        INSERT INTO operation_log (
            type, actor_id, target_type, target_id, tag_id, detail, created_at
        )
        SELECT 'tag.bind', NULL, 'work', work_id, tag_id,
            json_build_object('target_id_kind', 'internal',
                'reason', 'fluxer_native_migration', 'binding_id', id::text),
            timezone('utc', now())
        FROM imported
    """)
    # 保留旧原生映射作为迁移证据，新版查询与事件处理不再将它作为生效事实。
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM work_native_tag_binding b
                JOIN work_native_tag_source s ON s.id = b.source_id
                JOIN work w ON w.id = b.work_id
                JOIN tag t ON t.id = s.tag_id
                WHERE s.platform = 'fluxer' AND w.source_platform = 'fluxer'
                    AND t.deleted_at IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM tag_binding active
                        WHERE active.work_id = b.work_id AND active.tag_id = s.tag_id
                            AND active.ended_at IS NULL
                    )
            ) THEN
                RAISE EXCEPTION 'Fluxer work tag migration verification failed';
            END IF;
        END $$
    """)


def downgrade():
    """后续本地标签可能已修改或投票，回退使用部署前完整备份。"""
    raise RuntimeError("作品标签已转为本地治理，请使用维护窗口完整备份回滚")
