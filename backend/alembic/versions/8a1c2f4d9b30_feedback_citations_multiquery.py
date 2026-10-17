"""feedback, citation stats, page numbers and multi-query traces

Revision ID: 8a1c2f4d9b30
Revises: 317307eb3c02
Create Date: 2026-10-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8a1c2f4d9b30"
down_revision: str | Sequence[str] | None = "317307eb3c02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("citation_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("citations", sa.Column("page_number", sa.Integer(), nullable=True))
    op.add_column(
        "retrieval_traces",
        sa.Column(
            "sub_queries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "retrieval_traces",
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.create_table(
        "message_feedbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
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
        sa.CheckConstraint("rating IN (-1, 1)", name="ck_message_feedbacks_rating"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_message_feedbacks_message"),
    )
    op.create_index(
        "ix_message_feedbacks_message_id", "message_feedbacks", ["message_id"]
    )
    op.create_index("ix_message_feedbacks_user_id", "message_feedbacks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_message_feedbacks_user_id", table_name="message_feedbacks")
    op.drop_index("ix_message_feedbacks_message_id", table_name="message_feedbacks")
    op.drop_table("message_feedbacks")
    op.drop_column("retrieval_traces", "cache_hit")
    op.drop_column("retrieval_traces", "sub_queries")
    op.drop_column("citations", "page_number")
    op.drop_column("messages", "citation_stats")
    op.drop_column("documents", "page_count")
