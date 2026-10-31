"""semantic answer cache

Revision ID: c6f2a9d4e8b1
Revises: b3d8e5f1a7c2
Create Date: 2026-10-31 21:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6f2a9d4e8b1"
down_revision: str | Sequence[str] | None = "b3d8e5f1a7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_cache_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("prompt_variant", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized_question", sa.String(length=500), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("hits", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_hit_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_semantic_cache_entries_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_cache_entries")),
        sa.UniqueConstraint(
            "kb_id",
            "index_version",
            "prompt_variant",
            "normalized_question",
            name="uq_semantic_cache_key",
        ),
    )
    op.create_index(
        op.f("ix_semantic_cache_entries_kb_id"),
        "semantic_cache_entries",
        ["kb_id"],
        unique=False,
    )
    op.create_index(
        "ix_semantic_cache_lookup",
        "semantic_cache_entries",
        ["kb_id", "index_version", "prompt_variant"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_cache_lookup", table_name="semantic_cache_entries")
    op.drop_index(op.f("ix_semantic_cache_entries_kb_id"), table_name="semantic_cache_entries")
    op.drop_table("semantic_cache_entries")
