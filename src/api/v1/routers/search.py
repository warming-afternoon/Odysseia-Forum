from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.banner import BannerItem
from api.v1.schemas.search import SearchRequest, SearchResponse, ThreadDetail
from api.v1.schemas.search.author_detail import AuthorDetail
from banner.banner_service import BannerService
from core.cache_service import CacheService
from core.collection_repository import CollectionRepository
from core.follow_repository import ThreadFollowRepository
from core.impression_cache_service import ImpressionCacheService
from core.preferences_repository import PreferencesRepository
from core.tag_cache_service import TagCacheService
from dto.preferences import UserSearchPreferencesDTO
from dto.search import UCB1ConfigDTO
from search.qo.thread_search import ThreadSearchQuery
from models import Thread
from search.search_service import SearchService
from shared.enum.abyss_defaults import AbyssDefaults
from shared.enum.collection_type import CollectionType
from shared.keyword_parser import KeywordParser

# 全局变量，将在应用启动时由 bot_main.py 注入
async_session_factory: async_sessionmaker | None = None
cache_service_instance: CacheService | None = None
tag_cache_service_instance: TagCacheService | None = None
impression_cache_service_instance: ImpressionCacheService | None = None
# 频道映射配置: { target_channel_id: [ { "tag_name": str, "source_channel_ids": [int] } ] }
channel_mappings_config: Dict[int, List[Dict]] = {}

# 深渊区配置
abyss_config: Dict[str, Any] = {
    "channel_ids": AbyssDefaults.CHANNEL_IDS,
    "required_role_id": AbyssDefaults.REQUIRED_ROLE_ID,
}

router = APIRouter(
    prefix="/search", tags=["帖子搜索"], dependencies=[Depends(require_auth)]
)


