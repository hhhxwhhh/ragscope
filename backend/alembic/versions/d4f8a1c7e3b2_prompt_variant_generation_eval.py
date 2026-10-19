"""add prompt variants and optional generation evaluation

Revision ID: d4f8a1c7e3b2
Revises: a1d6f3c92b47
Create Date: 2026-10-19 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8a1c7e3b2"
down_revision: str | Sequence[str] | None = "a1d6f3c92b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("prompt_variant", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "eval_runs",
        sa.Column(
            "include_generation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "eval_runs",
        sa.Column("prompt_variant", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "eval_results",
        sa.Column("answer", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_results", "answer")
    op.drop_column("eval_runs", "prompt_variant")
    op.drop_column("eval_runs", "include_generation")
    op.drop_column("messages", "prompt_variant")
