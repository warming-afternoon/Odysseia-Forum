"""标签池批量导入：先生成只读计划，再在调用方事务中执行。"""

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import select, text

from core.tag_access_service import TagAccessService
from dto.events.tag_command import TagCommand
from models import Tag
from models.tag_alias import TagAlias
from models.tag_relation import TagRelation
from shared.tag_description import TagDescription
from shared.tag_error import TagError
from shared.tag_rules import validate_graph
from tag.custom_tag_service import CustomTagService


class ImportTag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    category: StrictInt = Field(ge=1, le=7)
    aliases: list[str] | None = Field(default=None, max_length=100)
    existing_id: str | None = None
    origin: str = ""
    description: TagDescription = ""
    """新标签的含义说明；复用已有标签时仅补写空描述，不覆盖已有非空内容"""
    notes: str = ""

    @property
    def is_abyss(self) -> bool:
        """根据原始表格位置识别深渊向 TAG。"""
        return self.origin.strip().startswith("深渊向TAG!")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        value = unicodedata.normalize("NFC", value).strip()
        if not value or len(value) > 100:
            raise ValueError("名称须为 1 至 100 字")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values):
        if values is None:
            return None
        values = sorted({unicodedata.normalize("NFC", v).strip() for v in values})
        if any(not v or len(v) > 200 for v in values):
            raise ValueError("别名须为 1 至 200 字")
        return values

    @field_validator("existing_id")
    @classmethod
    def valid_id(cls, value):
        if value is not None and (
            not value.isascii()
            or not value.isdecimal()
            or not 0 < int(value) <= 9223372036854775807
        ):
            raise ValueError("existing_id 必须是正 BIGINT 的十进制字符串")
        return value


class ImportRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    kind: Literal["implies", "excludes"] = "implies"


class ImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    batch: str = Field(min_length=1, max_length=200)
    tags: list[ImportTag] = Field(min_length=1)
    relations: list[ImportRelation] = Field(default_factory=list)


def build_plan(manifest, tags, aliases, relations):
    """使用负数临时 ID 校验整张关系图；预检不消耗数据库序列。"""
    errors, items, resolved = [], [], {}
    description_updates = []
    by_id = {t.id: t for t in tags}
    alias_map = {}
    for alias in aliases:
        alias_map.setdefault(alias.tag_id, set()).add(alias.name)
    seen_names, seen_ids = set(), set()
    for index, item in enumerate(manifest.tags, 1):
        label = f"{item.key} ({item.origin})"
        name_key = (item.category, item.name)
        if item.key in resolved or name_key in seen_names:
            errors.append(f"{label}: key 或分类＋名称重复")
            continue
        seen_names.add(name_key)
        match = None
        if item.existing_id:
            match = by_id.get(int(item.existing_id))
            if match is None or (match.name, match.category) != (
                item.name,
                item.category,
            ):
                errors.append(f"{label}: existing_id 不存在或名称/分类不一致")
                continue
        else:
            candidates = [
                t
                for t in tags
                if t.source == "custom" and (t.category, t.name) == name_key
            ]
            dc = [
                t
                for t in tags
                if t.source == "discord"
                and t.name == item.name
                and t.deleted_at is None
            ]
            if dc:
                errors.append(
                    f"{label}: 与 DC 标签重名，请明确 existing_id 映射；ID={[str(t.id) for t in dc]}"
                )
                continue
            if len(candidates) > 1:
                errors.append(f"{label}: 数据库存在多个匹配标签")
                continue
            match = candidates[0] if candidates else None
        if match:
            if match.id in seen_ids:
                errors.append(f"{label}: 多个条目映射到同一标签")
            seen_ids.add(match.id)
            if match.deleted_at or not match.enabled:
                errors.append(f"{label}: 已删除或停用，不能自动恢复")
            if getattr(match, "is_abyss", False) != item.is_abyss:
                errors.append(
                    f"{label}: 深渊向标识与数据库不一致，不能自动修改"
                )
            if item.aliases is not None and set(item.aliases) != alias_map.get(
                match.id, set()
            ):
                errors.append(f"{label}: 别名与数据库不一致，不能自动覆盖")
            if item.description and match.description != item.description:
                if match.description:
                    errors.append(f"{label}: 描述与数据库不一致，不能覆盖非空描述")
                else:
                    description_updates.append(
                        {
                            "key": item.key,
                            "id": str(match.id),
                            "origin": item.origin,
                            "before": match.description,
                            "after": item.description,
                        }
                    )
        resolved[item.key] = match.id if match else -index
        items.append(
            {
                "key": item.key,
                "origin": item.origin,
                "name": item.name,
                "category": item.category,
                "is_abyss": item.is_abyss,
                "action": "reuse" if match else "create",
                "id": str(match.id) if match else None,
                "description": match.description if match else item.description,
            }
        )
    edges = {(r.source_id, r.target_id, r.kind) for r in relations}
    planned = []
    for rel in manifest.relations:
        if rel.source not in resolved or rel.target not in resolved:
            errors.append(f"关系 {rel.source} → {rel.target}: 引用未解析的 key")
            continue
        a, b = resolved[rel.source], resolved[rel.target]
        if a == b:
            errors.append(f"关系 {rel.source} → {rel.target}: 自环")
            continue
        if rel.kind == "excludes":
            a, b = sorted((a, b))
        edge = (a, b, rel.kind)
        if edge not in edges:
            planned.append(rel.model_dump())
            edges.add(edge)
    try:
        validate_graph([(a, b) for a, b, kind in edges if kind == "implies"])
    except TagError as exc:
        errors.append(str(exc))
    return {
        "batch": manifest.batch,
        "status": "blocked" if errors else "ready",
        "errors": errors,
        "tags": items,
        "relations_to_create": planned,
        "description_updates": description_updates,
        "description_update_count": len(description_updates),
        "create_count": sum(i["action"] == "create" for i in items),
        "reuse_count": sum(i["action"] == "reuse" for i in items),
    }


