"""作品标签统计响应。"""

from pydantic import BaseModel
from api.v2.schemas.work_tag_stat_item import WorkTagStatItem


class WorkTagStatsResponse(BaseModel):
    """统计范围内的可见作品总数及标签分组。"""

    total_works: int
    """包括未挂标签作品的总数，同一作品只计一次。"""

    items: list[WorkTagStatItem]
    """真实标签及请求包含的虚拟标签统计。"""
