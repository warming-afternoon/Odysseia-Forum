"""add booklist is_anonymous

Revision ID: add_booklist_is_anonymous
Revises: 41854c02b42e
Create Date: 2026-05-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_booklist_is_anonymous"
down_revision: Union[str, Sequence[str], None] = "41854c02b42e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "booklist",
        sa.Column(
            "is_anonymous", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        op.f("ix_booklist_is_anonymous"), "booklist", ["is_anonymous"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_booklist_is_anonymous"), table_name="booklist")
    op.drop_column("booklist", "is_anonymous")
