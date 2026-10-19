"""add expected_mode to eval cases

Revision ID: a1d6f3c92b47
Revises: c4d81e7a2f90
Create Date: 2026-10-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1d6f3c92b47"
down_revision: str | Sequence[str] | None = "c4d81e7a2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_cases",
        sa.Column("expected_mode", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("eval_cases", "expected_mode")
