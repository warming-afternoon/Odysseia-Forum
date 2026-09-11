from datetime import datetime

from sqlmodel import Field, SQLModel

from shared.time_utils import utc_now


class TagNotificationTask(SQLModel, table=True):
    """保存审核通知的持久化发送任务及重试状态。"""

    __tablename__ = "tag_notification_task"

    id: int | None = Field(
        default=None, primary_key=True, description="通知发送任务的内部主键"
    )
    """通知发送任务的内部主键"""

    proposal_id: int = Field(
        unique=True, description="关联标签申请的内部 ID；每个申请最多创建一条通知任务"
    )
    """关联标签申请的内部 ID；每个申请最多创建一条通知任务"""

    status: str = Field(
        default="pending",
        index=True,
        description="任务状态：pending 为待发送或待重试，sent 为已发送或站内兜底完成，cancelled 为申请已结束而取消",
    )
    """任务状态：pending 为待发送或待重试，sent 为已发送或站内兜底完成，cancelled 为申请已结束而取消"""

    attempts: int = Field(
        default=0, description="已执行的通知发送尝试次数，用于计算失败重试间隔"
    )
    """已执行的通知发送尝试次数，用于计算失败重试间隔"""

    error: str | None = Field(
        default=None,
        description="最近一次发送异常类型或站内通知兜底原因；无异常记录时为空",
    )
    """最近一次发送异常类型或站内通知兜底原因；无异常记录时为空"""

    available_at: datetime = Field(
        default_factory=utc_now,
        index=True,
        description="允许下一次领取发送任务的时间（UTC）；失败后按退避规则延后",
    )
    """允许下一次领取发送任务的时间（UTC）；失败后按退避规则延后"""
