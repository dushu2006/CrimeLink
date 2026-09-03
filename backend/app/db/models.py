"""SQLAlchemy models — the system of record (PRD 6.1).

Design decisions carried over from the PRD:

* ``users`` is soft-disabled only (``is_active``).  There is no row deletion
  anywhere in CrimeLink.
* ``case_documents`` has ``UNIQUE (case_id, content_hash)`` so a duplicate
  upload is rejected by the database itself — no application code path can
  bypass it.
* ``entity_resolution_queue`` stores **provenance keys** (not opaque Neo4j
  element IDs), which keeps the queue meaningful across re-ingestion and makes
  merges reversible.
* ``audit_logs`` stores ``prev_row_hash`` / ``row_hash`` for tamper-evidence
  and is made append-only at the database level in production.
* ``resolution_note`` is NOT NULL on any resolved row: an investigator cannot
  merge two human beings without writing down why.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, pk_column, utcnow
from app.domain.enums import (
    AccessRequestStatus,
    AuditAction,
    CaseStatus,
    DocumentType,
    IngestionStatus,
    JobStatus,
    MatchBasis,
    PatternStatus,
    PatternType,
    ResolutionStatus,
    Role,
    SourceConfidence,
)


def _enum(column_type: type, constraint_name: str) -> SAEnum:
    """CHECK-constrained VARCHAR enum that works on PostgreSQL and SQLite."""
    return SAEnum(
        column_type,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        name=constraint_name,
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = pk_column()
    badge_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False)
    station_id: Mapped[str] = mapped_column(String(64), nullable=False)
    jurisdiction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_users_jurisdiction", "jurisdiction_id"),)


class RefreshToken(Base):
    """Refresh-token families with rotation and reuse detection (PRD 12.1).

    A replayed refresh token invalidates the entire family and is logged as a
    security event, which is what makes token theft detectable rather than
    merely unlikely.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = pk_column()
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = pk_column()
    case_number: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus, "case_status"), default=CaseStatus.OPEN, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    read_only_at: Mapped[datetime | None] = mapped_column(
        DateTime(),
        nullable=True,
        comment="DPDP retention: closed cases become read-only after 90 days (PRD 12.3)",
    )
    created_at: Mapped[datetime] = created_at_column()


class CaseDocument(Base):
    __tablename__ = "case_documents"

    id: Mapped[str] = pk_column()
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[DocumentType] = mapped_column(
        _enum(DocumentType, "document_type"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    derived_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        _enum(IngestionStatus, "ingestion_status"),
        default=IngestionStatus.PENDING,
        nullable=False,
    )
    ingestion_stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_confidence: Mapped[SourceConfidence] = mapped_column(
        _enum(SourceConfidence, "source_confidence"),
        default=SourceConfidence.UNVERIFIED,
        nullable=False,
    )
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Soft delete only — no row is ever physically removed",
    )
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    source_metadata: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment=(
            "Adapter-supplied provenance that is not derivable from the bytes: "
            "document_origin for verbatim files, line_origins for rendered text."
        ),
    )
    created_at: Mapped[datetime] = created_at_column()

    case: Mapped[Case] = relationship("Case", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("case_id", "content_hash", name="uq_case_documents_case_hash"),
        CheckConstraint("retry_count >= 0", name="ck_case_documents_retry_nonneg"),
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = pk_column()
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False
    )
    current_stage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = created_at_column()


class DocumentStageEvent(Base):
    """Append-only per-stage progress feed for the live UI (PRD 14 screen 2)."""

    __tablename__ = "document_stage_events"

    id: Mapped[str] = pk_column()
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at_column()


class SourceReference(Base):
    """Exact coordinates of the source material behind one piece of information.

    This is the table that makes CrimeLink traceable rather than merely
    evidenced.  ``CaseDocument`` answers *which document*; this answers *where
    inside the original corpus file*, which is a different question whenever the
    ingested document is a derived artefact.

    Only the fields that are meaningful for a given source type are populated —
    a CSV reference carries ``row_number``/``field_names``, a text reference
    carries ``line_start``/``line_end``, and a PDF reference carries
    ``page_number``.  Nothing is invented to fill a column.

    ``origin_file`` is a path relative to the dataset root, never an absolute
    filesystem path, so a reference stays valid across deployments and cannot be
    used to read outside the corpus.
    """

    __tablename__ = "source_references"

    id: Mapped[str] = pk_column()

    # --- what this reference belongs to -----------------------------------
    doc_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("case_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # --- where it came from -----------------------------------------------
    origin_file: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Path relative to the dataset root, e.g. 'operational/cdr.csv'",
    )
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="csv | txt | json | pdf | db"
    )
    record_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True,
        comment="Natural key within the origin file, e.g. 'CDR000032'",
    )

    # --- exact position (only the applicable ones are set) ------------------
    row_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="1-based line number in the origin file"
    )
    field_names: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    field_values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- position inside the ingested (derived) document --------------------
    text_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        # Re-processing a document must converge rather than accumulate
        # duplicate references (idempotency, PRD 9.3).
        UniqueConstraint(
            "doc_id", "origin_file", "row_number", "text_start",
            name="uq_source_references_position",
        ),
        Index("ix_source_references_origin", "origin_file", "row_number"),
    )


