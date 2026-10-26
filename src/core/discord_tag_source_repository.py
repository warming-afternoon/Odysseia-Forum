from sqlalchemy import select, text

from core.tag_source_write_required import TagSourceWriteRequired
from dto.meta.tag_meta import TagMeta
from models import DiscordTagSource, DiscordTagSyncState, Tag
from models.tag_notification_task import TagNotificationTask


class DiscordTagSourceRepository:
    """批量解析 DC 来源与标准概念；已知来源的改名和失效由完整快照处理。"""

    def __init__(self, session):
        """复用当前事务。"""
        self.session = session

    async def get_active_channel_tags(
        self, channel_ids: list[int]
    ) -> dict[int, list[TagMeta]]:
        """批量读取有效原生标签，用 Discord ID 和原始名称构建目录。"""
        if not channel_ids:
            return {}
        # 原生来源 ID 不等于标准标签 ID，不能使用概念映射替代频道标签身份。
        result = await self.session.execute(
            select(
                DiscordTagSource.channel_id,
                DiscordTagSource.discord_tag_id,
                DiscordTagSource.name,
            )
            .where(
                DiscordTagSource.channel_id.in_(channel_ids),
                DiscordTagSource.deleted_at.is_(None),
            )
            .order_by(DiscordTagSource.channel_id, DiscordTagSource.discord_tag_id)
        )
        tags: dict[int, list[TagMeta]] = {}
        for channel_id, discord_id, name in result:
            tags.setdefault(channel_id, []).append(TagMeta(id=discord_id, name=name))
        return tags

    async def concepts(self, names):
        """同名 DC 概念唯一，避免通过重复 INSERT 消耗序列。"""
        names = set(names)
        if not names:
            return {}
        rows = list(
            (
                await self.session.execute(
                    select(Tag).where(
                        Tag.name.in_(names),
                        Tag.source == "discord",
                        Tag.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        result = {tag.name: tag for tag in rows}
        conflicting = set(
            (
                await self.session.execute(
                    select(Tag.name).where(
                        Tag.name.in_(names),
                        Tag.source == "custom",
                        Tag.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        for name in sorted(names - result.keys()):
            tag = Tag(name=name, source="discord", originated_from_discord=True)
            self.session.add(tag)
            result[name] = tag
        await self.session.flush()
        for name in names - {tag.name for tag in rows}:
            if name in conflicting:
                self.session.add(
                    TagNotificationTask(kind="collision", tag_id=result[name].id)
                )
        return result

    async def ensure(
        self, tags_data, channel_id=None, *, allow_source_changes: bool = True
    ):
        """帖子快照仅补齐未知来源，不用缓存名称覆盖完整频道同步结果。"""
        if not tags_data:
            return []
        # 普通同步只读取来源；需要补齐时由调用方退出当前事务后独占重试。
        lock = (
            "pg_advisory_xact_lock"
            if allow_source_changes
            else "pg_advisory_xact_lock_shared"
        )
        await self.session.execute(text(f"SELECT {lock}(73902141)"))
        existing = list(
            (
                await self.session.execute(
                    select(DiscordTagSource)
                    .where(DiscordTagSource.discord_tag_id.in_(tags_data))
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        known = {source.discord_tag_id for source in existing}
        # 已有完整频道检查点时，未知缓存标签不能绕过权威快照重新创建来源。
        missing = tags_data.keys() - known
        if channel_id is not None and await self.session.get(
            DiscordTagSyncState, channel_id, populate_existing=True
        ):
            missing = set()
        needs_channel = channel_id is not None and any(
            source.channel_id is None for source in existing
        )
        if not allow_source_changes:
            if missing or needs_channel:
                raise TagSourceWriteRequired()
            return [source for source in existing if source.deleted_at is None]
        concepts = await self.concepts(tags_data[key] for key in missing)
        for key in sorted(missing):
            source = DiscordTagSource(
                discord_tag_id=key,
                channel_id=channel_id,
                name=tags_data[key],
                tag_id=concepts[tags_data[key]].id,
            )
            self.session.add(source)
            existing.append(source)
        for source in existing:
            if source.channel_id is None and channel_id is not None:
                source.channel_id = channel_id
        await self.session.flush()
        return [source for source in existing if source.deleted_at is None]
