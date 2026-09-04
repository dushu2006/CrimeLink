"""source_references: exact provenance coordinates for evidence navigation

Revision ID: c1f2a3b4d005
Revises: b7e6f1a9c002
Create Date: 2026-09-04T10:00:00+05:30

Adds the table that lets CrimeLink answer "which row of which original file
produced this?" — the question `case_documents` cannot answer whenever the
ingested document is derived from a corpus table rather than uploaded verbatim.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f2a3b4d005"
down_revision: str | None = "b7e6f1a9c002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = (
        sa.dialects.postgresql.JSONB if bind.dialect.name == "postgresql" else sa.JSON
    )
    op.create_table(
        "source_references",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("doc_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("origin_file", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("record_id", sa.String(120), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("field_names", json_type(), nullable=False),
        sa.Column("field_values", json_type(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("text_start", sa.Integer(), nullable=True),
        sa.Column("text_end", sa.Integer(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_source_references"),
        sa.ForeignKeyConstraint(
            ["doc_id"], ["case_documents.id"],
            name="fk_source_references_doc_id_case_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "doc_id", "origin_file", "row_number", "text_start",
            name="uq_source_references_position",
        ),
    )
    op.create_index("ix_source_references_doc_id", "source_references", ["doc_id"])
    op.create_index("ix_source_references_case_id", "source_references", ["case_id"])
    op.create_index("ix_source_references_record_id", "source_references", ["record_id"])
    op.create_index(
        "ix_source_references_origin", "source_references", ["origin_file", "row_number"]
    )

    # Adapter-supplied provenance that cannot be derived from the stored bytes.
    op.add_column(
        "case_documents",
        sa.Column("source_metadata", json_type(), nullable=True),
    )
    op.execute("UPDATE case_documents SET source_metadata = '{}' WHERE source_metadata IS NULL")


def downgrade() -> None:
    op.drop_column("case_documents", "source_metadata")
    op.drop_index("ix_source_references_origin", table_name="source_references")
    op.drop_index("ix_source_references_record_id", table_name="source_references")
    op.drop_index("ix_source_references_case_id", table_name="source_references")
    op.drop_index("ix_source_references_doc_id", table_name="source_references")
    op.drop_table("source_references")
