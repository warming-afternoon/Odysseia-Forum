"""跨平台作品的统一内容投影。"""

from datetime import datetime
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    JSON,
    Text,
    Index,
    event,
    inspect,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from shared.text_utils import build_search_vector_text
from sqlmodel import SQLModel, Field
from shared.time_utils import utc_now


class Work(SQLModel, table=True):
    """保存作品内容与主来源，不独立存储封面。"""

    __table_args__ = (
        CheckConstraint(
            "(rules_version = 0 AND rules_body = '' AND rules_updated_at IS NULL) OR "
            "(rules_version > 0 AND rules_updated_at IS NOT NULL)",
            name="ck_work_rules_state",
        ),
        CheckConstraint("rating IN ('general','r18','r18g')", name="ck_work_rating"),
        Index("ix_work_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_work_visible_created_id",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL AND source_visible IS TRUE"),
        ),
        Index(
            "ix_work_author_created_id",
            "author_id",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
        ),
    )

    search_vector: str | None = Field(
        default=None, sa_column=Column(TSVECTOR, nullable=True)
    )
    """与既有搜索相同的 rjieba simple 全文向量。"""

    last_active_at: datetime | None = Field(default=None, index=True)
    """所有来源帖子最新活跃时间。"""

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    """作品内部编号，对外序列化为字符串。"""

    title: str
    """作品标题。"""

    description: str | None = None
    """作品简介。"""

    summary: str | None = Field(default=None, max_length=200)
    """作者单独填写的列表摘要，未填写时列表截取作品正文。"""

    rating: str | None = None
    """作品的显式内容分级；旧作品可为空，继续使用来源分区及标签限制。"""

    layout: dict | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True))
    )
    """作者指定的作品页布局，空值使用默认布局。"""

    declarations: dict | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True))
    )
    """首次发布时的答题者、条款版本、确认时间及答案快照；旧来源作品为空。"""

    published_at: datetime | None = None
    """原生作品首次成功发布的时间，发布前为空。"""

    updated_at: datetime | None = None
    """作品资料最近一次编辑时间，独立于讨论活跃时间。"""

    chat_title: str | None = Field(default=None, max_length=60)
    """作品交流区的自定义标题，空值表示跟随作品标题。"""

    rules_body: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    """作者或管理者维护的当前作品规则正文。"""

    rules_version: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default="0"))
    """规则保存次数，零表示从未设置规则。"""

    rules_updated_at: datetime | None = None
    """作品规则最后一次成功保存的 UTC 时间。"""

    classification_revision: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False)
    )
    """作品期望分类的单调版本，异步移动任务据此排序。"""

    source_visible: bool = True
    """来源暂不可见时隐藏作品，但不伪造成权威删除。"""

    metadata_overrides: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    """用户显式覆盖的作品元数据，不被来源空快照清除。"""

    images: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    """作品图片，首图为展示封面。"""

    author_id: str | None = Field(default=None, index=True)
    """统一 Fluxer 作者编号。"""

    source_platform: str = "discord"
    """主来源平台。"""

    source_guild_id: str = ""
    """主来源服务器编号。"""

    source_channel_id: str = ""
    """主来源频道编号。"""

    source_thread_id: str = ""
    """主来源帖子编号。"""

    main_ref: str = ""
    """主来源链接。"""

    is_nsfw: bool = Field(default=False, index=True)
    """是否为成人分区。"""

    is_abyss: bool = Field(default=False, index=True)
    """是否为深渊分区。"""

    created_at: datetime = Field(default_factory=utc_now, index=True)
    """作品创建时间。"""

    deleted_at: datetime | None = Field(default=None, index=True)
    """软删除时间。"""

    deleted_user_id: str | None = None
    """执行本次作品软删除的操作者平台用户 ID，来源未提供时为空。"""

    deletion_reason: str | None = None
    """原生主帖删除不可恢复，其余为可管理恢复的隐藏。"""


@event.listens_for(Work, "before_insert")
@event.listens_for(Work, "before_update")
def update_work_search_vector(mapper, connection, target):
    """标题、正文或手写摘要变化时按 Forum 的分词规则更新作品索引。"""
    state = inspect(target)
    if state.persistent and not (
        state.attrs.title.history.has_changes()
        or state.attrs.description.history.has_changes()
        or state.attrs.summary.history.has_changes()
    ):
        return
    searchable_text = " ".join(
        part for part in (target.description, target.summary) if part
    )
    tokens = build_search_vector_text(target.title, searchable_text)
    target.search_vector = func.to_tsvector("simple", tokens) if tokens else None
