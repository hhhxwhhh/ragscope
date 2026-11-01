"""add faithfulness report column to messages

Revision ID: b1f3a8c7d204
Revises: a2c7e9f3b145
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1f3a8c7d204"
down_revision: str | None = "a2c7e9f3b145"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "faithfulness",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "faithfulness")
