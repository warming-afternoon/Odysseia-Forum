from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


class UserSearchPreferencesDTO(BaseModel):
    """用于传输用户搜索偏好设置的数据传输对象。"""

    user_id: int

    # 作者偏好
    include_authors: Optional[List[int]] = None
    exclude_authors: Optional[List[int]] = None

    # 标签偏好
    include_tags: Optional[List[str]] = None
    exclude_tags: Optional[List[str]] = None

    # 关键词偏好
    include_keywords: str = ""
    exclude_keywords: str = ""

    # 时间偏好
    after_date: Optional[datetime] = None
    before_date: Optional[datetime] = None

    # 显示偏好
    preview_image_mode: str = "thumbnail"
    results_per_page: int = 5

    class Config:
        from_attributes = True
