"""补齐频道目录资料，为 API 独立启动准备持久化数据。"""

import sqlalchemy as sa

from alembic import op

revision = "add_channel_metadata"
down_revision = "add_banner_ownership"
branch_labels = None
depends_on = None


def upgrade():
    """新增可空元数据字段，由过渡期 BOT 补齐历史记录。"""
    # 不推断旧频道类型和名称，保持现有 Banner 数据兼容。
    op.add_column("channel", sa.Column("guild_name", sa.String(), nullable=True))
    op.add_column("channel", sa.Column("category_name", sa.String(), nullable=True))
    op.add_column("channel", sa.Column("channel_type", sa.Integer(), nullable=True))
    op.add_column("channel", sa.Column("synced_at", sa.DateTime(), nullable=True))


def downgrade():
    """移除新增资料字段，保留频道及 Banner 记录。"""
    # 仅撤销本次扩展，不删除已有频道。
    for name in ("synced_at", "channel_type", "category_name", "guild_name"):
        op.drop_column("channel", name)
