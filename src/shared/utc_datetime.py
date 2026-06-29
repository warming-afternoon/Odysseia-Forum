"""UTCDateTime — 在 Pydantic JSON 序列化时将 naive datetime 标记为 UTC (Z 后缀)。

解决后端返回无时区标识的 datetime 导致前端相对时间 8 小时误差的问题：
naive datetime 序列化时自动追加 Z 后缀，让前端的 new Date() 正确解析为 UTC。
"""

from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer


def _serialize_dt_as_utc(dt: datetime) -> str:
    """将 datetime 序列化为 ISO 8601 字符串。

    naive datetime（无时区信息）自动追加 Z 后缀，视为 UTC。
    aware datetime 按自带时区序列化。
    """
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_dt_as_utc, return_type=str),
]
"""用于 Pydantic schema 的 datetime 类型注解。

在 model_dump(mode="json") 时，naive datetime 会自动追加 Z 后缀，
让前端 JavaScript 的 new Date() 正确解析为 UTC 时间，消除相对时间的时区偏差。

用法:
    from shared.utc_datetime import UTCDateTime

    class ThreadDetail(BaseModel):
        created_at: UTCDateTime
        last_active_at: Optional[UTCDateTime]
"""
