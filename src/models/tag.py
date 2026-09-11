from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger, CheckConstraint, Index, text
from sqlmodel import Column, Field, Relationship, SQLModel

from models import ThreadTagLink

if TYPE_CHECKING:
    from models import TagVote, Thread


class Tag(SQLModel, table=True):
    """统一保存原生与自定义标签实体及生命周期状态。"""

    # 自定义标准名的唯一性包含软删除记录，避免重建实体绕过治理历史。
    __table_args__ = (
        Index(
            "uq_custom_tag_category_name",
            "category",
            "name",
            unique=True,
            postgresql_where=text("source = 'custom'"),
        ),
        CheckConstraint(
            "(source = 'custom' AND category IS NOT NULL AND category BETWEEN 1 AND 7 AND discord_tag_id IS NULL) OR (source = 'discord' AND category IS NULL)",
            name="ck_tag_source_category",
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="本项目内部标签主键，由正数 BIGINT 序列生成；与 Discord 标签 ID 分离",
    )
    """本项目内部标签主键，由正数 BIGINT 序列生成；与 Discord 标签 ID 分离"""

    name: str = Field(
        index=True, description="标签名称；自定义标签保存不含分类前缀的标准名"
    )
    """标签名称；自定义标签保存不含分类前缀的标准名"""

    source: str = Field(
        default="discord",
        index=True,
        description="标签来源：discord 为 DC 原生标签，custom 为自定义标签",
    )
    """标签来源：discord 为 DC 原生标签，custom 为自定义标签"""

    discord_tag_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, unique=True),
        description="DC 原生标签的唯一 Discord ID，用于同步定位；自定义标签为空",
    )
    """DC 原生标签的唯一 Discord ID，用于同步定位；自定义标签为空"""

    category: int | None = Field(
        default=None,
        description="自定义分类：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法；原生标签为空",
    )
    """自定义分类：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法；原生标签为空"""

    enabled: bool = Field(
        default=True,
        description="是否允许新增绑定和提议；停用后已有绑定仍保留展示、搜索与投票",
    )
    """是否允许新增绑定和提议；停用后已有绑定仍保留展示、搜索与投票"""

    deleted_at: datetime | None = Field(
        default=None,
        description="软删除时间（UTC）；为空表示未删除，恢复实体时不恢复旧绑定",
    )
    """软删除时间（UTC）；为空表示未删除，恢复实体时不恢复旧绑定"""

    threads: List["Thread"] = Relationship(
        back_populates="tags",
        sa_relationship_kwargs={
            "primaryjoin": "Tag.id == ThreadTagLink.tag_id",
            "secondaryjoin": "Thread.id == ThreadTagLink.thread_id",
            "secondary": ThreadTagLink.__table__,
        },
    )
    """通过原生标签关联表连接的帖子；自定义绑定由 CustomTagBinding 独立记录"""

    votes: List["TagVote"] = Relationship(
        back_populates="tag",
        sa_relationship_kwargs={
            "primaryjoin": "Tag.id == TagVote.tag_id",
            "foreign_keys": "[TagVote.tag_id]",
        },
    )
    """原有原生标签投票关系；自定义标签投票由 CustomTagVote 独立记录"""
