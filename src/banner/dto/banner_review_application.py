from dataclasses import dataclass
from datetime import datetime

from models import BannerApplication


@dataclass(frozen=True)
class BannerReviewApplication:
    """可在事务提交和会话关闭后使用的申请审核快照。"""

    id: int
    """申请记录的数据库主键，区别于帖子或频道的 Discord ID"""

    thread_id: int
    """申请展示的目标 Discord ID；根据 target_type 表示帖子或频道"""

    channel_id: int
    """帖子所属频道 ID；频道型申请为目标频道 ID，不代表展示范围"""

    applicant_id: int
    """申请人的 Discord 用户 ID，用于用户限制及审核结果通知"""

    cover_image_url: str | None
    """自定义封面 URL；帖子型申请为 None 时动态使用帖子当前首图"""

    target_scope: str
    """展示范围：global 表示全频道，其他值为具体频道 ID 字符串"""

    target_type: int
    """目标类型：1 表示论坛帖子，2 表示频道，对应 TargetType 枚举"""

    status: str
    """申请状态：pending、approved 或 rejected，对应 ApplicationStatus 枚举"""

    applied_at: datetime
    """申请提交时间"""

    reviewed_at: datetime | None
    """审核时间（UTC）；尚未审核时为 None"""

    reviewer_id: int | None
    """审核员的 Discord 用户 ID；自动拒绝沿用触发批准的审核员，未审核时为 None"""

    reject_reason: str | None
    """拒绝理由；自动拒绝时包含触发批准的申请 ID，未被拒绝时为 None"""

    review_message_id: int | None
    """原审核消息的 Discord ID，用于更新结果及移除按钮；尚未回填时为 None"""

    review_thread_id: int | None
    """原审核消息所在 Discord 子区的 ID；尚未回填时为 None"""

    @classmethod
    def from_application(
        cls, application: BannerApplication
    ) -> "BannerReviewApplication":
        """在会话内复制审核和消息投递所需字段。"""
        # 快照不保留 ORM 引用，主键应在创建申请时完成刷新。
        if application.id is None:
            raise ValueError("申请数据异常")
        return cls(
            **{name: getattr(application, name) for name in cls.__dataclass_fields__}
        )
