"""为新审核通过的 Banner 保存申请及申请人归属，旧记录不回填。"""

import sqlalchemy as sa
from alembic import op

revision = "add_banner_ownership"
down_revision = "add_abyss_tag_flag"
branch_labels = None
depends_on = None


def upgrade():
    """增加可空归属字段及关联，保留旧记录的过渡期。"""
    # 两张表采用相同归属结构，迁移不推断历史申请人。
    for table in ("banner_carousel", "banner_waitlist"):
        op.add_column(table, sa.Column("application_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("applicant_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_application_id",
            table,
            "banner_application",
            ["application_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_applicant_id", table, ["applicant_id"])


def downgrade():
    """移除归属字段及关联，不改变原有 Banner 内容。"""
    for table in ("banner_waitlist", "banner_carousel"):
        op.drop_index(f"ix_{table}_applicant_id", table_name=table)
        op.drop_constraint(f"fk_{table}_application_id", table, type_="foreignkey")
        op.drop_column(table, "applicant_id")
        op.drop_column(table, "application_id")
