"""retrieval trace stage payload

Adds one JSONB column to ``retrieval_traces`` holding the per-stage payloads that
do not have their own column: graph, crag, compress and the candidate counts.

Why one column instead of four: these payloads grow a key at a time as retrieval
gains stages, and every shape change would otherwise be another migration. One
"extras" blob keeps the schema stable while the trace keeps expanding, which is
the same trade-off already made for ``config`` and ``candidates``.

Revision ID: a2c7e9f3b145
Revises: d8b4f2c6a9e3
Create Date: 2026-10-31 22:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2c7e9f3b145"
down_revision: str | Sequence[str] | None = "d8b4f2c6a9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrieval_traces",
        sa.Column(
            "stage_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("retrieval_traces", "stage_payload")