@router.post("/", response_model=SearchResponse, summary="执行帖子搜索")
async def execute_search(
    request: SearchRequest, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    根据指定的条件搜索帖子，并返回包含作者信息的分页结果。

    - request: 搜索请求参数，包含所有搜索条件
    - return: 分页的搜索结果，包含帖子列表和总数
    """
    # 检查服务是否初始化完成
    if (
        not async_session_factory
        or not cache_service_instance
        or not tag_cache_service_instance
        or not impression_cache_service_instance
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search 服务尚未初始化",
        )

    user_id = (
        int(current_user["id"])
        if current_user and "id" in current_user
        else None
    )

    # [深渊区权限判断] 读取用户身份组，判断是否需要屏蔽深渊区频道
    user_roles = current_user.get("roles", []) if current_user else []
    exclude_channel_ids: list[int] = [
        int(cid) for cid in (request.exclude_channel_ids or [])
    ]

    if abyss_config:
        required_role = str(abyss_config.get("required_role_id", ""))
        abyss_channels: list[int] = abyss_config.get("channel_ids", [])

        # 若用户未登录，或已登录但身份组列表中不包括深渊区查看需要的身份组
        if not user_roles or required_role not in [str(r) for r in user_roles]:
            exclude_channel_ids.extend(abyss_channels)

    # 去重
    exclude_channel_ids = list(set(exclude_channel_ids))

    # 处理偏好合并：仅在用户已登录且 apply_preferences 为 True 时执行
    if request.apply_preferences and user_id:
        async with async_session_factory() as session:
            pref_repo = PreferencesRepository(session)
            prefs = await pref_repo.get_user_preferences(user_id)
            if prefs:
                _merge_user_preferences(request, prefs)

    # 解析高级搜索语法，提取作者名和最终搜索词
    author_name, final_keywords, final_exclude_keywords = _parse_search_keywords(
        request.keywords, request.exclude_keywords
    )

    # 处理收藏搜索：仅在启用收藏搜索且用户已登录时返回用户ID
    user_id_for_collection_search = None
    if request.search_by_collection and current_user and "id" in current_user:
        user_id_for_collection_search = int(current_user["id"])

    # 处理频道映射虚拟标签，解析实际搜索的频道ID和标签
    channel_result = _resolve_channel_mappings(
        request.channel_ids,  # type: ignore
        request.include_tags,
        request.exclude_tags
    )
    effective_channel_ids = channel_result["channel_ids"]
    effective_include_tags = channel_result["include_tags"]
    effective_exclude_tags = channel_result["exclude_tags"]
    searched_channel_ids = channel_result["searched_ids"]
    has_mapping = channel_result["has_mapping"]

    # 构建查询对象，封装所有搜索条件
    query_object = ThreadSearchQuery(
        guild_id=request.guild_id,  # type: ignore
        channel_ids=effective_channel_ids,
        exclude_channel_ids=exclude_channel_ids,
        include_tags=effective_include_tags,
        exclude_tags=effective_exclude_tags,
        tag_logic=request.tag_logic,
        keywords=final_keywords,
        exclude_keywords=final_exclude_keywords,
        exclude_keyword_exemption_markers=request.exclude_keyword_exemption_markers,
        include_authors=request.include_authors,  # type: ignore
        exclude_authors=request.exclude_authors,  # type: ignore
        author_name=author_name,
        created_after=request.created_after,
        created_before=request.created_before,
        active_after=request.active_after,
        active_before=request.active_before,
        reaction_count_range=request.reaction_count_range,
        reply_count_range=request.reply_count_range,
        sort_method=request.sort_method,
        sort_order=request.sort_order,
        custom_base_sort=request.custom_base_sort,
        user_id_for_collection_search=user_id_for_collection_search,
    )

    try:
        # 获取搜索配置参数（UCB1排序算法相关配置）
        ucb1_config = await cache_service_instance.get_ucb1_config()

        exclude_thread_ids = request.exclude_thread_ids or []

        async with async_session_factory() as session:
            # 执行搜索查询并更新展示计数
            threads, total_threads = await _perform_search_and_update_counts(
                session, query_object, ucb1_config, request.limit, exclude_thread_ids  # type: ignore
            )

            # 获取当前用户ID用于后续收藏状态和未读数查询
            user_id = (
                int(current_user["id"]) if current_user and "id" in current_user else None
            )

            # 检查用户的收藏状态
            collected_thread_ids = set()
            if user_id and threads:
                thread_ids = [t.thread_id for t in threads]
                collection_service = CollectionRepository(session)
                collected_thread_ids = await collection_service.get_collected_target_ids(
                    user_id, CollectionType.THREAD, thread_ids
                )

            # 转换搜索结果为响应格式，包含虚拟标签匹配
            results = _build_thread_results(
                threads, has_mapping, effective_channel_ids,
                request.channel_ids, collected_thread_ids  # type: ignore
            )

            # 构建可用的标签列表：虚拟标签置顶 + 实际被搜索频道的真实标签
            available_tags, virtual_tags = _build_available_tags(
                request.channel_ids, searched_channel_ids, has_mapping  # type: ignore
            )

            # 获取Banner轮播列表和未读更新数量
            banner_carousel, unread_count = await _get_banner_and_unread(
                session, request.channel_ids, user_id  # type: ignore
            )

        return SearchResponse(
            total=total_threads,
            limit=request.limit,
            offset=len(exclude_thread_ids),
            results=results,
            available_tags=available_tags,
            virtual_tags=virtual_tags,
            banner_carousel=banner_carousel,
            unread_count=unread_count,
        )
    except Exception as e:
        print(f"搜索时发生内部错误: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="执行搜索时发生内部错误",
        )

@router.get(
    "/thread/{thread_id}",
    response_model=ThreadDetail,
    summary="按 Discord 帖子 ID 获取详情",
)
async def get_thread_detail(
    thread_id: int, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """用于 Banner 点击等场景；避免依赖当前页搜索结果是否已包含该帖。"""
    if not tag_cache_service_instance or not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search 服务尚未初始化",
        )

    user_id = int(current_user["id"]) if current_user and "id" in current_user else None

    async with async_session_factory() as session:
        repo = SearchService(session, tag_cache_service_instance)
        thread = await repo.get_thread_by_discord_id(thread_id)
        if not thread:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="帖子不存在或不可查看"
            )

        collected_thread_ids: set[int] = set()
        if user_id:
            collection_service = CollectionRepository(session)
            collected_thread_ids = await collection_service.get_collected_target_ids(
                user_id, CollectionType.THREAD, [thread.thread_id]
            )

        return ThreadDetail(
            thread_id=thread.thread_id,
            guild_id=thread.guild_id,
            channel_id=thread.channel_id,
            title=thread.title,
            author=AuthorDetail.model_validate(thread.author)
            if thread.author
            else None,
            created_at=thread.created_at,
            last_active_at=thread.last_active_at,
            reaction_count=thread.reaction_count,
            reply_count=thread.reply_count,
            display_count=thread.display_count,
            first_message_excerpt=thread.first_message_excerpt,
            thumbnail_urls=thread.thumbnail_urls or [],
            tags=[tag.name for tag in thread.tags],
            virtual_tags=[],
            collected_flag=thread.thread_id in collected_thread_ids,
        )

# -------------------------
# 辅助方法
# -------------------------


def _merge_user_preferences(
    request: SearchRequest, prefs: UserSearchPreferencesDTO
):
    """
    将用户偏好合并到搜索请求中。
    原则：仅当前端未显示传递（未出现在 unset 列表中）
    且偏好有值时，才使用偏好值覆盖。
    """
    # 获取前端明确传递的字段集合
    explicit_fields = request.model_dump(exclude_unset=True)

    # 映射表: 偏好字段名 -> 请求对象字段名
    mapping = {
        "preferred_channels": "channel_ids",
        "include_authors": "include_authors",
        "exclude_authors": "exclude_authors",
        "include_tags": "include_tags",
        "exclude_tags": "exclude_tags",
        "include_keywords": "keywords",
        "exclude_keywords": "exclude_keywords",
        "exclude_keyword_exemption_markers": (
            "exclude_keyword_exemption_markers"
        ),
        "sort_method": "sort_method",
        "custom_base_sort": "custom_base_sort",
        "created_after": "created_after",
        "created_before": "created_before",
        "active_after": "active_after",
        "active_before": "active_before",
    }

    for pref_key, req_key in mapping.items():
        # 如果前端没传这个参数
        if req_key not in explicit_fields:
            pref_value = getattr(prefs, pref_key, None)
            # 如果偏好设置中有非空有效值，则覆盖默认值
            if pref_value is not None and pref_value != "":
                setattr(request, req_key, pref_value)


def _parse_search_keywords(
    keywords: str | None, exclude_keywords: str | None
) -> tuple[str | None, str | None, str | None]:
    """
    解析搜索关键词，提取作者名、包含词和排除词。

    使用 KeywordParser 解析高级搜索语法（支持 author:xxx 语法），
    将精确匹配词用引号包围以支持短语搜索，合并解析出的排除词与原有排除词。

    Returns:
        tuple: (作者名, 最终关键词字符串, 最终排除词字符串)
    """
    author_name = None
    parsed_include_keywords: list[str] = []
    parsed_exclude_keywords: list[str] = []
    remaining_keywords = keywords or ""

    if keywords:
        # 清理并解析关键词，提取作者名和各类关键词
        sanitized_keywords = KeywordParser.sanitize(keywords)
        (
            author_name,
            parsed_include_keywords,
            parsed_exclude_keywords,
            remaining_keywords,
        ) = KeywordParser.parse(sanitized_keywords)

    # 合并解析出的排除词与原有的排除词，以空格分隔
    final_exclude_keywords = exclude_keywords or ""
    if parsed_exclude_keywords:
        if final_exclude_keywords:
            final_exclude_keywords += " " + " ".join(parsed_exclude_keywords)
        else:
            final_exclude_keywords = " ".join(parsed_exclude_keywords)

    # 构建最终关键词：精确匹配词加引号，保留剩余普通关键词
    final_keywords_parts: list[str] = []
    if parsed_include_keywords:
        for kw in parsed_include_keywords:
            final_keywords_parts.append(f'"{kw}"')
    if remaining_keywords:
        final_keywords_parts.append(remaining_keywords)

    final_keywords = " ".join(final_keywords_parts) if final_keywords_parts else None
    return author_name, final_keywords, final_exclude_keywords or None


def _resolve_channel_mappings(
    channel_ids: List[int] | None,
    include_tags: List[str],
    exclude_tags: List[str],
) -> Dict[str, Any]:
    """
    处理频道映射虚拟标签，将虚拟标签转换为实际频道ID。

    Returns:
        Dict: 包含处理后channel_ids、include_tags、exclude_tags、
              searched_ids（实际搜索的频道集合）、has_mapping（是否有映射）
    """
    result = {
        "channel_ids": list(channel_ids) if channel_ids else None,
        "include_tags": list(include_tags),
        "exclude_tags": list(exclude_tags),
        "searched_ids": set(),
        "has_mapping": False,
    }

    # 无频道ID或多频道时不处理映射，直接返回
    if not result["channel_ids"] or len(result["channel_ids"]) != 1:
        return result

    origin_channel_id = result["channel_ids"][0]
    mappings = channel_mappings_config.get(origin_channel_id, [])
    if not mappings:
        return result

    result["has_mapping"] = True
    virtual_tag_set = {m["tag_name"] for m in mappings}
    mapping_tag_lookup = {m["tag_name"]: m for m in mappings}

    # 分离虚拟标签和真实标签，虚拟标签不传给后端搜索
    included_virtual = [t for t in result["include_tags"] if t in virtual_tag_set]
    excluded_virtual = [t for t in result["exclude_tags"] if t in virtual_tag_set]
    result["include_tags"] = [t for t in result["include_tags"] if t not in virtual_tag_set]
    result["exclude_tags"] = [t for t in result["exclude_tags"] if t not in virtual_tag_set]

    # 计算需要排除的频道（被排除虚拟标签对应的所有频道）
    excluded_channels: set[int] = set()
    for vt in excluded_virtual:
        excluded_channels.update(mapping_tag_lookup[vt].get("source_channel_ids", []))

    # 根据是否选中虚拟标签决定频道范围
    if included_virtual:
        # 有选中的虚拟标签 → 取选中标签对应频道的交集
        channel_sets: list[set[int]] = []
        for vt in included_virtual:
            channel_sets.append(set(mapping_tag_lookup[vt].get("source_channel_ids", [])))
        intersected = channel_sets[0]
        for cs in channel_sets[1:]:
            intersected &= cs
        intersected -= excluded_channels
        result["channel_ids"] = list(intersected)
    else:
        # 无选中虚拟标签 → 搜索原频道 + 所有映射频道（排除被排除的）
        all_mapped: set[int] = set()
        for m in mappings:
            all_mapped.update(m.get("source_channel_ids", []))
        all_mapped -= excluded_channels
        result["channel_ids"] = [origin_channel_id] + list(all_mapped)

    result["searched_ids"] = set(result["channel_ids"])
    return result


async def _perform_search_and_update_counts(
    session: Any,
    query_object: ThreadSearchQuery,
    ucb1_config: UCB1ConfigDTO,
    limit: int,
    exclude_thread_ids: List[int],
) -> tuple[Any, int]:
    """
    执行搜索查询并更新帖子展示次数计数。

    Returns:
        tuple: (帖子列表, 总数)
    """
    repo = SearchService(session, tag_cache_service_instance)  # type: ignore[arg-type]
    threads, total_threads = await repo.search_threads_with_count(
        query_object,
        limit=limit,
        total_display_count=ucb1_config.total_display_count,
        exploration_factor=ucb1_config.exploration_factor,
        strength_weight=ucb1_config.strength_weight,
        exclude_thread_ids=exclude_thread_ids,
    )

    # 按创建时间或收藏时间排序时，不记录展示次数，避免影响热度排序
    is_time_sort = query_object.sort_method in ["created_at", "collected_at"]
    is_custom_time = (
        query_object.sort_method == "custom"
        and query_object.custom_base_sort in ["created_at", "collected_at"]
    )
    count_view = not (is_time_sort or is_custom_time)

    if threads and count_view:
        thread_ids_to_update = [t.id for t in threads if t.id is not None]
        await impression_cache_service_instance.increment(  # type: ignore[union-attr]
            thread_ids_to_update
        )

    return threads, total_threads


def _build_thread_results(
    threads: List[Any],
    has_mapping: bool,
    effective_channel_ids: List[int] | None,
    request_channel_ids: List[int] | None,
    collected_thread_ids: set[int],
) -> List[ThreadDetail]:
    """
    将Thread模型列表转换为ThreadDetail响应列表。

    构建频道ID到虚拟标签的映射，用于在帖子卡片上展示匹配的虚拟标签。
    同时处理作者信息序列化和收藏状态标记。

    Returns:
        List[ThreadDetail]: 转换后的帖子详情列表
    """
    # 预计算 channel_id → 匹配的虚拟标签名（用于帖子卡片标签展示）
    channel_to_virtual: dict[int, list[str]] = {}
    if has_mapping and effective_channel_ids and request_channel_ids:
        origin_ch = request_channel_ids[0] if request_channel_ids else None
        if origin_ch:
            for m in channel_mappings_config.get(origin_ch, []):
                for src_id in m.get("source_channel_ids", []):
                    channel_to_virtual.setdefault(src_id, []).append(m["tag_name"])

    # 转换每个帖子为响应格式
    results: list[ThreadDetail] = []
    for thread in threads:
        matched_virtual = channel_to_virtual.get(thread.channel_id, [])
        thread_detail = ThreadDetail(
            thread_id=thread.thread_id,
            guild_id=thread.guild_id,
            channel_id=thread.channel_id,
            title=thread.title,
            author=AuthorDetail.model_validate(thread.author) if thread.author else None,
            created_at=thread.created_at,
            last_active_at=thread.last_active_at,
            reaction_count=thread.reaction_count,
            reply_count=thread.reply_count,
            display_count=thread.display_count,
            first_message_excerpt=thread.first_message_excerpt,
            thumbnail_urls=thread.thumbnail_urls or [],
            tags=[tag.name for tag in thread.tags],
            virtual_tags=matched_virtual,
            collected_flag=thread.thread_id in collected_thread_ids,
        )
        results.append(thread_detail)

    return results


def _build_available_tags(
    request_channel_ids: List[int] | None,
    searched_channel_ids: set[int],
    has_mapping: bool,
) -> tuple[List[str], List[str]]:
    """
    构建可用的标签列表：虚拟标签置顶 + 实际被搜索频道的真实标签（去重）。

    仅单频道搜索时构建可用标签列表。虚拟标签排在前面方便用户选择，
    真实标签从实际被搜索的频道中聚合（避免显示无关频道的标签）。

    Returns:
        tuple: (可用标签列表, 虚拟标签列表)
    """
    available_tags: list[str] = []
    virtual_tags: list[str] = []

    # 单频道搜索时才构建可用标签
    if not request_channel_ids or len(request_channel_ids) != 1:
        return available_tags, virtual_tags

    target_channel_id = request_channel_ids[0]
    if not cache_service_instance:
        return available_tags, virtual_tags

    # 收集虚拟标签（置顶显示）
    if has_mapping:
        mappings_for_tags = channel_mappings_config.get(target_channel_id, [])
        virtual_tags = [m["tag_name"] for m in mappings_for_tags]

    # 从实际搜索的频道集合聚合真实标签（去重处理）
    real_tag_names: list[str] = []
    seen_tag_names: set[str] = set()
    channels_to_scan = searched_channel_ids if searched_channel_ids else {target_channel_id}

    all_channels_cache = cache_service_instance.get_indexed_channels()
    for ch in all_channels_cache:
        if ch.id not in channels_to_scan:
            continue
        for tag in ch.available_tags:
            if tag.name in seen_tag_names:
                continue
            seen_tag_names.add(tag.name)
            real_tag_names.append(tag.name)

    available_tags = virtual_tags + real_tag_names
    return available_tags, virtual_tags


async def _get_banner_and_unread(
    session: Any,
    request_channel_ids: List[int] | None,
    user_id: int | None,
) -> tuple[List[BannerItem], int]:
    """
    获取Banner轮播列表和用户的未读更新数量。

    在同一session中查询Banner列表（用于首页轮播展示）和
    用户关注帖子的未读更新数量（用于小红点提示）。

    Returns:
        tuple: (Banner列表, 未读数量)
    """
    target_channel_id = request_channel_ids[0] if request_channel_ids else None

    # 获取Banner轮播列表
    banner_service = BannerService(session)
    banners = await banner_service.get_active_banners(channel_id=target_channel_id)
    guild_by_thread: dict[int, int] = {}
    if banners:
        banner_tids = [b.thread_id for b in banners]
        guild_rows = await session.execute(
            select(Thread.thread_id, Thread.guild_id).where(
                Thread.thread_id.in_(banner_tids)  # type: ignore[arg-type]
            )
        )
        guild_by_thread = {tid: gid for tid, gid in guild_rows.all()}
    
    banner_carousel = [
        BannerItem(
            thread_id=banner.thread_id,
            title=banner.title,
            cover_image_url=banner.cover_image_url,
            channel_id=banner.channel_id if banner.channel_id else 0,
            guild_id=guild_by_thread.get(banner.thread_id, 0),
        )
        for banner in banners
    ]

    # 读取未读更新数量（失败时返回0不影响主流程）
    unread_count = 0
    if user_id is not None:
        try:
            follow_service = ThreadFollowRepository(session)
            unread_count = await follow_service.get_unread_count(user_id=user_id)
        except Exception:
            unread_count = 0

    return banner_carousel, unread_count
