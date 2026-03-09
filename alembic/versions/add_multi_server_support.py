"""add multi-server support

Revision ID: add_multi_server
Revises: 3f8f2d284f85
Create Date: 2026-03-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "add_multi_server"
down_revision = "3f8f2d284f85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 为 thread 表添加 guild_id 列
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "guild_id",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index("ix_thread_guild_id", ["guild_id"])

    # 2. 重建 usersearchpreferences 表以支持复合主键
    #    SQLite 不支持修改主键，所以使用 batch 模式重建表
    with op.batch_alter_table(
        "usersearchpreferences",
        schema=None,
        recreate="always",
        naming_convention={
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        },
    ) as batch_op:
        batch_op.add_column(
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "guild_id",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        # 旧表用 user_id 做主键，重建后用 id 做主键
        # 并创建 (user_id, guild_id) 唯一约束

    # 清除 server_default (仅作迁移用途)
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.alter_column("guild_id", server_default=None)

    # 创建唯一约束索引
    op.create_index(
        "ix_usersearchpreferences_guild_id",
        "usersearchpreferences",
        ["guild_id"],
    )
    op.create_index(
        "uq_user_guild_preferences",
        "usersearchpreferences",
        ["user_id", "guild_id"],
        unique=True,
    )


def downgrade() -> None:
    # 移除 usersearchpreferences 的 guild_id 列并恢复旧主键
    op.drop_index(
        "uq_user_guild_preferences", table_name="usersearchpreferences"
    )
    op.drop_index(
        "ix_usersearchpreferences_guild_id", table_name="usersearchpreferences"
    )
    with op.batch_alter_table(
        "usersearchpreferences", schema=None, recreate="always"
    ) as batch_op:
        batch_op.drop_column("guild_id")
        batch_op.drop_column("id")

    # 移除 thread 表的 guild_id 列
    with op.batch_alter_table("thread", schema=None) as batch_op:
        batch_op.drop_index("ix_thread_guild_id")
        batch_op.drop_column("guild_id")
