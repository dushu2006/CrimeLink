"""Add investigation workflow tables (stage runs + findings).

Revision ID: e3b4c5d6f007
Revises: d2a3b4c5e006
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = "e3b4c5d6f007"
down_revision = "d2a3b4c5e006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_stage_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False, index=True),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"]),
    )
    op.create_table(
        "investigation_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False, index=True),
        sa.Column("finding_type", sa.String(48), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(8), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("entity_keys", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("investigation_findings")
    op.drop_table("investigation_stage_runs")
