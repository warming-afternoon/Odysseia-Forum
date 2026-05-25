"""add tournament system

Revision ID: add_tournament_system
Revises: add_booklist_is_anonymous
Create Date: 2026-05-25 19:22:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "add_tournament_system"
down_revision: Union[str, Sequence[str], None] = "add_booklist_is_anonymous"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "booklist",
        sa.Column(
            "is_tournament", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        op.f("ix_booklist_is_tournament"), "booklist", ["is_tournament"], unique=False
    )
    op.add_column(
        "booklist",
        sa.Column("tournament_channel_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_booklist_tournament_channel_id"),
        "booklist",
        ["tournament_channel_id"],
        unique=True,
    )
    op.add_column(
        "booklist_item",
        sa.Column("tournament_participated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("booklist_item", "tournament_participated_at")
    op.drop_index(op.f("ix_booklist_tournament_channel_id"), table_name="booklist")
    op.drop_column("booklist", "tournament_channel_id")
    op.drop_index(op.f("ix_booklist_is_tournament"), table_name="booklist")
    op.drop_column("booklist", "is_tournament")