class QuarantinedRecord(Base):
    """A corpus row that was read but could not be attached to any case.

    The adapter builds per-case documents, so a row it cannot route to a case
    simply never becomes one.  Those rows used to be counted in a scan warning
    and then dropped, which made real coverage unauditable: nothing recorded
    *which* rows were missing or why.

    This is deliberately not a ``CaseDocument`` with ``quarantined=True``.  That
    flag marks a document that failed *processing* and still belongs to a case;
    these rows have no case at all, which is precisely the problem.  They are
    kept here with enough coordinates to open the original record, so the
    quarantine view can show the row itself rather than a count.
    """

    __tablename__ = "quarantined_records"

    id: Mapped[str] = pk_column()

    # --- where the row came from -------------------------------------------
    origin_file: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Path relative to the dataset root, e.g. 'operational/cdr.csv'",
    )
    row_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="1-based line number including the header"
    )
    record_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True,
        comment="Natural primary key within the origin file, e.g. 'CDR000032'",
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="cdr | transactions | vehicle_sightings | ..."
    )

    # --- why it could not be imported --------------------------------------
    reason_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="Stable machine-readable category, e.g. 'unresolved_case_id'",
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Human-readable explanation for an investigator"
    )
    unresolved_case_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="The case_id on the row, when it was present but unknown",
    )

    # --- the row itself, so the record can be inspected and re-driven -------
    field_values: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    import_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    resolved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="Set when a later import attaches the row to a case",
    )

    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        # Re-running an import must converge on the same set of quarantined
        # rows rather than accumulate a copy per run (idempotency, PRD 9.3).
        UniqueConstraint(
            "origin_file", "row_number", "record_id",
            name="uq_quarantined_records_position",
        ),
        Index("ix_quarantined_records_reason", "source_type", "reason_code"),
    )


class EntityResolutionItem(Base):
    """Review queue 1 — possible duplicate people (PRD 6.1 / 9.2)."""

    __tablename__ = "entity_resolution_queue"

    id: Mapped[str] = pk_column()
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_basis: Mapped[MatchBasis] = mapped_column(
        _enum(MatchBasis, "match_basis"), default=MatchBasis.NAME_FUZZY, nullable=False
    )
    evidence_doc_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[ResolutionStatus] = mapped_column(
        _enum(ResolutionStatus, "resolution_status"),
        default=ResolutionStatus.PENDING,
        nullable=False,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "status <> 'PENDING' OR resolved_by IS NULL",
            name="ck_erq_pending_unresolved",
        ),
    )


class DetectedPattern(Base):
    """Review queue 2 — suspicious behaviour, never a confirmed finding (PRD 11.3)."""

    __tablename__ = "detected_patterns"

    id: Mapped[str] = pk_column()
    case_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    pattern_type: Mapped[PatternType] = mapped_column(
        _enum(PatternType, "pattern_type"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    entity_keys: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_doc_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[PatternStatus] = mapped_column(
        _enum(PatternStatus, "pattern_status"), default=PatternStatus.NEW, nullable=False
    )
    detected_at: Mapped[datetime] = created_at_column()
    reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class PatternConfig(Base):
    """Deployment-tunable thresholds (PRD 11.3: never hardcoded)."""

    __tablename__ = "pattern_config"

    id: Mapped[str] = pk_column()
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, onupdate=utcnow)


class JurisdictionAccessRequest(Base):
    """Cross-jurisdiction access, always time-boxed (PRD 12.4)."""

    __tablename__ = "jurisdiction_access_requests"

    id: Mapped[str] = pk_column()
    requester_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    target_jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AccessRequestStatus] = mapped_column(
        _enum(AccessRequestStatus, "access_request_status"),
        default=AccessRequestStatus.PENDING,
        nullable=False,
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class AuditLog(Base):
    """Append-only, hash-chained audit trail (PRD 12.2).

    ``row_hash = SHA256(prev_row_hash || canonical_json(row))``.  Editing any
    historical row invalidates every hash after it, which a single linear
    verification pass detects.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    badge_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_type: Mapped[AuditAction] = mapped_column(
        _enum(AuditAction, "audit_action_type"), nullable=False
    )
    target_resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    jurisdiction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    prev_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_audit_logs_timestamp", "timestamp"),)


class AuditChainHead(Base):
    """Single-row table holding the chain head.

    Updating this row inside the same transaction as the insert serialises
    appends (row lock on PostgreSQL, write lock on SQLite) without requiring
    advisory locks, so the chain cannot fork under concurrent writers.
    """

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditAnchor(Base):
    """Record of nightly anchors written to the separate credential store."""

    __tablename__ = "audit_anchors"

    id: Mapped[str] = pk_column()
    anchored_at: Mapped[datetime] = created_at_column()
    last_audit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    head_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
