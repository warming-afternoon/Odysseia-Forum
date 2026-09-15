from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger, CheckConstraint, Index, text
from sqlmodel import Column, Field, Relationship, SQLModel


if TYPE_CHECKING:
    from models import Thread


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
            "(source = 'custom' AND ((category BETWEEN 1 AND 7 AND category IS NOT NULL) OR (category IS NULL AND discord_tag_id IS NOT NULL))) OR (source = 'discord' AND category IS NULL AND discord_tag_id IS NOT NULL)",
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
        description="DC 原生标签的唯一 Discord ID，用于同步定位；转换标签保留原始 ID",
    )
    """DC 原生标签的唯一 Discord ID，用于同步定位；转换标签保留原始 ID"""

    discord_channel_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, index=True),
        description="原始 DC 来源频道 ID，转换后保留用于溯源",
    )
    """标签来源频道；不因转为自定义实体而清空"""

    discord_synced_at: datetime | None = Field(
        default=None, description="最近一次完整频道同步确认时间（UTC）"
    )
    """用于识别已完成同步及离线补偿的时间"""

    category: int | None = Field(
        default=None,
        description="自定义分类：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法；原生及未分类转换标签为空",
    )
    """自定义分类：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法；原生及未分类转换标签为空"""

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
        sa_relationship_kwargs={
            "secondary": "tag_binding",
            "primaryjoin": "and_(Tag.id == foreign(TagBinding.tag_id), Tag.deleted_at.is_(None))",
            "secondaryjoin": "and_(Thread.id == foreign(TagBinding.target_id), TagBinding.target_type == 'thread', TagBinding.ended_at.is_(None))",
            "viewonly": True,
        },
    )
    """通过统一有效绑定查询帖子；迁移备份表不参与 ORM 关系"""
