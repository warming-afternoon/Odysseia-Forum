"""按统一标签概念聚合的作品统计。"""

from pydantic import BaseModel
from api.v2.schemas.work_tag_channel_stats import WorkTagChannelStats


class WorkTagStatItem(BaseModel):
    """同一作品的重复绑定只计一次，不按绑定来源拆分。"""

    id: str
    """真实标签内部编号；虚拟标签使用 virtual: 名称标识。"""

    tag_name: str
    """标签标准名称。"""

    category: int | None = None
    """标签分类编号。"""

    category_name: str | None = None
    """标签分类中文名。"""

    is_abyss: bool = False
    """标签是否为深渊向。"""

    is_virtual: bool = False
    """虚拟标签应使用名称筛选，不能作为实体编号提交。"""

    total_work_count: int
    """跨来源频道去重后的可见作品数。"""

    channel_info: list[WorkTagChannelStats]
    """按主来源平台、服务器和频道分组的统计。"""
