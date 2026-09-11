"""investigation threads + INVESTIGATE audit action

Revision ID: f4c5d6e7f008
Revises: e3b4c5d6f007
Create Date: 2026-09-12T00:00:00+05:30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'f4c5d6e7f008'
down_revision: str | None = 'e3b4c5d6f007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_audit_values_old = (
    "'LOGIN','LOGIN_FAILED','SEARCH','GRAPH_EXPAND','DOC_VIEW','DOC_UPLOAD',"
    "'MERGE','MERGE_REJECT','PATTERN_REVIEW','EXPORT','ACCESS_REQUEST',"
    "'ACCESS_APPROVAL','QUARANTINE_RELEASE','CONFIG_CHANGE','AI_QUERY'"
)
_audit_values_new = _audit_values_old + ",'INVESTIGATE'"


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "investigation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), nullable=False, index=True),
        sa.Column("case_id", sa.String(36), nullable=True, index=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="master"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    if _is_postgres():
        op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_action_type;")
        op.execute(
            "ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_action_type "
            f"CHECK (action_type IN ({_audit_values_new}));"
        )


def downgrade() -> None:
    # INVESTIGATE rows must go first: the old CHECK would reject them.
    op.execute("DELETE FROM audit_logs WHERE action_type = 'INVESTIGATE'")
    if _is_postgres():
        op.execute("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_action_type;")
        op.execute(
            "ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_action_type "
            f"CHECK (action_type IN ({_audit_values_old}));"
        )
    op.drop_table("investigation_sessions")
