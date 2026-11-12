"""作品标签统计 HTTP 适配。"""

from fastapi import APIRouter, Depends
from api.v2.dependencies import current_member, get_session_factory
from api.v2.schemas.work_tag_stats_request import WorkTagStatsRequest
from api.v2.schemas.work_tag_stats_response import WorkTagStatsResponse
from dto.work.work_actor_dto import WorkActorDTO
from work.work_tag_statistics_service import WorkTagStatisticsService

router = APIRouter(prefix="/v2/tags", tags=["作品标签统计"])


@router.post("/stats", response_model=WorkTagStatsResponse)
async def work_tag_stats(
    request: WorkTagStatsRequest, principal=Depends(current_member)
):
    """在当前身份和内容可见范围内统计统一作品标签。"""
    async with get_session_factory()() as session:
        # 身份快照与请求范围一并传入，统计不能绕过搜索的访问限制。
        service = WorkTagStatisticsService(session)
        return await service.aggregate(
            request, WorkActorDTO.from_principal(principal)
        )
