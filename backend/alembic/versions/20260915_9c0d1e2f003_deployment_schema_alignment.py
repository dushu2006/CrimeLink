"""Deployment schema alignment: make ``alembic upgrade head`` equal the models.

Revision ID: 9c0d1e2f003
Revises: 8b9c0d1e002

Why this revision exists
------------------------
Until now the relational schema was only ever *completed* by
``Base.metadata.create_all()`` at startup (see ``app/db/bootstrap.py``), while
the migration chain lagged behind ``app/db/models.py``.  That is invisible on
the embedded SQLite profile — SQLite ignores ``VARCHAR`` lengths and
``create_all`` happily adds new tables — but it breaks a clean PostgreSQL
deployment, which is exactly what a fresh Render database is.  Measured against
a database created by ``alembic upgrade head`` only, the drift was:

* six tables the models declare were never created by any migration —
  ``datasets``, ``dataset_files``, ``dataset_entities``,
  ``dataset_relationships``, ``dataset_jobs`` and ``investigation_jobs``
  (the whole dataset-management and job surface);
* four columns were missing — ``cases.dataset_id``,
  ``cases.dataset_case_key``, ``case_documents.dataset_id`` and
  ``source_references.dataset_id``;
* three columns were too narrow for the enum values the models declare:
  ``case_documents.document_type`` (16 → 25),
  ``cases.status`` (12 → 20) and ``users.role`` (12 → 20).  PostgreSQL
  enforces the width, so writing ``ACTIVE_INVESTIGATION`` or
  ``SOCIAL_MEDIA_INTELLIGENCE`` failed with *value too long*, while SQLite
  accepted it silently;
* two enum ``CHECK`` constraints were missing values the models declare:
  ``case_documents.document_type`` and ``audit_logs.action_type``;
* ``cases`` still carried the single-column ``UNIQUE (case_number)`` — the
  models moved to ``UNIQUE (dataset_id, case_number)`` so two datasets may
  legitimately use the same case number;
* ``case_documents.case_id`` and ``source_references.case_id`` were ``NOT
  NULL`` although the models (and the dataset-scoped importer) allow a document
  or provenance row without a case;
* model-named indexes were missing, and a set of pre-rename index names
  (``ix_claims_case``, ``ix_custody_case``, …) were still present.

Every statement below is guarded with ``IF EXISTS`` / inspector checks, so the
revision is a no-op for an object that already exists.  That is deliberate: a
database previously created by ``create_all`` is *adopted* by
``python -m app.db.upgrade`` (additive reconcile → stamp → upgrade), which
re-runs this revision as the alignment step.  See ``app/db/upgrade.py``.

``downgrade()`` reverses the structural additions.  It deliberately does not
narrow the widened columns nor re-tighten the enum ``CHECK`` constraints: both
can fail against rows written after the upgrade (a narrowed column loses data,
a narrowed CHECK rejects existing values), so the safe direction is the one
this migration takes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c0d1e2f003"
down_revision: str | None = "8b9c0d1e002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------- #
# Model-named indexes that the chain had created under older names.  Each entry
# is (legacy name, table, model name, columns): the legacy index is dropped only
# when it exists, the model index is created only when it is absent.
# --------------------------------------------------------------------------- #
_INDEX_RENAMES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("ix_approvals_case", "approval_records", "ix_approval_records_case_id", ("case_id",)),
    ("ix_approvals_object", "approval_records", "ix_approval_records_object_id", ("object_id",)),
    ("ix_claims_case", "claims", "ix_claims_case_id", ("case_id",)),
    ("ix_contradictions_case", "contradictions", "ix_contradictions_case_id", ("case_id",)),
    ("ix_contradictions_subject", "contradictions", "ix_contradictions_subject_key", ("subject_key",)),
    ("ix_custody_case", "evidence_custody_events", "ix_evidence_custody_events_case_id", ("case_id",)),
    ("ix_custody_evidence", "evidence_custody_events", "ix_evidence_custody_events_evidence_id", ("evidence_id",)),
    ("ix_hypotheses_case", "hypotheses", "ix_hypotheses_case_id", ("case_id",)),
    ("ix_reports_case", "investigation_reports", "ix_investigation_reports_case_id", ("case_id",)),
    ("ix_tasks_case", "investigation_tasks", "ix_investigation_tasks_case_id", ("case_id",)),
    ("ix_notes_case", "investigator_notes", "ix_investigator_notes_case_id", ("case_id",)),
)

#: Indexes the models declare that no earlier revision created.
_MISSING_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("cases", "ix_cases_case_number", ("case_number",)),
    ("cases", "ix_cases_dataset_case_key", ("dataset_case_key",)),
    ("cases", "ix_cases_dataset_id", ("dataset_id",)),
    ("case_documents", "ix_case_documents_dataset_id", ("dataset_id",)),
    ("source_references", "ix_source_references_dataset_id", ("dataset_id",)),
)

#: Columns the models declare as nullable although an earlier revision made them
#: NOT NULL.  A dataset-scoped document / provenance row has no case.
_RELAX_NOT_NULL: tuple[tuple[str, str], ...] = (
    ("case_documents", "case_id"),
    ("source_references", "case_id"),
)

#: Columns widened to the width the models declare.  PostgreSQL enforces
#: ``VARCHAR`` length; SQLite does not, so there the widening is a no-op.
_WIDENED_COLUMNS: tuple[tuple[str, str, int, int], ...] = (
    ("case_documents", "document_type", 16, 25),
    ("cases", "status", 12, 20),
    ("users", "role", 12, 20),
)

#: Columns the models declare NOT NULL although an earlier revision left them
#: nullable, with the literal used to backfill rows that predate the change.
_NOT_NULL_BACKFILL: tuple[tuple[str, str, str], ...] = (
    ("cases", "classification", "'INTERNAL'"),
    ("investigation_sessions", "created_at", "now()"),
    ("investigation_sessions", "updated_at", "now()"),
    ("case_documents", "source_metadata", "'{}'::jsonb"),
)

_ENUMS: dict[str, tuple[str, ...]] = {
    "access_request_status": (
        "PENDING",
        "APPROVED",
        "DENIED",
        "EXPIRED",
    ),
    "approval_status": (
        "PENDING",
        "APPROVED",
        "REJECTED",
    ),
    "approval_type": (
        "EVIDENCE_SEAL",
        "ENTITY_MERGE",
        "FINDING",
        "REPORT",
        "CASE_CLOSURE",
        "EVIDENCE_EXPORT",
    ),
    "audit_action_type": (
        "LOGIN",
        "LOGIN_FAILED",
        "SEARCH",
        "GRAPH_EXPAND",
        "DOC_VIEW",
        "DOC_UPLOAD",
        "MERGE",
        "MERGE_REJECT",
        "PATTERN_REVIEW",
        "EXPORT",
        "ACCESS_REQUEST",
        "ACCESS_APPROVAL",
        "QUARANTINE_RELEASE",
        "CONFIG_CHANGE",
        "AI_QUERY",
        "INVESTIGATE",
        "GLOBAL_SEARCH",
        "TIMELINE_ANALYZE",
    ),
    "case_classification": (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
        "SECRET",
        "HIGHLY_RESTRICTED",
    ),
    "case_status": (
        "DRAFT",
        "OPEN",
        "ACTIVE_INVESTIGATION",
        "UNDER_REVIEW",
        "SUBMITTED",
        "CLOSED",
        "SEALED",
    ),
    "claim_status": (
        "KNOWN",
        "UNKNOWN",
        "MISSING",
        "CONTRADICTORY",
        "UNVERIFIED",
    ),
    "contradiction_status": (
        "KNOWN",
        "UNKNOWN",
        "MISSING",
        "CONTRADICTORY",
        "UNVERIFIED",
    ),
    "custody_event_type": (
        "COLLECTED",
        "IMPORTED",
        "HASH_VERIFIED",
        "STORED",
        "ACCESSED",
        "DOWNLOADED",
        "DERIVED",
        "SHARED",
        "EXPORTED",
        "SEALED",
    ),
    "document_type": (
        "FIR",
        "CHARGE_SHEET",
        "CDR",
        "CCTV",
        "FINANCIAL",
        "FINANCIAL_TRANSACTION",
        "SURVEILLANCE",
        "SURVEILLANCE_REPORT",
        "WITNESS_STATEMENT",
        "SCENE_REPORT",
        "INTELLIGENCE_REPORT",
        "CRIMINAL_RECORD",
        "CRIMINAL_HISTORY",
        "ARREST_RECORD",
        "BAIL_RECORD",
        "SOCIAL_MEDIA",
        "SOCIAL_MEDIA_INTELLIGENCE",
        "ANPR",
        "PATROL_REPORT",
        "LEGAL_RECORD",
        "CASE_DIARY",
        "SEIZURE",
        "FORENSIC",
        "GEO_EVENT",
        "REVIEW",
        "DATASET_RESOURCE",
        "README",
        "DATA_DICTIONARY",
        "QUALITY_REPORT",
        "MASTER_INDEX",
        "INTEL",
        "EVIDENCE",
        "RELATIONSHIP",
        "OTHER",
    ),
    "evidence_classification": (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
        "SECRET",
        "HIGHLY_RESTRICTED",
    ),
    "hypothesis_status": (
        "OPEN",
        "SUPPORTED",
        "WEAKENED",
        "REJECTED",
        "UNVERIFIED",
    ),
    "ingestion_status": (
        "PENDING",
        "PROCESSING",
        "COMPLETE",
        "FAILED",
        "QUARANTINED",
    ),
    "job_status": (
        "QUEUED",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "QUARANTINED",
    ),
    "match_basis": (
        "NAME_FUZZY",
        "PHONE_PARTIAL",
        "PHOTO_SIMILARITY",
        "ALIAS_CO_MENTION",
    ),
    "note_classification": (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
        "SECRET",
        "HIGHLY_RESTRICTED",
    ),
    "pattern_status": (
        "NEW",
        "REVIEWED",
        "DISMISSED",
        "ESCALATED",
    ),
    "pattern_type": (
        "STRUCTURING",
        "BURNER_PHONE",
        "RAPID_MOVEMENT",
        "NETWORK_BRIDGE",
    ),
    "resolution_status": (
        "PENDING",
        "MERGED",
        "REJECTED",
    ),
    "role": (
        "VIEWER",
        "INVESTIGATOR",
        "SUPERVISOR",
        "FORENSIC_ANALYST",
        "FINANCIAL_ANALYST",
        "INTELLIGENCE_ANALYST",
        "AUDITOR",
        "STATION_ADMIN",
        "DISTRICT_ADMIN",
        "SUPER_ADMIN",
        "ADMIN",
    ),
    "source_confidence": (
        "VERIFIED",
        "UNVERIFIED",
        "ANONYMOUS_TIP",
        "SYNTHETIC",
    ),
    "task_priority": (
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ),
    "task_status": (
        "TODO",
        "IN_PROGRESS",
        "BLOCKED",
        "PENDING_REVIEW",
        "COMPLETED",
        "CANCELLED",
    ),
    "user_max_classification": (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
        "SECRET",
        "HIGHLY_RESTRICTED",
    ),
}

#: Every enum column the models declare, and the CHECK constraint the
#: naming convention derives for it (`ck_<table>_<enum name>`).
_ENUM_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("approval_records", "approval_type", "approval_type"),
    ("approval_records", "status", "approval_status"),
    ("audit_logs", "action_type", "audit_action_type"),
    ("case_documents", "document_type", "document_type"),
    ("case_documents", "ingestion_status", "ingestion_status"),
    ("case_documents", "source_confidence", "source_confidence"),
    ("case_documents", "classification", "evidence_classification"),
    ("cases", "status", "case_status"),
    ("cases", "classification", "case_classification"),
    ("claims", "status", "claim_status"),
    ("contradictions", "status", "contradiction_status"),
    ("detected_patterns", "pattern_type", "pattern_type"),
    ("detected_patterns", "status", "pattern_status"),
    ("entity_resolution_queue", "match_basis", "match_basis"),
    ("entity_resolution_queue", "status", "resolution_status"),
    ("evidence_custody_events", "event_type", "custody_event_type"),
    ("hypotheses", "status", "hypothesis_status"),
    ("ingestion_jobs", "status", "job_status"),
    ("investigation_tasks", "priority", "task_priority"),
    ("investigation_tasks", "status", "task_status"),
    ("investigator_notes", "classification", "note_classification"),
    ("jurisdiction_access_requests", "status", "access_request_status"),
    ("users", "role", "role"),
    ("users", "max_classification", "user_max_classification"),
)


def _enum(name: str) -> sa.Enum:
    return sa.Enum(*_ENUMS[name], name=name, native_enum=False, create_constraint=True)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {i["name"] for i in _inspector().get_indexes(table)}


def _has_constraint(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    inspector = _inspector()
    names = {c["name"] for c in inspector.get_unique_constraints(table)}
    names |= {c["name"] for c in inspector.get_check_constraints(table)}
    names |= {c["name"] for c in inspector.get_foreign_keys(table)}
    return name in names


def _has_alembic_table(name: str) -> bool:  # pragma: no cover - debugging aid
    return _has_table(name)


# --------------------------------------------------------------------------- #
# 1. Tables the chain never created
# --------------------------------------------------------------------------- #
def _create_missing_tables() -> None:
    if not _has_table("datasets"):
        op.create_table(
            "datasets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("version", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("source_kind", sa.String(24), nullable=False),
            sa.Column("root_path", sa.Text(), nullable=False),
            sa.Column("origin_note", sa.Text(), nullable=False),
            sa.Column("stage_detail", sa.JSON(), nullable=False),
            sa.Column("stats", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("graph_built_at", sa.DateTime(), nullable=True),
            sa.Column("search_indexed_at", sa.DateTime(), nullable=True),
            sa.Column("ai_indexed_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_datasets_is_active", "datasets", ["is_active"])
        op.create_index("ix_datasets_status", "datasets", ["status"])

    if not _has_table("dataset_files"):
        op.create_table(
            "dataset_files",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), nullable=False),
            sa.Column("relative_path", sa.Text(), nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("extension", sa.String(24), nullable=False),
            sa.Column("media_type", sa.String(160), nullable=False),
            sa.Column("file_kind", sa.String(24), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("container_path", sa.Text(), nullable=True),
            sa.Column("semantic_type", sa.String(48), nullable=False),
            sa.Column("classification_confidence", sa.Float(), nullable=False),
            sa.Column("column_mapping", sa.JSON(), nullable=False),
            sa.Column("unmapped_columns", sa.JSON(), nullable=False),
            sa.Column("mapping_notes", sa.JSON(), nullable=False),
            sa.Column("mapping_accepted_at", sa.DateTime(), nullable=True),
            sa.Column("mapping_accepted_by", sa.String(36), nullable=True),
            sa.Column("sheet_names", sa.JSON(), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("page_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("doc_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("dataset_id", "relative_path", name="uq_dataset_files_path"),
        )
        op.create_index("ix_dataset_files_dataset_id", "dataset_files", ["dataset_id"])
        op.create_index("ix_dataset_files_doc_id", "dataset_files", ["doc_id"])
        op.create_index("ix_dataset_files_semantic", "dataset_files", ["dataset_id", "semantic_type"])

    if not _has_table("dataset_entities"):
        op.create_table(
            "dataset_entities",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), nullable=False),
            sa.Column("canonical_id", sa.String(200), nullable=False),
            sa.Column("entity_type", sa.String(32), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("normalized_value", sa.String(240), nullable=False),
            sa.Column("attributes", sa.JSON(), nullable=False),
            sa.Column("provenance", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("dataset_id", "canonical_id", name="uq_dataset_entities_canonical"),
        )
        op.create_index("ix_dataset_entities_dataset_id", "dataset_entities", ["dataset_id"])
        op.create_index("ix_dataset_entities_entity_type", "dataset_entities", ["entity_type"])
        op.create_index(
            "ix_dataset_entities_lookup",
            "dataset_entities",
            ["dataset_id", "entity_type", "normalized_value"],
        )

    if not _has_table("dataset_relationships"):
        op.create_table(
            "dataset_relationships",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), nullable=False),
            sa.Column("source_canonical_id", sa.String(200), nullable=False),
            sa.Column("target_canonical_id", sa.String(200), nullable=False),
            sa.Column("rel_type", sa.String(48), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("edge_key", sa.String(96), nullable=False),
            sa.Column("valid_from", sa.String(32), nullable=True),
            sa.Column("valid_to", sa.String(32), nullable=True),
            sa.Column("observed_at", sa.String(32), nullable=True),
            sa.Column("attributes", sa.JSON(), nullable=False),
            sa.Column("provenance", sa.JSON(), nullable=False),
            sa.Column("case_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("dataset_id", "edge_key", name="uq_dataset_relationships_key"),
        )
        op.create_index("ix_dataset_relationships_dataset_id", "dataset_relationships", ["dataset_id"])
        op.create_index("ix_dataset_relationships_rel_type", "dataset_relationships", ["rel_type"])
        op.create_index(
            "ix_dataset_relationships_source_canonical_id",
            "dataset_relationships",
            ["source_canonical_id"],
        )
        op.create_index(
            "ix_dataset_relationships_target_canonical_id",
            "dataset_relationships",
            ["target_canonical_id"],
        )

    if not _has_table("dataset_jobs"):
        op.create_table(
            "dataset_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), nullable=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("stage", sa.String(48), nullable=False),
            sa.Column("progress_pct", sa.Integer(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("steps", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("requested_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_dataset_jobs_dataset_id", "dataset_jobs", ["dataset_id"])

    if not _has_table("investigation_jobs"):
        op.create_table(
            "investigation_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_id", sa.String(36), nullable=True),
            sa.Column("case_id", sa.String(36), nullable=True),
            sa.Column("investigation_id", sa.String(36), nullable=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("stage", sa.String(48), nullable=False),
            sa.Column("progress_pct", sa.Integer(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("steps", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("requested_by", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_investigation_jobs_dataset_id", "investigation_jobs", ["dataset_id"])
        op.create_index("ix_investigation_jobs_case_id", "investigation_jobs", ["case_id"])
        op.create_index("ix_investigation_jobs_investigation_id", "investigation_jobs", ["investigation_id"])


def _drop_created_tables() -> None:
    for table in (
        "investigation_jobs",
        "dataset_jobs",
        "dataset_relationships",
        "dataset_entities",
        "dataset_files",
        "datasets",
    ):
        if _has_table(table):
            op.drop_table(table)


# --------------------------------------------------------------------------- #
# 2. Columns the chain never created
# --------------------------------------------------------------------------- #
def _add_missing_columns() -> None:
    additions = (
        ("cases", sa.Column("dataset_id", sa.String(36), nullable=True)),
        ("cases", sa.Column("dataset_case_key", sa.String(120), nullable=True)),
        ("case_documents", sa.Column("dataset_id", sa.String(36), nullable=True)),
        ("source_references", sa.Column("dataset_id", sa.String(36), nullable=True)),
    )
    for table, column in additions:
        if _has_table(table) and not _has_column(table, column.name):
            op.add_column(table, column)


def _drop_added_columns() -> None:
    for table, column in (
        ("cases", "dataset_case_key"),
        ("cases", "dataset_id"),
        ("case_documents", "dataset_id"),
        ("source_references", "dataset_id"),
    ):
        if _has_column(table, column):
            op.drop_column(table, column)


# --------------------------------------------------------------------------- #
# 3. Indexes: legacy names dropped, model names created
# --------------------------------------------------------------------------- #
def _align_indexes() -> None:
    bind = op.get_bind()
    for legacy, table, current, columns in _INDEX_RENAMES:
        if _has_index(table, legacy):
            bind.exec_driver_sql(f'DROP INDEX IF EXISTS "{legacy}"')
        if _has_table(table) and not _has_index(table, current):
            op.create_index(current, table, list(columns))

    for table, name, columns in _MISSING_INDEXES:
        if _has_table(table) and not _has_index(table, name):
            op.create_index(name, table, list(columns))


def _drop_created_indexes() -> None:
    bind = op.get_bind()
    for table, name, columns in _MISSING_INDEXES:
        if _has_index(table, name):
            bind.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')


# --------------------------------------------------------------------------- #
# 4. Column widths (PostgreSQL enforces them; SQLite does not)
# --------------------------------------------------------------------------- #
def _widen_columns() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite ignores VARCHAR length entirely, so widening is a no-op there
        # and no table rewrite is performed.
        return
    for table, column, _, target in _WIDENED_COLUMNS:
        if not _has_column(table, column):
            continue
        bind.exec_driver_sql(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE VARCHAR({target})'
        )


# --------------------------------------------------------------------------- #
# 5. NOT NULL relaxations and the composite case-number uniqueness
# --------------------------------------------------------------------------- #
def _relax_not_null() -> None:
    bind = op.get_bind()
    for table, column in _RELAX_NOT_NULL:
        if not _has_column(table, column):
            continue
        if bind.dialect.name == "postgresql":
            bind.exec_driver_sql(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP NOT NULL')
        else:
            _sqlite_rebuild_relaxing(table, column)


def _align_case_uniqueness() -> None:
    bind = op.get_bind()
    if not _has_table("cases"):
        return
    legacy = "uq_cases_case_number"
    current = "uq_cases_dataset_case_number"
    if bind.dialect.name == "postgresql":
        if _has_constraint("cases", legacy):
            bind.exec_driver_sql(f'ALTER TABLE cases DROP CONSTRAINT IF EXISTS "{legacy}"')
        if not _has_constraint("cases", current):
            bind.exec_driver_sql(
                f'ALTER TABLE cases ADD CONSTRAINT "{current}" UNIQUE (dataset_id, case_number)'
            )
        return
    if _has_constraint("cases", legacy) or not _has_constraint("cases", current):
        _sqlite_rebuild_case_uniqueness(legacy, current)


def _reinstate_case_uniqueness() -> None:
    bind = op.get_bind()
    if not _has_table("cases"):
        return
    legacy = "uq_cases_case_number"
    current = "uq_cases_dataset_case_number"
    if bind.dialect.name == "postgresql":
        if _has_constraint("cases", current):
            bind.exec_driver_sql(f'ALTER TABLE cases DROP CONSTRAINT IF EXISTS "{current}"')
        if not _has_constraint("cases", legacy):
            bind.exec_driver_sql(
                f'ALTER TABLE cases ADD CONSTRAINT "{legacy}" UNIQUE (case_number)'
            )


# --------------------------------------------------------------------------- #
# 6. Enum CHECK constraints that gained values
# --------------------------------------------------------------------------- #
def _align_enum_checks() -> None:
    """Make every enum CHECK constraint say what the models say.

    An earlier revision, or the ``create_all`` reconcile that adopted a
    pre-Alembic database, can leave an enum column with a CHECK that predates
    values the models added (``SYNTHETIC`` source confidence, the wider
    document-type and role lists, a classification constraint that never got
    created because SQLite cannot add a table constraint with ``ALTER TABLE``).
    Values outside the list are rejected by the database, so this is not
    cosmetic.
    """
    bind = op.get_bind()
    by_table: dict[str, list[tuple[str, str]]] = {}
    for table, column, enum_name in _ENUM_COLUMNS:
        if not _has_column(table, column):
            continue
        if bind.dialect.name == "postgresql":
            constraint = f"ck_{table}_{enum_name}"
            values = ", ".join(f"'{value}'" for value in _ENUMS[enum_name])
            bind.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint}"')
            bind.exec_driver_sql(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" '
                f"CHECK ({column} IN ({values}))"
            )
        else:
            by_table.setdefault(table, []).append((column, enum_name))
    for table, columns in by_table.items():
        _sqlite_align_table_enum_checks(table, tuple(columns))


# --------------------------------------------------------------------------- #
# 7. NOT NULL columns the chain left nullable
# --------------------------------------------------------------------------- #
def _enforce_not_null() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite cannot ALTER a column's nullability in place, and the embedded
        # profile writes every one of these columns on insert.  Left as declared
        # (the embedded profile also re-applies the model schema on boot).
        return
    for table, column, backfill in _NOT_NULL_BACKFILL:
        if not _has_column(table, column):
            continue
        bind.exec_driver_sql(
            f'UPDATE "{table}" SET "{column}" = {backfill} WHERE "{column}" IS NULL'
        )
        bind.exec_driver_sql(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET NOT NULL'
        )


# --------------------------------------------------------------------------- #
# SQLite helpers — SQLite has no ALTER for constraints, so the table is rebuilt
# with the corrected definition while every row, index and rowid is preserved.
# --------------------------------------------------------------------------- #
def _sqlite_table_sql(table: str) -> str | None:
    bind = op.get_bind()
    row = bind.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _sqlite_rebuild(table: str, new_sql: str, temp_suffix: str) -> None:
    """Recreate *table* from *new_sql*, copying every row and index."""
    bind = op.get_bind()
    temp = f"{table}__{temp_suffix}"
    columns = [row[1] for row in bind.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()]
    column_list = ", ".join(f'"{c}"' for c in columns)
    indexes = bind.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()

    # ``sqlite_master.sql`` stores the table name with or without quotes
    # depending on who created it (Alembic writes it bare, SQLAlchemy's
    # ``create_all`` quotes it), so rename it with a tolerant pattern.
    rename = re.compile(
        rf'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"{re.escape(table)}"|{re.escape(table)})(?![\w])',
        re.IGNORECASE,
    )
    if not rename.search(new_sql):
        return

    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        bind.exec_driver_sql(rename.sub(f'CREATE TABLE "{temp}"', new_sql, count=1))
        bind.exec_driver_sql(
            f'INSERT INTO "{temp}" ({column_list}) SELECT {column_list} FROM "{table}"'
        )
        bind.exec_driver_sql(f'DROP TABLE "{table}"')
        bind.exec_driver_sql(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
        for _, index_sql in indexes:
            bind.exec_driver_sql(index_sql)
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def _sqlite_rebuild_relaxing(table: str, column: str) -> None:
    create_sql = _sqlite_table_sql(table)
    if not create_sql:
        return
    pattern = re.compile(
        rf'("{re.escape(column)}"|{re.escape(column)})(\s+VARCHAR\(\d+\))\s+NOT\s+NULL',
        re.IGNORECASE,
    )
    if not pattern.search(create_sql):
        return
    _sqlite_rebuild(table, pattern.sub(r"\1\2", create_sql, count=1), "relax")


def _sqlite_align_table_enum_checks(table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """Align every enum CHECK constraint on *table* in one table rebuild.

    SQLite has no ``ALTER TABLE ... ADD/DROP CONSTRAINT``, so the definition is
    edited element by element (see ``_split_table_elements``) and the table
    rebuilt once, no matter how many enum columns it carries.
    """
    create_sql = _sqlite_table_sql(table)
    if not create_sql:
        return
    head, sep, rest = create_sql.partition("(")
    body, closing, tail = rest.rpartition(")")
    if not sep or not closing:
        return
    elements = _split_table_elements(body)
    changed = False
    for column, enum_name in columns:
        constraint = f"ck_{table}_{enum_name}"
        values = ", ".join(f"'{value}'" for value in _ENUMS[enum_name])
        expected = f"CONSTRAINT {constraint} CHECK ({column} IN ({values}))"
        pattern = re.compile(
            rf'^CONSTRAINT\s+("{re.escape(constraint)}"|{re.escape(constraint)})\b',
            re.IGNORECASE,
        )
        for index, element in enumerate(elements):
            if pattern.match(element):
                if element != expected:
                    elements[index] = expected
                    changed = True
                break
        else:
            elements.append(expected)
            changed = True
    if not changed:
        return
    rebuilt = f"{head}(\n\t" + ",\n\t".join(elements) + f"\n){tail}"
    _sqlite_rebuild(table, rebuilt, "enum")


def _split_table_elements(body: str) -> list[str]:
    """Split a ``CREATE TABLE`` body into top-level elements.

    Comma-aware and quote-aware: a ``CHECK (col IN ('a', 'b'))`` element stays
    one element instead of being torn apart at its commas.
    """
    elements: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote: str | None = None
    for character in body:
        if quote:
            buffer.append(character)
            if character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            elements.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(character)
    tail = "".join(buffer).strip()
    if tail:
        elements.append(tail)
    return elements


def _sqlite_rebuild_case_uniqueness(legacy: str, current: str) -> None:
    """Swap ``UNIQUE (case_number)`` for ``UNIQUE (dataset_id, case_number)``.

    SQLite deletes the old constraint by rebuilding the table.  The rebuild
    keeps SQLAlchemy's own layout — one constraint per line — because the
    SQLite dialect's ``CREATE TABLE`` parser relies on it to report constraint
    names; a reformatted statement reflects as a mangled name.
    """
    create_sql = _sqlite_table_sql("cases")
    if not create_sql:
        return
    head, sep, rest = create_sql.partition("(")
    body, sep2, tail = rest.rpartition(")")
    if not sep or not sep2:
        return
    legacy_pattern = re.compile(rf'^CONSTRAINT\s+("{re.escape(legacy)}"|{re.escape(legacy)})\b', re.I)
    current_pattern = re.compile(rf'^CONSTRAINT\s+("{re.escape(current)}"|{re.escape(current)})\b', re.I)
    elements = _split_table_elements(body)
    kept = [element for element in elements if not legacy_pattern.match(element)]
    if len(kept) == len(elements):
        return
    if not any(current_pattern.match(element) for element in kept):
        kept.append(f"CONSTRAINT {current} UNIQUE (dataset_id, case_number)")
    rebuilt = f"{head}(\n\t" + ",\n\t".join(kept) + f"\n){tail}"
    _sqlite_rebuild("cases", rebuilt, "uq")


# --------------------------------------------------------------------------- #
def upgrade() -> None:
    _create_missing_tables()
    _add_missing_columns()
    _align_indexes()
    _widen_columns()
    _relax_not_null()
    _align_case_uniqueness()
    _align_enum_checks()
    _enforce_not_null()


def downgrade() -> None:
    # Structural additions are reversed.  Column widening and the widened enum
    # CHECK constraints are intentionally left in place: narrowing either one
    # can fail against rows written since the upgrade.
    _reinstate_case_uniqueness()
    _drop_created_indexes()
    _drop_added_columns()
    _drop_created_tables()
