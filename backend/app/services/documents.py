"""Document upload, job tracking, evidence access and hash verification (PRD 7/10/12.5).

Upload ordering is deliberate and follows the PRD: **the original bytes go to
object storage first**, before the database row is created and before any
extraction runs.  If a later stage corrupts something, re-processing always
starts from the pristine original.

Upload returns ``202 Accepted`` with a job id immediately; the heavy work runs in
the background (PRD principle P2).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.container import Container, get_container
from app.db.base import new_uuid, utcnow
from app.db.models import Case, CaseDocument, DocumentStageEvent, IngestionJob
from app.domain.enums import (
    DocumentType,
    IngestionStatus,
    JobStatus,
    SourceConfidence,
)
from app.domain.provenance import content_hash
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.logging import get_logger, new_trace_id
from app.pipeline.adapters.registry import supported_types
from app.security.deps import Principal

log = get_logger("crimelink.services.documents")

MAX_UPLOAD_BYTES_FALLBACK = 64 * 1024 * 1024


async def upload_document(
    session: AsyncSession,
    *,
    container: Container | None = None,
    case: Case,
    principal: Principal,
    filename: str,
    payload: bytes,
    document_type: DocumentType,
    source_confidence: SourceConfidence = SourceConfidence.UNVERIFIED,
    mime_type: str = "application/octet-stream",
    language_hint: str | None = None,
) -> tuple[CaseDocument, IngestionJob]:
    """Persist the original, record metadata, and enqueue the pipeline."""
    container = container or get_container()
    settings: Settings = container.settings

    if not payload:
        raise ValidationFailedError("The uploaded file is empty.")
    if len(payload) > settings.upload_max_bytes:
        raise ValidationFailedError(
            f"File exceeds the {settings.upload_max_bytes // (1024 * 1024)} MB upload limit."
        )
    if document_type.value not in supported_types():
        raise ValidationFailedError(f"Unsupported document type '{document_type.value}'.")

    digest = content_hash(payload)

    # Duplicate detection is enforced by UNIQUE (case_id, content_hash) at the
    # database level; this pre-check only turns the constraint violation into a
    # clear message.  The constraint is the real guarantee.
    existing = (
        await session.execute(
            select(CaseDocument).where(
                CaseDocument.case_id == case.id, CaseDocument.content_hash == digest
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "This exact file has already been uploaded to this case "
            f"(as '{existing.filename}')."
        )

    doc_id = new_uuid()
    storage_key = f"{case.id}/{doc_id}/{filename}"

    # 1. Write-once object storage holds the original before anything else.
    container.object_store.put(
        settings.minio_bucket_documents, storage_key, payload, content_type=mime_type
    )

    # 2. Metadata row (the hash is the chain-of-custody fingerprint).
    document = CaseDocument(
        id=doc_id,
        case_id=case.id,
        document_type=document_type,
        filename=filename,
        storage_key=storage_key,
        content_hash=digest,
        size_bytes=len(payload),
        mime_type=mime_type,
        ingestion_status=IngestionStatus.PENDING,
        source_confidence=source_confidence,
        uploaded_by=principal.id,
    )
    session.add(document)

    job = IngestionJob(
        id=new_uuid(),
        case_id=case.id,
        doc_id=doc_id,
        status=JobStatus.QUEUED,
        trace_id=new_trace_id(),
        requested_by=principal.id,
    )
    session.add(job)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("This file has already been uploaded to this case.") from exc

    # 3. Commit BEFORE dispatching.  A worker (or another API process) can pick
    #    the job up within milliseconds; if the rows are still inside an open
    #    transaction the pipeline would read a database in which the document
    #    "does not exist yet" and quarantine a perfectly good upload (PRD 9.4:
    #    the pipeline must never lose a document).
    await session.commit()

    container.broker.dispatch_document_pipeline(
        job_id=job.id,
        doc_id=doc_id,
        case_id=case.id,
        trace_id=job.trace_id or "",
        user_id=principal.id,
    )
    log.info(
        "document.uploaded",
        doc_id=doc_id,
        case_id=case.id,
        job_id=job.id,
        sha256=digest[:16],
        size=len(payload),
        document_type=document_type.value,
    )
    return document, job


async def list_documents(
    session: AsyncSession, case_id: str, *, include_deleted: bool = False
) -> list[CaseDocument]:
    stmt = select(CaseDocument).where(CaseDocument.case_id == case_id)
    if not include_deleted:
        stmt = stmt.where(CaseDocument.is_deleted.is_(False))
    return list((await session.execute(stmt.order_by(CaseDocument.created_at))).scalars().all())


def document_row(document: CaseDocument, containers: Container | None = None) -> dict[str, Any]:
    container = containers or get_container()
    return {
        "id": document.id,
        "case_id": document.case_id,
        "document_type": document.document_type.value,
        "filename": document.filename,
        "language": document.language,
        "size_bytes": document.size_bytes,
        "content_hash": document.content_hash,
        "ingestion_status": document.ingestion_status.value,
        "ingestion_stage": document.ingestion_stage,
        "failure_reason": document.failure_reason,
        "source_confidence": document.source_confidence.value,
        "quarantined": document.quarantined,
        "retry_count": document.retry_count,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "evidence_url": container.object_store.presigned_url(
            container.settings.minio_bucket_documents,
            document.storage_key,
            container.settings.presigned_url_ttl_seconds,
        )
        if container.settings.effective_object_store_backend == "minio"
        else None,
    }


async def get_job(session: AsyncSession, job_id: str) -> IngestionJob:
    job = await session.get(IngestionJob, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return job


async def job_row(session: AsyncSession, job: IngestionJob) -> dict[str, Any]:
    events = (
        await session.execute(
            select(DocumentStageEvent)
            .where(DocumentStageEvent.doc_id == job.doc_id)
            .order_by(DocumentStageEvent.id)
        )
    ).scalars().all()
    return {
        "job_id": job.id,
        "doc_id": job.doc_id,
        "case_id": job.case_id,
        "status": job.status.value,
        "current_stage": job.current_stage,
        "stage_name": job.stage_name,
        "progress_pct": job.progress_pct,
        "error": job.error,
        "trace_id": job.trace_id,
        "total_stages": 6,
        "stages": [
            {
                "stage": e.stage,
                "stage_name": e.stage_name,
                "status": e.status,
                "detail": e.detail,
                "at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


async def evidence_payload(
    session: AsyncSession,
    container: Container,
    document: CaseDocument,
    span: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Document metadata, a 15-minute signed link, and the highlighted snippet."""
    url = container.object_store.presigned_url(
        container.settings.minio_bucket_documents,
        document.storage_key,
        container.settings.presigned_url_ttl_seconds,
    )
    snippet: str | None = None
    if span and document.derived_key:
        try:
            raw = container.object_store.get(
                container.settings.minio_bucket_derived, document.derived_key
            )
            text = raw.decode("utf-8", errors="replace")
            start, end = max(0, span[0]), min(len(text), span[1])
            snippet = text[max(0, start - 120) : min(len(text), end + 120)]
        except Exception:  # noqa: BLE001 - derived text is best-effort
            snippet = None
    return {
        "document_id": document.id,
        "case_id": document.case_id,
        "filename": document.filename,
        "document_type": document.document_type.value,
        "content_hash": document.content_hash,
        "language": document.language,
        "source_confidence": document.source_confidence.value,
        "ingestion_status": document.ingestion_status.value,
        "signed_url": url,
        "expires_in_seconds": container.settings.presigned_url_ttl_seconds,
        "text_span": list(span) if span else None,
        "snippet": snippet,
    }


