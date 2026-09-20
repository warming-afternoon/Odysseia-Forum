"""导入清单校验和 PostgreSQL 原子性验证。"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from conftest import TEST_DATABASE_URL
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models import Tag
from models.operation_log import OperationLog
from models.tag_relation import TagRelation
from shared.tag_error import TagError
from tag.custom_tag_service import CustomTagService
from tag.tag_import import ImportManifest, build_plan, import_tags


def manifest(**extra):
    return ImportManifest.model_validate(
        {
            "version": 1,
            "batch": "test",
            "tags": [
                {
                    "key": "child",
                    "name": "女仆装",
                    "category": 1,
                    "aliases": ["女佣装"],
                },
                {"key": "parent", "name": "制服", "category": 1},
            ],
            "relations": [{"source": "child", "target": "parent"}],
            **extra,
        }
    )


def tag(id=1, **extra):
    values = {
        "name": "女仆装",
        "category": 1,
        "source": "custom",
        "enabled": True,
        "deleted_at": None,
        "description": "",
        "is_abyss": False,
        **extra,
    }
    return SimpleNamespace(id=id, **values)


def test_plan_normalizes_and_detects_duplicates():
    data = manifest(
        tags=[
            {"key": "a", "name": " e\u0301 ", "category": 1},
            {"key": "b", "name": "é", "category": 1},
        ],
        relations=[],
    )
    assert data.tags[0].name == "é"
    assert build_plan(data, [], [], [])["errors"]
    with pytest.raises(ValidationError):
        manifest(tags=[{"key": "a", "name": " ", "category": 1}])
    with pytest.raises(ValidationError):
        manifest(tags=[{"key": "a", "name": "a", "category": True}])


def test_conflicts_and_alias_preservation():
    assert build_plan(manifest(), [tag(enabled=False)], [], [])["errors"]
    assert build_plan(manifest(), [tag(deleted_at="deleted")], [], [])["errors"]
    assert build_plan(manifest(), [tag()], [], [])["errors"]
    aliases = [SimpleNamespace(tag_id=1, name="女佣装")]
    plan = build_plan(manifest(), [tag()], aliases, [])
    assert not plan["errors"]
    assert plan["reuse_count"] == 1
    data = manifest()
    data.tags[0].aliases = None
    assert not build_plan(data, [tag()], aliases, [])["errors"]


def test_dc_requires_explicit_mapping():
    dc = tag()
    dc.source = "discord"
    data = manifest()
    data.tags[0].aliases = None
    assert build_plan(data, [dc], [], [])["errors"]
    data.tags[0].existing_id = "1"
    assert not build_plan(data, [dc], [], [])["errors"]
    data.tags[0].existing_id = "99"
    assert build_plan(data, [dc], [], [])["errors"]


def test_origin_classifies_abyss_and_blocks_direction_mismatch():
    """深渊表来源自动标记，复用不同方向实体时阻止整批导入。"""
    data = manifest(
        tags=[
            {
                "key": "abyss",
                "name": "饲养",
                "category": 5,
                "origin": " 深渊向TAG!A45:E45 ",
            },
            {
                "key": "normal",
                "name": "日常",
                "category": 5,
                "origin": "百进制正常向TAG!A1:E1",
            },
        ],
        relations=[],
    )
    plan = build_plan(data, [], [], [])
    assert [item["is_abyss"] for item in plan["tags"]] == [True, False]
    existing = tag(name="饲养", category=5, is_abyss=False)
    assert build_plan(data, [existing], [], [])["status"] == "blocked"


def test_repository_manifest_classifies_s2_r0045_as_abyss():
    """仓库现有清单中的饲养按 origin 推导为深渊向且无需改写清单。"""
    path = Path(__file__).parents[1] / "temp/类脑配置/custom-tags-v1.json"
    data = ImportManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    item = next(tag for tag in data.tags if tag.key == "s2-r0045")
    assert item.name == "饲养"
    assert item.origin == "深渊向TAG!A45:E45"
    assert item.is_abyss is True


def test_symmetric_relations_are_deduplicated():
    data = manifest(
        relations=[
            {"source": "child", "target": "parent", "kind": "excludes"},
            {"source": "parent", "target": "child", "kind": "excludes"},
        ]
    )
    plan = build_plan(data, [], [], [])
    assert not plan["errors"]
    assert len(plan["relations_to_create"]) == 1


@pytest.mark.parametrize("value", ["0", "-1", "9223372036854775808", "１２", "abc"])
def test_existing_id_validation(value):
    with pytest.raises(ValidationError):
        manifest(tags=[{"key": "a", "name": "a", "category": 1, "existing_id": value}])


def test_graph_missing_self_cycle_and_existing_edges():
    for relations in [
        [{"source": "child", "target": "missing"}],
        [{"source": "child", "target": "child"}],
        [
            {"source": "child", "target": "parent"},
            {"source": "parent", "target": "child"},
        ],
    ]:
        assert build_plan(manifest(relations=relations), [], [], [])["errors"]
    parent = tag(2)
    parent.name = "制服"
    existing = [SimpleNamespace(source_id=2, target_id=1, kind="implies")]
    data = manifest()
    data.tags[0].aliases = None
    assert build_plan(data, [tag(), parent], [], existing)["errors"]


@pytest_asyncio.fixture
async def import_db():
    schema = "test_tag_import_" + uuid4().hex
    engine = create_async_engine(
        TEST_DATABASE_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.run_sync(SQLModel.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_readonly_apply_rerun_and_audit(import_db):
    config = {"bot_admin_user_ids": [99]}
    async with import_db() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        report = await import_tags(session, config, 99, manifest())
        assert report["create_count"] == 2
        assert not list((await session.execute(select(Tag))).scalars())
    async with import_db() as session, session.begin():
        report = await import_tags(session, config, 99, manifest(), apply=True)
        assert report["status"] == "applied"
    async with import_db() as session, session.begin():
        before = len(list((await session.execute(select(OperationLog))).scalars()))
        report = await import_tags(session, config, 99, manifest(), apply=True)
        assert report["create_count"] == 0
        assert report["reuse_count"] == 2
        assert report["relations_to_create"] == []
        assert len(list((await session.execute(select(TagRelation))).scalars())) == 1
        assert (
            len(list((await session.execute(select(OperationLog))).scalars())) == before
        )
    async with import_db() as session, session.begin():
        with pytest.raises(TagError):
            await import_tags(session, config, 1, manifest(), apply=True)


@pytest.mark.asyncio
async def test_late_failure_rolls_back_everything(import_db, monkeypatch):
    original = CustomTagService.dispatch

    async def fail_relation(self, command):
        if command.payload["operation"] == "add_relation":
            raise RuntimeError("模拟最后一步失败")
        return await original(self, command)

    monkeypatch.setattr(CustomTagService, "dispatch", fail_relation)
    with pytest.raises(RuntimeError):
        async with import_db() as session, session.begin():
            await import_tags(
                session, {"bot_admin_user_ids": [99]}, 99, manifest(), apply=True
            )
    async with import_db() as session:
        assert not list((await session.execute(select(Tag))).scalars())
        assert not list((await session.execute(select(OperationLog))).scalars())
