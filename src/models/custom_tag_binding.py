from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, text
from sqlmodel import Field, SQLModel

from shared.time_utils import utc_now


class CustomTagBinding(SQLModel, table=True):
    """保存自定义标签的一轮绑定及汇总票数，解绑后保留历史。"""

    __tablename__ = "custom_tag_binding"

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="本轮挂标记录的内部主键；重新挂标会创建新的轮次 ID",
    )
    """本轮挂标记录的内部主键；重新挂标会创建新的轮次 ID"""

    target_type: str = Field(
        index=True, description="绑定目标类型：thread 为帖子，booklist 为书单"
    )
    """绑定目标类型：thread 为帖子，booklist 为书单"""

    target_id: int = Field(
        sa_column=Column(BigInteger, nullable=False, index=True),
        description="绑定目标 ID：帖子使用 Discord 帖子 ID，书单使用内部书单 ID",
    )
    """绑定目标 ID：帖子使用 Discord 帖子 ID，书单使用内部书单 ID"""

    tag_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("tag.id"), nullable=False, index=True),
        description="绑定的自定义标签内部 ID",
    )
    """绑定的自定义标签内部 ID"""

    actor_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="执行挂标的 Discord 用户 ID；自动超时挂标时为 0",
    )
    """执行挂标的 Discord 用户 ID；自动超时挂标时为 0"""

    created_at: datetime = Field(
        default_factory=utc_now, description="本轮挂标创建时间（UTC）"
    )
    """本轮挂标创建时间（UTC）"""

    ended_at: datetime | None = Field(
        default=None, description="本轮解绑时间（UTC）；为空表示当前仍生效"
    )
    """本轮解绑时间（UTC）；为空表示当前仍生效"""

    end_reason: str | None = Field(
        default=None,
        description="解绑原因：manual 为手动移除，downvote 为投票下标，tag_deleted 为标签软删除",
    )
    """解绑原因：manual 为手动移除，downvote 为投票下标，tag_deleted 为标签软删除"""

    upvotes: int = Field(
        default=0, description="本轮当前正向票数；仅统计有效赞票，重新挂标从零开始"
    )
    """本轮当前正向票数；仅统计有效赞票，重新挂标从零开始"""

    downvotes: int = Field(
        default=0, description="本轮当前负向票数；负向票减正向票大于 5 时自动解绑"
    )
    """本轮当前负向票数；负向票减正向票大于 5 时自动解绑"""

    # 同一目标和标签仅允许一轮生效绑定，已结束的轮次保留为历史。
    __table_args__ = (
        Index(
            "uq_active_custom_tag",
            "target_type",
            "target_id",
            "tag_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        CheckConstraint(
            "target_type IN ('thread', 'booklist') AND upvotes >= 0 AND downvotes >= 0",
            name="ck_custom_tag_binding",
        ),
    )