async def verify_document_hash(
    container: Container, document: CaseDocument
) -> dict[str, Any]:
    """Re-compute the stored document's SHA-256 (chain of custody, PRD 12.5)."""
    from app.domain.provenance import content_hash

    try:
        raw = container.object_store.get(
            container.settings.minio_bucket_documents, document.storage_key
        )
    except Exception as exc:  # noqa: BLE001
        raise NotFoundError("The stored document could not be read.") from exc
    actual = content_hash(raw)
    return {
        "document_id": document.id,
        "recorded_hash": document.content_hash,
        "computed_hash": actual,
        "match": actual == document.content_hash,
        "size_bytes": len(raw),
        "verified_at": utcnow().isoformat(),
    }


async def quarantine_list(session: AsyncSession, case_id: str | None = None) -> list[CaseDocument]:
    stmt = select(CaseDocument).where(CaseDocument.quarantined.is_(True))
    if case_id:
        stmt = stmt.where(CaseDocument.case_id == case_id)
    return list((await session.execute(stmt.order_by(CaseDocument.created_at.desc()))).scalars().all())


async def release_from_quarantine(session: AsyncSession, document: CaseDocument) -> CaseDocument:
    """ADMIN action: put a quarantined document back into the pipeline."""
    document.quarantined = False
    document.quarantined_at = None if hasattr(document, "quarantined_at") else None
    document.ingestion_status = IngestionStatus.PENDING
    document.retry_count = 0
    document.failure_reason = None
    await session.flush()
    return document


async def discard_quarantined(session: AsyncSession, document: CaseDocument) -> CaseDocument:
    """ADMIN action: soft-delete a quarantined document (never a row delete)."""
    document.is_deleted = True
    document.quarantined = False
    await session.flush()
    return document


def settings_snapshot() -> Settings:
    return get_settings()
