"""在 Compose 应用容器中预检/导入 JSON 标签清单，默认只读。"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from shared.tag_error import TagError
from tag.tag_import import ImportManifest, import_tags


async def run(args):
    raw = Path(args.file).read_bytes()
    manifest = ImportManifest.model_validate_json(raw)
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL 必须使用 postgresql+asyncpg://")
    engine = create_async_engine(url, echo=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                if not args.apply:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL lock_timeout = '15s'"))
                await session.execute(text("SET LOCAL statement_timeout = '120s'"))
                current = set(
                    (
                        await session.execute(
                            text("SELECT version_num FROM alembic_version")
                        )
                    ).scalars()
                )
                expected = set(ScriptDirectory(str(ROOT / "alembic")).get_heads())
                if current != expected:
                    raise ValueError(
                        f"迁移版本不一致：数据库={sorted(current)}，镜像={sorted(expected)}；请先完成迁移"
                    )
                report = await import_tags(
                    session, config, args.actor_id, manifest, apply=args.apply
                )
            # 仅在事务退出并成功提交后输出 applied。
            report["sha256"] = hashlib.sha256(raw).hexdigest()
            report["mode"] = "apply" if args.apply else "dry-run"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2 if report["errors"] else 0
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="UTF-8 JSON 导入清单")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument(
        "--actor-id", type=int, required=True, help="配置中的 BOT 管理员 Discord ID"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 0 < args.actor_id <= 9223372036854775807:
        parser.error("actor-id 必须是正 BIGINT")
    try:
        return asyncio.run(run(args))
    except (ValueError, OSError, TagError, ValidationError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- CLI 边界隐藏数据库连接信息和 SQL 参数
        # 数据库异常可能含连接信息或 SQL 参数，不直接回显。
        print(
            f"导入失败（{type(exc).__name__}），请检查连接、迁移版本及数据库日志。若提交时断连，先重新预检确认状态。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
