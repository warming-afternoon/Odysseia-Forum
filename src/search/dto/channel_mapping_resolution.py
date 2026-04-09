from typing import List, Optional, Set

from pydantic import BaseModel, Field


class ChannelMappingResolutionDTO(BaseModel):
    """频道映射解析结果的数据传输对象"""

    effective_channel_ids: Optional[List[int]] = Field(
        default=None,
        description="实际用于查询的真实频道ID列表",
    )
    """实际用于查询的真实频道ID列表"""

    effective_include_tags: List[str] = Field(
        default_factory=list,
        description="过滤掉虚拟标签后的实际正选标签",
    )
    """过滤掉虚拟标签后的实际正选标签"""

    effective_exclude_tags: List[str] = Field(
        default_factory=list,
        description="过滤掉虚拟标签后的实际反选标签",
    )
    """过滤掉虚拟标签后的实际反选标签"""

    searched_ids: Set[int] = Field(
        default_factory=set,
        description="实际参与搜索的所有真实频道ID集合",
    )
    """实际参与搜索的所有真实频道ID集合"""

    has_mapping: bool = Field(
        default=False,
        description="本次查询是否触发了频道映射机制",
    )
    """本次查询是否触发了频道映射机制"""
