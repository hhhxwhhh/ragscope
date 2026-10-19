"""human answer review queue

Revision ID: e7b2c9a4d610
Revises: d4f8a1c7e3b2
Create Date: 2026-10-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7b2c9a4d610"
down_revision: str | Sequence[str] | None = "d4f8a1c7e3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column(
            "issue_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('pass', 'needs_fix', 'fail')",
            name="ck_answer_reviews_verdict",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_answer_reviews_message"),
    )
    op.create_index("ix_answer_reviews_message_id", "answer_reviews", ["message_id"])
    op.create_index("ix_answer_reviews_reviewer_id", "answer_reviews", ["reviewer_id"])


def downgrade() -> None:
    op.drop_index("ix_answer_reviews_reviewer_id", table_name="answer_reviews")
    op.drop_index("ix_answer_reviews_message_id", table_name="answer_reviews")
    op.drop_table("answer_reviews")
