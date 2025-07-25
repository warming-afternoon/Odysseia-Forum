from typing import Optional, List
from sqlmodel import Field, SQLModel, JSON, Column
from datetime import datetime

class UserSearchPreferences(SQLModel, table=True):
    """用户搜索偏好模型，完整版。"""
    user_id: int = Field(primary_key=True)
    
    # 作者偏好
    include_authors: Optional[List[int]] = Field(default=None, sa_column=Column(JSON))
    exclude_authors: Optional[List[int]] = Field(default=None, sa_column=Column(JSON))
    
    # 时间偏好
    after_date: Optional[datetime] = Field(default=None)
    before_date: Optional[datetime] = Field(default=None)
    
    # 显示偏好
    preview_image_mode: str = Field(default="thumbnail")
    results_per_page: int = Field(default=5)