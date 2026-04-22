from typing import Optional, Set
from pydantic import BaseModel, Field


class FTSResultDTO(BaseModel):
    """
    关键词 FTS 全文搜索结果的数据传输对象。
    """
    
    include_ids: Optional[Set[int]] = Field(
        default=None,
    )
    """
    正选关键词匹配的帖子 ID 集合（内部主键 thread.id）<br>
    如果 keywords 为 None，则此字段为 None<br>
    如果 keywords 有值但未匹配到任何帖子，则为空集。
    """
    
    
    exclude_ids: Set[int] = Field(
        default_factory=set,
    )
    """
    反选关键词匹配的帖子 ID 集合（内部主键 thread.id））<br>
    如果没有排除关键词或未匹配到任何帖子，则为空集。
    """
    
    @property
    def has_include_ids(self) -> bool:
        """是否包含正选关键词结果（非 None）"""
        return self.include_ids is not None
    
    @property
    def has_exclude_ids(self) -> bool:
        """是否有排除的帖子 ID"""
        return bool(self.exclude_ids)
    
    def get_final_ids(self) -> Optional[Set[int]]:
        """
        获取最终有效的帖子 ID 集合（正选减去反选）。<br>
        如果 include_ids 为 None，则返回 None。<br>
        如果 include_ids 为空集或减去 exclude_ids 后为空集，则返回空集。
        """
        if self.include_ids is None:
            return None
        return self.include_ids - self.exclude_ids
    
    def is_empty(self) -> bool:
        """
        检查最终结果是否为空（没有匹配的帖子）。<br>
        当 include_ids 为 None 时，表示没有正选关键词，不算空。<br>
        当 include_ids 为空集或减去 exclude_ids 后为空集时，返回 True。
        """
        final_ids = self.get_final_ids()
        return final_ids is not None and not final_ids