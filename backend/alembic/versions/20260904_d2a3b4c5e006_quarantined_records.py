"""Quarantine corpus rows that cannot be attached to any case.

The corpus adapter builds per-case documents, so a row it cannot route to a
case never becomes one.  Those rows were counted in a scan warning and then
dropped, leaving no record of which rows were missing or why.  This table keeps
them with enough coordinates to reopen the original record.

Revision ID: d2a3b4c5e006
Revises: c1f2a3b4d005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d2a3b4c5e006"
down_revision: str | None = "c1f2a3b4d005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "quarantined_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("origin_file", sa.Text(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("record_id", sa.String(length=120), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("unresolved_case_id", sa.String(length=64), nullable=True),
        sa.Column("field_values", sa.JSON(), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=True),
        sa.Column("import_run_id", sa.String(length=64), nullable=True),
        sa.Column(
            "resolved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "origin_file",
            "row_number",
            "record_id",
            name="uq_quarantined_records_position",
        ),
    )
    op.create_index(
        "ix_quarantined_records_record_id", "quarantined_records", ["record_id"]
    )
    op.create_index(
        "ix_quarantined_records_reason_code", "quarantined_records", ["reason_code"]
    )
    op.create_index(
        "ix_quarantined_records_import_run_id",
        "quarantined_records",
        ["import_run_id"],
    )
    op.create_index(
        "ix_quarantined_records_reason",
        "quarantined_records",
        ["source_type", "reason_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_quarantined_records_reason", table_name="quarantined_records")
    op.drop_index(
        "ix_quarantined_records_import_run_id", table_name="quarantined_records"
    )
    op.drop_index(
        "ix_quarantined_records_reason_code", table_name="quarantined_records"
    )
    op.drop_index("ix_quarantined_records_record_id", table_name="quarantined_records")
    op.drop_table("quarantined_records")
