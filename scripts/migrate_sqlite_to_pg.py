"""
SQLite → PostgreSQL 数据迁移脚本（一次性使用）。

用法：
    1. 确保 PostgreSQL 容器运行且表已创建（init_db()）
    2. 设置环境变量或修改下方配置
    3. python scripts/migrate_sqlite_to_pg.py

环境变量：
    SQLITE_PATH: SQLite 数据库路径（默认 data/database.db）
    PG_URL: PostgreSQL 连接 URL（默认 postgresql://odysseia:changeme@localhost:5432/odysseia）
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timezone

import asyncpg
import rjieba

# ── 配置 ────────────────────────────────────────────

SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/database.db")
PG_URL = os.environ.get(
    "PG_URL", "postgresql://odysseia:changeme@localhost:5432/odysseia"
)
BATCH_SIZE = 500

# ── 辅助函数 ────────────────────────────────────────


def _build_search_tokens(title: str | None, excerpt: str | None) -> list[str] | None:
    """用 rjieba 对 title + excerpt 分词。"""
    parts = []
    if title:
        parts.append(title)
    if excerpt:
        parts.append(excerpt)
    if not parts:
        return None
    combined = " ".join(parts)
    tokens = list(rjieba.cut(combined))
    filtered = [t.lower().strip() for t in tokens if t.strip()]
    return filtered if filtered else None


def _convert_sqlite_value(val, col_name: str):
    """将 SQLite 值转换为 PostgreSQL 兼容类型。"""
    if val is None:
        return None
    # SQLite JSON 列存储为字符串，在 PG 中需转为 JSON 兼容格式
    if col_name == "thumbnail_urls" and isinstance(val, str):
        import json
        return json.loads(val) if val else []
    # SQLite 的布尔值是 0/1，PG 需要 True/False
    if col_name == "show_flag":
        return bool(val)
    return val


# ── 表迁移顺序（按外键依赖） ─────────────────────────

TABLE_ORDER = [
    "author",
    "tag",
    "bot_config",
    "thread",
    "thread_tag_link",
    "tag_vote",
    "thread_follow",
    "usersearchpreferences",
    "user_update_preference",
    "mutex_tag_group",
    "mutex_tag_rule",
    "banner_application",
    "banner_carousel",
    "banner_waitlist",
    "user_collection",
    "booklist",
    "booklist_item",
]

# 需要用 rjieba 填充 search_vector 的表
SEARCH_VECTOR_TABLE = "thread"


async def main():
    print(f"SQLite 源: {SQLITE_PATH}")
    print(f"PostgreSQL 目标: {PG_URL}")

    # ── 1. 连接数据库 ──
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = await asyncpg.connect(PG_URL)

    try:
        # ── 2. 验证源数据 ──
        for table in TABLE_ORDER:
            count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  SQLite.{table}: {count} 行")

        # ── 3. 迁移每张表 ──
        for table in TABLE_ORDER:
            print(f"\n迁移表: {table}")

            # 获取列名
            pragma = sqlite_conn.execute(f"PRAGMA table_info({table})").fetchall()
            columns = [row["name"] for row in pragma]
            col_list = ", ".join(columns)
            placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))

            # 读取数据
            rows = sqlite_conn.execute(f"SELECT {col_list} FROM {table}").fetchall()

            if not rows:
                print(f"  空表，跳过")
                continue

            # 批量插入 PG
            total = len(rows)
            for offset in range(0, total, BATCH_SIZE):
                batch = rows[offset : offset + BATCH_SIZE]
                pg_batch = []
                for row in batch:
                    converted = tuple(
                        _convert_sqlite_value(row[col], col) for col in columns
                    )
                    pg_batch.append(converted)

                await pg_conn.executemany(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING",
                    pg_batch,
                )
                print(f"  已迁移 {min(offset + BATCH_SIZE, total)}/{total}")

            # 验证行数
            pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"  PG.{table}: {pg_count} 行")

        # ── 4. 填充 search_vector ──
        print("\n填充 search_vector...")
        thread_rows = await pg_conn.fetch(
            "SELECT id, thread_id, title, first_message_excerpt FROM thread"
        )
        updated = 0
        for row in thread_rows:
            tokens = _build_search_tokens(row["title"], row["first_message_excerpt"])
            if tokens:
                await pg_conn.execute(
                    "UPDATE thread SET search_vector = array_to_tsvector($1) WHERE id = $2",
                    tokens,
                    row["id"],
                )
                updated += 1
                if updated % 100 == 0:
                    print(f"  已更新 {updated} 行 search_vector")
        print(f"search_vector 更新完成: {updated}/{len(thread_rows)}")

        # ── 5. 创建 GIN 索引 ──
        print("\n创建 GIN 索引...")
        await pg_conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_thread_search_vector "
            "ON thread USING GIN (search_vector)"
        )

        # ── 6. 重置序列 ──
        print("\n重置序列...")
        for table in TABLE_ORDER:
            try:
                await pg_conn.execute(
                    f"SELECT setval('{table}_id_seq', "
                    f"(SELECT COALESCE(MAX(id), 0) FROM {table}))"
                )
            except Exception:
                pass  # 某些表可能没有 id 序列

        print("\n✅ 迁移完成！")

    finally:
        sqlite_conn.close()
        await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
