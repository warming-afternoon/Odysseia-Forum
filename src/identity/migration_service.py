"""分批投影、条件回填与切换前审计。"""

import json
from sqlalchemy import text
from identity.reference_manifest import REFERENCES, AUTHOR_LISTS, fluxer_column


class MigrationService:
    """只处理 Fluxer 权威导出，不生成或猜测统一用户 ID。"""

    def __init__(self, engine):
        self.engine = engine

    def import_batch(self, records):
        """一个事务批量导入用户和绑定，版本倒退或身份冲突直接失败。"""
        # 文件中的字段来自 Fluxer 导出或批量 RPC，旧 ID 永远不作为新 ID。
        clean = []
        seen_discord = set()
        seen_users = set()
        for row in records:
            user_id = str(row["user_id"])
            discord_id = str(row["discord_user_id"])
            if (
                not user_id.isdecimal()
                or user_id == "0"
                or not discord_id.isdecimal()
                or discord_id == "0"
            ):
                raise ValueError("权威身份必须是非零十进制字符串")
            if user_id in seen_users or discord_id in seen_discord:
                raise ValueError("同一批次包含重复或碰撞身份")
            seen_users.add(user_id)
            seen_discord.add(discord_id)
            clean.append(
                {
                    **row,
                    "user_id": user_id,
                    "discord_user_id": discord_id,
                    "version": int(row.get("version", 1)),
                }
            )
        payload = json.dumps(clean)
        with self.engine.begin() as conn:
            # 使用单条集合查询发现重绑，避免静默改写历史所有权。
            conflicts = conn.execute(
                text("""SELECT d.discord_user_id FROM discord_identity d
                JOIN jsonb_to_recordset(CAST(:payload AS jsonb)) AS x(discord_user_id text,user_id text,version bigint)
                ON d.discord_user_id=x.discord_user_id
                WHERE d.user_id<>x.user_id OR d.version>x.version LIMIT 1"""),
                {"payload": payload},
            ).first()
            if conflicts:
                raise ValueError("绑定冲突或版本倒退，需要人工审核")
            conn.execute(
                text("""INSERT INTO "user" (id,username,display_name,avatar_url)
                SELECT user_id,username,display_name,avatar_url FROM jsonb_to_recordset(CAST(:payload AS jsonb))
                AS x(user_id text,username text,display_name text,avatar_url text)
                ON CONFLICT(id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,
                avatar_url=excluded.avatar_url,updated_at=now()"""),
                {"payload": payload},
            )
            conn.execute(
                text("""INSERT INTO discord_identity (discord_user_id,user_id,version)
                SELECT discord_user_id,user_id,version FROM jsonb_to_recordset(CAST(:payload AS jsonb))
                AS x(discord_user_id text,user_id text,version bigint)
                ON CONFLICT(discord_user_id) DO UPDATE SET version=excluded.version"""),
                {"payload": payload},
            )
        return len(clean)

    def backfill_batch(self, table, column, limit):
        """锁定有限行并校验源快照，原系统并发改作者时不会被覆盖。"""
        if table not in REFERENCES or column not in REFERENCES[table]:
            raise ValueError("未知身份列")
        target = fluxer_column(column)
        # NULL 与系统零保留原语义；无映射的行由审计阻止切换。
        statement = f"""WITH batch AS (
            SELECT t.ctid AS row_id,t.{column} AS source_id,d.user_id
            FROM {table} t JOIN discord_identity d ON d.discord_user_id=t.{column}::text
            WHERE t.{column}<>0 AND t.{target} IS DISTINCT FROM d.user_id
            ORDER BY t.ctid LIMIT :limit FOR UPDATE OF t SKIP LOCKED)
            UPDATE {table} t SET {target}=b.user_id FROM batch b
            WHERE t.ctid=b.row_id AND t.{column} IS NOT DISTINCT FROM b.source_id"""
        with self.engine.begin() as conn:
            return conn.execute(text(statement), {"limit": limit}).rowcount

    def backfill_lists(self, table, column, limit):
        """只在全部作者映射齐全时原子替换列表，保留顺序和重复项。"""
        if column not in AUTHOR_LISTS.get(table, []):
            raise ValueError("未知作者列表")
        target = "fluxer_" + column
        statement = f"""WITH candidates AS (
            SELECT t.ctid AS row_id,t.{column}::jsonb AS source_ids,
                COALESCE((SELECT jsonb_agg(d.user_id ORDER BY a.ordinality)
                FROM jsonb_array_elements_text(t.{column}::jsonb) WITH ORDINALITY a(id,ordinality)
                JOIN discord_identity d ON d.discord_user_id=a.id),'[]'::jsonb) AS ids
            FROM {table} t WHERE t.{column} IS NOT NULL
                AND t.{target}::jsonb IS DISTINCT FROM COALESCE((SELECT jsonb_agg(d.user_id ORDER BY a.ordinality)
                FROM jsonb_array_elements_text(t.{column}::jsonb) WITH ORDINALITY a(id,ordinality)
                JOIN discord_identity d ON d.discord_user_id=a.id),'[]'::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(t.{column}::jsonb) a(id)
                LEFT JOIN discord_identity d ON d.discord_user_id=a.id WHERE d.user_id IS NULL)
            ORDER BY t.ctid LIMIT :limit FOR UPDATE OF t SKIP LOCKED),
            batch AS (SELECT * FROM candidates WHERE ids IS DISTINCT FROM
                (SELECT {target}::jsonb FROM {table} t WHERE t.ctid=candidates.row_id))
            UPDATE {table} t SET {target}=b.ids::json FROM batch b
            WHERE t.ctid=b.row_id AND t.{column}::jsonb IS NOT DISTINCT FROM b.source_ids"""
        with self.engine.begin() as conn:
            return conn.execute(text(statement), {"limit": limit}).rowcount

    def backfill_vote_keys(self, limit):
        """历史投票代理键分批填充，不在 DDL 事务中全表改写。"""
        with self.engine.begin() as conn:
            return conn.execute(text("""WITH batch AS (SELECT ctid FROM tag_vote WHERE id IS NULL
                LIMIT :limit FOR UPDATE SKIP LOCKED) UPDATE tag_vote t SET id=nextval('fluxer_tag_vote_id_seq')
                FROM batch WHERE t.ctid=batch.ctid AND t.id IS NULL"""), {"limit": limit}).rowcount

    def audit(self):
        """统计全部缺失、失配和孤立身份，任何非零计数都阻止切换。"""
        result = {}
        with self.engine.connect() as conn:
            result["tag_vote.id"] = conn.execute(text("SELECT count(*) FROM tag_vote WHERE id IS NULL")).scalar_one()
            for table, columns in REFERENCES.items():
                for column in columns:
                    target = fluxer_column(column)
                    result[f"{table}.{column}"] = conn.execute(
                        text(f"""SELECT count(*) FROM {table} t
                        LEFT JOIN discord_identity d ON d.discord_user_id=t.{column}::text
                        LEFT JOIN "user" u ON u.id=t.{target}
                        WHERE (t.{column}<>0 AND t.{column} IS NOT NULL AND
                        (d.user_id IS NULL OR t.{target} IS DISTINCT FROM d.user_id))
                        OR (t.{target} IS NOT NULL AND u.id IS NULL)""")
                    ).scalar_one()
            for table, columns in AUTHOR_LISTS.items():
                for column in columns:
                    result[f"{table}.{column}"] = conn.execute(
                        text(f"""SELECT count(*) FROM {table} t
                        WHERE t.{column} IS NOT NULL AND (t.fluxer_{column}::jsonb IS DISTINCT FROM
                        COALESCE((SELECT jsonb_agg(d.user_id ORDER BY a.ordinality)
                        FROM jsonb_array_elements_text(t.{column}::jsonb) WITH ORDINALITY a(id,ordinality)
                        JOIN discord_identity d ON d.discord_user_id=a.id),'[]'::jsonb) OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements_text(t.{column}::jsonb) a(id)
                        LEFT JOIN discord_identity d ON d.discord_user_id=a.id WHERE d.user_id IS NULL))""")
                    ).scalar_one()
        return result
