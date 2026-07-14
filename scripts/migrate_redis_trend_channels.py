"""将近九十天全局趋势日榜回填为频道日榜。

用法：
  docker compose exec -T odysseia-forum-api \
    uv run scripts/migrate_redis_trend_channels.py
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.redis_trend_channel_migrator import RedisTrendChannelMigrator  # noqa: E402
from shared.database import AsyncSessionFactory, close_db  # noqa: E402
from shared.redis_client import RedisManager  # noqa: E402


async def run_migration(force: bool) -> None:
    """初始化运行时依赖并执行频道趋势回填。"""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    await RedisManager.init_redis(redis_url)
    try:
        migrator = RedisTrendChannelMigrator(
            session_factory=AsyncSessionFactory,
            redis_client=RedisManager.get_client(),
        )
        result = await migrator.run(force=force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await close_db()
        await RedisManager.close_redis()


def main() -> None:
    """解析命令行参数并运行异步回填。"""
    parser = argparse.ArgumentParser(description="回填 Redis 频道趋势日榜")
    parser.add_argument(
        "--force",
        action="store_true",
        help="清除迁移进度并幂等重跑全部日榜",
    )
    args = parser.parse_args()
    asyncio.run(run_migration(force=args.force))


if __name__ == "__main__":
    main()
