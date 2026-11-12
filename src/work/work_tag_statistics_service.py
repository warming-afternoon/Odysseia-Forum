"""合并作品的本地与原生标签，并按可见作品统计。"""

from collections import defaultdict

from sqlalchemy import and_, case, func, literal, select, union, union_all

from api.v2.schemas.work_tag_channel_stats import WorkTagChannelStats
from api.v2.schemas.work_tag_stat_item import WorkTagStatItem
from api.v2.schemas.work_tag_stats_request import WorkTagStatsRequest
from api.v2.schemas.work_tag_stats_response import WorkTagStatsResponse
from dto.work.work_actor_dto import WorkActorDTO
from models.tag import Tag
from models.tag_binding import TagBinding
from models.thread import Thread
from models.work import Work
from models.work_native_tag_binding import WorkNativeTagBinding
from models.work_native_tag_source import WorkNativeTagSource
from shared.enum.tag_category import TagCategory
from work.work_repository import WorkRepository


class WorkTagStatisticsService:
    """在数据库中去重和聚合，避免逐作品读取标签或重复计算来源。"""

    def __init__(self, session):
        """复用请求会话，不修改任何绑定或来源数据。"""
        self.session = session

    async def aggregate(
        self, request: WorkTagStatsRequest, actor: WorkActorDTO
    ) -> WorkTagStatsResponse:
        """按与作品搜索相同的访问条件聚合真实及虚拟标签。"""
        filters, _, _ = WorkRepository.visibility_filters(actor)
        platform = request.platform
        if platform is None and (request.guild_id or request.channel_ids):
            platform = "discord"
        if platform:
            filters.append(Work.source_platform == platform)
        if request.guild_id:
            filters.append(Work.source_guild_id == request.guild_id)

        # 虚拟频道只在 Discord 命名空间解释，源频道不能泄漏隐藏作品。
        mappings = []
        if request.include_virtual and platform != "fluxer":
            for target, entries in actor.config.get("channel_mappings", {}).items():
                if request.channel_ids and str(target) not in request.channel_ids:
                    continue
                for entry in entries:
                    name = entry.get("tag_name")
                    sources = {str(item) for item in entry.get("source_channel_ids", [])}
                    if name and sources:
                        mappings.append((str(target), name, sources))
        selected = set(request.channel_ids or [])
        total_filters = list(filters)
        real_filters = list(filters)
        if selected:
            extended = selected.union(*(sources for _, _, sources in mappings))
            total_filters.append(Work.source_channel_id.in_(extended))
            real_filters.append(Work.source_channel_id.in_(selected))
        total = await self.session.scalar(
            select(func.count(Work.id)).where(*total_filters)
        )
        items = await self.real_tag_stats(real_filters, actor.can_view_abyss)
        if mappings:
            items.extend(await self.virtual_tag_stats(filters, mappings))
        items.sort(key=lambda item: (-item.total_work_count, item.tag_name, item.id))
        return WorkTagStatsResponse(total_works=int(total or 0), items=items)

    async def real_tag_stats(self, filters, can_view_abyss):
        """合并新旧治理目标及原生来源，以作品和标准标签的组合去重。"""
        # 回填中的旧帖子以 Thread.work_id 兜底；书单绑定不参与作品统计。
        binding_work = func.coalesce(
            TagBinding.work_id,
            case((TagBinding.target_type == "work", TagBinding.target_id)),
            Thread.work_id,
        )
        local = (
            select(binding_work.label("work_id"), TagBinding.tag_id.label("tag_id"))
            .select_from(TagBinding)
            .outerjoin(
                Thread,
                and_(TagBinding.target_type == "thread", Thread.id == TagBinding.target_id),
            )
            .where(
                TagBinding.ended_at.is_(None),
                TagBinding.target_type.in_(("thread", "work")),
                binding_work.is_not(None),
            )
        )
        native = (
            select(WorkNativeTagBinding.work_id, WorkNativeTagSource.tag_id)
            .join(
                WorkNativeTagSource,
                WorkNativeTagSource.id == WorkNativeTagBinding.source_id,
            )
            .where(WorkNativeTagSource.platform == "discord")
        )
        bindings = union(local, native).subquery("work_tag_pairs")
        statement = (
            select(
                Tag.id, Tag.name, Tag.category, Tag.is_abyss,
                Work.source_platform, Work.source_guild_id, Work.source_channel_id,
                func.count().label("work_count"),
            )
            .select_from(bindings)
            .join(Work, Work.id == bindings.c.work_id)
            .join(Tag, Tag.id == bindings.c.tag_id)
            .where(*filters, Tag.deleted_at.is_(None))
            .group_by(
                Tag.id, Tag.name, Tag.category, Tag.is_abyss,
                Work.source_platform, Work.source_guild_id, Work.source_channel_id,
            )
        )
        if not can_view_abyss:
            statement = statement.where(Tag.is_abyss.is_(False))
        buckets = {}
        for tag_id, name, category, abyss, platform, guild, channel, count in (
            await self.session.execute(statement)
        ).all():
            item = buckets.setdefault(
                tag_id,
                WorkTagStatItem(
                    id=str(tag_id), tag_name=name, category=category,
                    category_name=TagCategory(category).name if category else None,
                    is_abyss=abyss, total_work_count=0, channel_info=[],
                ),
            )
            item.total_work_count += count
            item.channel_info.append(
                WorkTagChannelStats(
                    platform=platform, guild_id=guild, channel_id=channel,
                    work_count=count,
                )
            )
        for item in buckets.values():
            item.channel_info.sort(key=lambda row: (row.platform, row.guild_id, row.channel_id))
        return list(buckets.values())

    async def virtual_tag_stats(self, filters, mappings):
        """保留原有虚拟标签，跨重复映射和多个挂载频道仍按作品去重。"""
        selects = [
            select(
                Work.id.label("work_id"),
                Work.source_guild_id.label("guild_id"),
                literal(target).label("channel_id"),
                literal(name).label("tag_name"),
            ).where(
                *filters, Work.source_platform == "discord",
                Work.source_channel_id.in_(sources),
            )
            for target, name, sources in mappings
        ]
        mapped = union_all(*selects).subquery("virtual_work_tags")
        counts = select(
            mapped.c.tag_name, func.count(func.distinct(mapped.c.work_id))
        ).group_by(mapped.c.tag_name)
        totals = dict((await self.session.execute(counts)).all())
        channels = defaultdict(list)
        statement = select(
            mapped.c.tag_name, mapped.c.guild_id, mapped.c.channel_id,
            func.count(func.distinct(mapped.c.work_id)),
        ).group_by(mapped.c.tag_name, mapped.c.guild_id, mapped.c.channel_id)
        for name, guild, channel, count in (await self.session.execute(statement)).all():
            channels[name].append(
                WorkTagChannelStats(
                    platform="discord", guild_id=guild, channel_id=channel,
                    work_count=count, is_virtual=True,
                )
            )
        return [
            WorkTagStatItem(
                id="virtual:" + name, tag_name=name, is_virtual=True,
                total_work_count=count,
                channel_info=sorted(channels[name], key=lambda row: (row.guild_id, row.channel_id)),
            )
            for name, count in totals.items()
        ]