async def import_tags(session, config, actor, manifest, *, apply=False):
    """调用方必须提供事务；所有写操作复用领域服务及其审计。"""
    if not TagAccessService(config).bot_admin(actor):
        raise TagError("forbidden", "操作者必须是配置中的 BOT 管理员", 403)
    await session.execute(text("SELECT pg_advisory_xact_lock(73902141)"))
    tags = list((await session.execute(select(Tag))).scalars())
    aliases = list((await session.execute(select(TagAlias))).scalars())
    relations = list((await session.execute(select(TagRelation))).scalars())
    plan = build_plan(manifest, tags, aliases, relations)
    if plan["errors"] or not apply:
        return plan
    service = CustomTagService(session, config)
    ids = {}
    entries = {entry.key: entry for entry in manifest.tags}
    for item in plan["tags"]:
        if item["action"] == "create":
            entry = entries[item["key"]]
            result = await service.dispatch(
                TagCommand(
                    "manage",
                    actor,
                    {
                        "operation": "create",
                        "name": entry.name,
                        "category": entry.category,
                        "is_abyss": entry.is_abyss,
                        "aliases": entry.aliases or [],
                        "description": entry.description,
                    },
                )
            )
            item["id"] = str(result["id"])
            await service.log(
                "tag.pool.import",
                actor,
                "tag",
                int(item["id"]),
                int(item["id"]),
                batch=manifest.batch,
                key=entry.key,
                origin=entry.origin,
                is_abyss=entry.is_abyss,
            )
        ids[item["key"]] = int(item["id"])
    for change in plan["description_updates"]:
        tag_id = int(change["id"])
        await service.dispatch(
            TagCommand(
                "manage",
                actor,
                {
                    "operation": "update",
                    "tag_id": tag_id,
                    "description": change["after"],
                },
            )
        )
        await service.log(
            "tag.pool.import",
            actor,
            "tag",
            tag_id,
            tag_id,
            batch=manifest.batch,
            key=change["key"],
            origin=change["origin"],
            operation="fill_description",
            before=change["before"],
            after=change["after"],
        )
        next(item for item in plan["tags"] if item["key"] == change["key"])[
            "description"
        ] = change["after"]
    for rel in plan["relations_to_create"]:
        await service.dispatch(
            TagCommand(
                "manage",
                actor,
                {
                    "operation": "add_relation",
                    "tag_id": ids[rel["source"]],
                    "target_tag_id": ids[rel["target"]],
                    "kind": rel["kind"],
                },
            )
        )
    plan["status"] = "applied"
    return plan
