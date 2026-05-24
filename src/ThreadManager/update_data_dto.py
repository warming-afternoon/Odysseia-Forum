"""批量更新服务的数据传输对象。"""

from datetime import datetime
from typing import TypedDict


class UpdateData(TypedDict):
    increment: int
    last_active_at: datetime | None
