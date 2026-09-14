"""Industry foundation: classification, custody and reviewable investigation work.

This migration adds the durable records needed for evidence-grounded
investigation workflows without replacing the existing graph or pipeline.

Revision ID: 7a8b9c0d001
Revises: f4c5d6e7f008
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "7a8b9c0d001"
down_revision = "f4c5d6e7f008"
branch_labels = None
depends_on = None


def _enum(name: str, values: list[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _upgrade_existing_checks() -> None:
    role_values = "'VIEWER','INVESTIGATOR','SUPERVISOR','FORENSIC_ANALYST','FINANCIAL_ANALYST','INTELLIGENCE_ANALYST','AUDITOR','STATION_ADMIN','DISTRICT_ADMIN','SUPER_ADMIN','ADMIN'"
    case_values = "'DRAFT','OPEN','ACTIVE_INVESTIGATION','UNDER_REVIEW','SUBMITTED','CLOSED','SEALED'"
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role")
        op.execute(f"ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ({role_values}))")
        op.execute("ALTER TABLE cases DROP CONSTRAINT IF EXISTS ck_cases_case_status")
        op.execute(f"ALTER TABLE cases ADD CONSTRAINT ck_cases_case_status CHECK (status IN ({case_values}))")
    elif bind.dialect.name == "sqlite":
        # SQLite cannot alter a CHECK in place; batch mode performs the safe
        # copy-and-move migration while preserving existing rows.
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_constraint("role", type_="check")
            batch.create_check_constraint("role", f"role IN ({role_values})")
        with op.batch_alter_table("cases", recreate="always") as batch:
            batch.drop_constraint("case_status", type_="check")
            batch.create_check_constraint("case_status", f"status IN ({case_values})")


def upgrade() -> None:
    # Classification is enforced in the API and persisted in the relational
    # record.  Server defaults make this migration safe for existing rows.
    op.add_column(
        "users",
        sa.Column(
            "max_classification",
            _enum("user_max_classification", ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "SECRET", "HIGHLY_RESTRICTED"]),
            nullable=False,
            server_default="CONFIDENTIAL",
        ),
    )
    op.add_column(
        "cases",
        sa.Column(
            "classification",
            _enum("case_classification", ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "SECRET", "HIGHLY_RESTRICTED"]),
            nullable=False,
            server_default="INTERNAL",
        ),
    )
    op.add_column(
        "case_documents",
        sa.Column(
            "classification",
            _enum("evidence_classification", ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "SECRET", "HIGHLY_RESTRICTED"]),
            nullable=False,
            server_default="CONFIDENTIAL",
        ),
    )
    _upgrade_existing_checks()

    op.create_table(
        "evidence_custody_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("event_type", _enum("custody_event_type", ["COLLECTED", "IMPORTED", "HASH_VERIFIED", "STORED", "ACCESSED", "DOWNLOADED", "DERIVED", "SHARED", "EXPORTED", "SEALED"]), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("object_hash", sa.String(64), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["case_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.create_index("ix_custody_evidence", "evidence_custody_events", ["evidence_id"])
    op.create_index("ix_custody_case", "evidence_custody_events", ["case_id"])
    op.create_index("ix_custody_case_time", "evidence_custody_events", ["case_id", "created_at"])

    op.create_table(
        "investigation_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=True),
        sa.Column("creator_id", sa.String(36), nullable=False),
        sa.Column("priority", _enum("task_priority", ["LOW", "MEDIUM", "HIGH", "CRITICAL"]), nullable=False),
        sa.Column("status", _enum("task_status", ["TODO", "IN_PROGRESS", "BLOCKED", "PENDING_REVIEW", "COMPLETED", "CANCELLED"]), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("linked_evidence", sa.JSON(), nullable=False),
        sa.Column("linked_entities", sa.JSON(), nullable=False),
        sa.Column("linked_findings", sa.JSON(), nullable=False),
        sa.Column("comments", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"]),
    )
    op.create_index("ix_tasks_case", "investigation_tasks", ["case_id"])

    op.create_table(
        "investigator_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("classification", _enum("note_classification", ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "SECRET", "HIGHLY_RESTRICTED"]), nullable=False),
        sa.Column("linked_evidence", sa.JSON(), nullable=False),
        sa.Column("linked_entities", sa.JSON(), nullable=False),
        sa.Column("linked_findings", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
    )
    op.create_index("ix_notes_case", "investigator_notes", ["case_id"])

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("supporting_evidence", sa.JSON(), nullable=False),
        sa.Column("contradicting_evidence", sa.JSON(), nullable=False),
        sa.Column("unknown_information", sa.JSON(), nullable=False),
        sa.Column("investigator_id", sa.String(36), nullable=False),
        sa.Column("status", _enum("hypothesis_status", ["OPEN", "SUPPORTED", "WEAKENED", "REJECTED", "UNVERIFIED"]), nullable=False),
        sa.Column("assessment", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["investigator_id"], ["users.id"]),
    )
    op.create_index("ix_hypotheses_case", "hypotheses", ["case_id"])

    op.create_table(
        "contradictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("subject_key", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(120), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("verification_steps", sa.JSON(), nullable=False),
        sa.Column("status", _enum("contradiction_status", ["KNOWN", "UNKNOWN", "MISSING", "CONTRADICTORY", "UNVERIFIED"]), nullable=False),
        sa.Column("detected_by", sa.String(32), nullable=False),
        sa.Column("reviewed_by", sa.String(36), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
    )
    op.create_index("ix_contradictions_case", "contradictions", ["case_id"])
    op.create_index("ix_contradictions_subject", "contradictions", ["subject_key"])

    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("predicate", sa.String(120), nullable=False),
        sa.Column("object", sa.String(200), nullable=False),
        sa.Column("observed_at", sa.String(64), nullable=True),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", _enum("claim_status", ["KNOWN", "UNKNOWN", "MISSING", "CONTRADICTORY", "UNVERIFIED"]), nullable=False),
        sa.Column("contradiction_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_claims_case", "claims", ["case_id"])
    op.create_index("ix_claims_subject", "claims", ["subject"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=True),
        sa.Column("object_type", sa.String(48), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("approval_type", _enum("approval_type", ["EVIDENCE_SEAL", "ENTITY_MERGE", "FINDING", "REPORT", "CASE_CLOSURE", "EVIDENCE_EXPORT"]), nullable=False),
        sa.Column("status", _enum("approval_status", ["PENDING", "APPROVED", "REJECTED"]), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("decided_by", sa.String(36), nullable=True),
        sa.Column("object_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
    )
    op.create_index("ix_approvals_case", "approval_records", ["case_id"])
    op.create_index("ix_approvals_object", "approval_records", ["object_id"])

    op.create_table(
        "model_registry",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=True),
        sa.Column("deployment_version", sa.String(80), nullable=True),
        sa.Column("performance", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("evaluation_status", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_model_registry_role", "model_registry", ["role"])

    op.create_table(
        "investigation_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("evidence_index", sa.JSON(), nullable=False),
        sa.Column("object_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("generated_by", sa.String(36), nullable=True),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
    )
    op.create_index("ix_reports_case", "investigation_reports", ["case_id"])


def downgrade() -> None:
    for table in (
        "investigation_reports", "model_registry", "approval_records", "claims",
        "contradictions", "hypotheses", "investigator_notes", "investigation_tasks",
        "evidence_custody_events",
    ):
        op.drop_table(table)
    op.drop_column("case_documents", "classification")
    op.drop_column("cases", "classification")
    op.drop_column("users", "max_classification")
