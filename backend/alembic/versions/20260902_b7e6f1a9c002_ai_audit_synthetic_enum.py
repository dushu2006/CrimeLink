"""extend enums for ai audit + synthetic source confidence

Revision ID: b7e6f1a9c002
Revises: a5dcb473a661
Create Date: 2026-09-02T09:20:00+05:30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = 'b7e6f1a9c002'
down_revision: str | None = 'a5dcb473a661'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The two enums extended by the production-grade upgrade:
# 1. audit_action_type gains 'AI_QUERY' (every AI request is audited).
# 2. source_confidence gains 'SYNTHETIC' (explicit tag for dev corpus records).
# Both enums were created with native_enum=False (CHECK constraint) for portability,
# but on Postgres the existing CHECK constraints must be extended.

_audit_values_old = (
    "'LOGIN','LOGIN_FAILED','SEARCH','GRAPH_EXPAND','DOC_VIEW','DOC_UPLOAD',"
    "'MERGE','MERGE_REJECT','PATTERN_REVIEW','EXPORT','ACCESS_REQUEST',"
    "'ACCESS_APPROVAL','QUARANTINE_RELEASE','CONFIG_CHANGE'"
)
_audit_values_new = _audit_values_old + ",'AI_QUERY'"

_sc_values_old = "'VERIFIED','UNVERIFIED','ANONYMOUS_TIP'"
_sc_values_new = _sc_values_old + ",'SYNTHETIC'"


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _is_sqlite() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "sqlite"


def upgrade() -> None:
    if _is_postgres():
        # Postgres: drop the check constraint and re-add it with the new value.
        op.execute(
            f"ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_action_type;"
        )
        op.execute(
            f"ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_action_type "
            f"CHECK (action_type IN ({_audit_values_new}));"
        )
        op.execute(
            f"ALTER TABLE case_documents DROP CONSTRAINT IF EXISTS ck_case_documents_source_confidence;"
        )
        op.execute(
            f"ALTER TABLE case_documents ADD CONSTRAINT ck_case_documents_source_confidence "
            f"CHECK (source_confidence IN ({_sc_values_new}));"
        )
    # SQLite (native_enum=False, CHECK-less in our default config): no migration
    # is needed — values are stored as text and SQLAlchemy enforces at the app layer.


def downgrade() -> None:
    if _is_postgres():
        # First delete any rows that use the new values so the old CHECK passes.
        op.execute("DELETE FROM audit_logs WHERE action_type = 'AI_QUERY';")
        op.execute("DELETE FROM case_documents WHERE source_confidence = 'SYNTHETIC';")
        op.execute(
            f"ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_action_type;"
        )
        op.execute(
            f"ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_logs_action_type "
            f"CHECK (action_type IN ({_audit_values_old}));"
        )
        op.execute(
            f"ALTER TABLE case_documents DROP CONSTRAINT IF EXISTS ck_case_documents_source_confidence;"
        )
        op.execute(
            f"ALTER TABLE case_documents ADD CONSTRAINT ck_case_documents_source_confidence "
            f"CHECK (source_confidence IN ({_sc_values_old}));"
        )
