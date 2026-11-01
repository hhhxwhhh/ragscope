"""knowledge graph entities, relations and chunk mentions

Revision ID: a9e4b7c1d2f8
Revises: f5a7c1d9e3b4
Create Date: 2026-10-31 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9e4b7c1d2f8"
down_revision: str | Sequence[str] | None = "f5a7c1d9e3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column(
            "entity_type", sa.String(length=24), server_default="concept", nullable=False
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mention_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_graph_entities_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_entities")),
        sa.UniqueConstraint("kb_id", "normalized_name", name="uq_graph_entities_kb_name"),
    )
    op.create_index(op.f("ix_graph_entities_kb_id"), "graph_entities", ["kb_id"], unique=False)
    op.create_index(
        "ix_graph_entities_kb_type", "graph_entities", ["kb_id", "entity_type"], unique=False
    )

    op.create_table(
        "graph_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("source_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_graph_relations_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["graph_entities.id"],
            name=op.f("fk_graph_relations_source_id_graph_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["graph_entities.id"],
            name=op.f("fk_graph_relations_target_id_graph_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_chunk_id"],
            ["chunks.id"],
            name=op.f("fk_graph_relations_source_chunk_id_chunks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_relations")),
        sa.UniqueConstraint(
            "kb_id", "source_id", "target_id", "relation", name="uq_graph_relations_edge"
        ),
    )
    op.create_index(
        op.f("ix_graph_relations_kb_id"), "graph_relations", ["kb_id"], unique=False
    )
    op.create_index(
        op.f("ix_graph_relations_source_id"), "graph_relations", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_graph_relations_target_id"), "graph_relations", ["target_id"], unique=False
    )
    op.create_index(
        "ix_graph_relations_kb_source", "graph_relations", ["kb_id", "source_id"], unique=False
    )

    op.create_table(
        "chunk_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_entities_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["graph_entities.id"],
            name=op.f("fk_chunk_entities_entity_id_graph_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_entities")),
        sa.UniqueConstraint("chunk_id", "entity_id", name="uq_chunk_entities_pair"),
    )
    op.create_index(
        op.f("ix_chunk_entities_chunk_id"), "chunk_entities", ["chunk_id"], unique=False
    )
    op.create_index(
        op.f("ix_chunk_entities_entity_id"), "chunk_entities", ["entity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chunk_entities_entity_id"), table_name="chunk_entities")
    op.drop_index(op.f("ix_chunk_entities_chunk_id"), table_name="chunk_entities")
    op.drop_table("chunk_entities")

    op.drop_index("ix_graph_relations_kb_source", table_name="graph_relations")
    op.drop_index(op.f("ix_graph_relations_target_id"), table_name="graph_relations")
    op.drop_index(op.f("ix_graph_relations_source_id"), table_name="graph_relations")
    op.drop_index(op.f("ix_graph_relations_kb_id"), table_name="graph_relations")
    op.drop_table("graph_relations")

    op.drop_index("ix_graph_entities_kb_type", table_name="graph_entities")
    op.drop_index(op.f("ix_graph_entities_kb_id"), table_name="graph_entities")
    op.drop_table("graph_entities")
