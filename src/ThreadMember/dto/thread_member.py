from datetime import datetime
from typing import TypedDict

class ThreadMemberData(TypedDict):
    """帖子成员数据 DTO"""
    thread_id: int
    user_id: int
    joined_at: datetime
