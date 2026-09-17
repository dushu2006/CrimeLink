"""Document upload, job tracking, evidence access and hash verification (PRD 7/10/12.5).

Upload ordering is deliberate and follows the PRD: **the original bytes go to
object storage first**, before the database row is created and before any
extraction runs.  If a later stage corrupts something, re-processing always
starts from the pristine original.

Upload returns ``202 Accepted`` with a job id immediately; the heavy work runs in
the background (PRD principle P2).
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.container import Container, get_container
from app.db.base import new_uuid, utcnow
from app.db.models import Case, CaseDocument, DocumentStageEvent, EvidenceCustodyEvent, IngestionJob
from app.domain.enums import (
    CustodyEventType,
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
    classification="CONFIDENTIAL",
    mime_type: str = "application/octet-stream",
    language_hint: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> tuple[CaseDocument, IngestionJob]:
    """Persist the original, record metadata, and enqueue the pipeline."""
    container = container or get_container()
    settings: Settings = container.settings
    from app.domain.enums import InformationClassification
    from app.security.classification import require_classification
    evidence_classification = InformationClassification(classification)
    require_classification(principal, evidence_classification)

    if not payload:
        raise ValidationFailedError("The uploaded file is empty.")
    # Treat the client filename as display metadata only.  Never allow a path
    # separator, dot-segment, NUL, or an absolute path into an object key.
    raw_filename = str(filename or "upload.bin").replace("\\", "/")
    safe_filename = PurePath(raw_filename).name
    if (
        safe_filename in {"", ".", ".."}
        or "\x00" in safe_filename
        or "/" in raw_filename
        or "\\" in str(filename)
        or safe_filename != raw_filename
    ):
        raise ValidationFailedError("Filename must be a single path-safe name.")
    filename = safe_filename
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
        classification=evidence_classification,
        uploaded_by=principal.id,
        source_metadata=dict(source_metadata or {}),
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
    # Custody is part of the same transaction as the evidence metadata.  A
    # document can never appear as stored without an auditable custody trail.
    session.add_all(
        [
            EvidenceCustodyEvent(
                evidence_id=doc_id,
                case_id=case.id,
                event_type=CustodyEventType.IMPORTED,
                actor_id=principal.id,
                object_hash=digest,
                location=storage_key,
                details={"filename": filename, "mime_type": mime_type},
            ),
            EvidenceCustodyEvent(
                evidence_id=doc_id,
                case_id=case.id,
                event_type=CustodyEventType.STORED,
                actor_id=principal.id,
                object_hash=digest,
                location=storage_key,
                details={"storage_backend": container.object_store.backend_name},
            ),
        ]
    )
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
    meta = document.source_metadata or {}
    relative_path = meta.get("relative_path") or document.storage_key
    if relative_path:
        relative_path = relative_path.replace("\\", "/").lstrip("/")
    dataset_file_id = meta.get("dataset_file_id")
    return {
        "id": document.id,
        "case_id": document.case_id,
        "document_type": document.document_type.value,
        "filename": document.filename,
        "relative_path": relative_path,
        "storage_key": document.storage_key,
        "dataset_file_id": dataset_file_id,
        "language": document.language,
        "size_bytes": document.size_bytes,
        "content_hash": document.content_hash,
        "ingestion_status": document.ingestion_status.value,
        "ingestion_stage": document.ingestion_stage,
        "failure_reason": document.failure_reason,
        "source_confidence": document.source_confidence.value,
        "classification": document.classification.value,
        "integrity_state": "UNVERIFIED",
        "quarantined": document.quarantined,
        "retry_count": document.retry_count,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "evidence_url": container.object_store.presigned_url(
            container.settings.minio_bucket_documents,
            document.storage_key,
            container.settings.presigned_url_ttl_seconds,
        ),
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


async def requeue_stale_documents(
    *,
    container: Container | None = None,
) -> dict[str, Any]:
    """Re-dispatch documents whose pipeline died with the previous process.

    The embedded profile's job executor is in-process, so when the API process
    stops, every in-flight or queued job stops with it.  A restart then leaves
    documents stuck in ``PENDING`` (killed before the job ran), ``PROCESSING``
    (killed mid-pipeline) or ``FAILED`` with retry budget left (killed between
    a transient failure and its scheduled retry).  This is the recovery half of
    restart-safe ingestion: it re-queues exactly those documents and re-dispatches
    them through the *current* process's broker.

    Every stage of the pipeline is safe to re-run: source-reference persistence
    is an upsert keyed on the source position, and graph injection is keyed on
    provenance keys, so a re-run converges instead of duplicating.

    A job row left in ``QUEUED`` or ``RUNNING`` by the crashed process is *not*
    proof that work is still happening — the job executor lives in the same
    process that died.  So liveness is taken from the current process's broker:
    if the broker reports queued/running work, documents that still carry
    ``QUEUED``/``RUNNING`` job rows are genuinely scheduled and are left alone
    (this is what keeps an accidental concurrent invocation harmless); if the
    broker is provably idle, those rows are orphans of the dead process and the
    documents are re-queued.

    Idempotent: calling it twice when nothing is stale re-queues nothing.

    Operational note: recovery assumes one importer at a time (the embedded
    profile is a single SQLite database; two importers contend for the write
    lock anyway).  Do not start a second ingest or press Resume while another
    import process is actively draining — start the replacement *after* the
    previous process has exited, which is exactly the restart this recovers.
    """
    from app.pipeline.orchestrator import MAX_RETRIES as PIPELINE_MAX_RETRIES

    container = container or get_container()
    from app.db.session import async_session

    stale_statuses = [IngestionStatus.PENDING.value, IngestionStatus.PROCESSING.value]
    # A transient pipeline failure marks the document FAILED before its retry is
    # re-dispatched; if the process dies in that gap the document needs a nudge
    # too — but only while it still has retry budget (else it would have been
    # quarantined by the pipeline itself).
    stale_statuses.append(IngestionStatus.FAILED.value)

    # Broker liveness: ``pending_jobs`` is the number of queued+running jobs the
    # *current* process's executor is tracking.  ``None`` means the broker cannot
    # attest (e.g. the Celery adapter reports worker stats instead), in which
    # case RUNNING rows are treated as live work.
    try:
        pending_jobs = container.broker.health().get("pending_jobs")
    except Exception:  # noqa: BLE001 - a dead broker must not block recovery
        pending_jobs = None
    broker_provably_idle = pending_jobs == 0

    requeued: list[str] = []
    skipped_running = 0
    skipped_quarantined = 0

    async with async_session() as session:
        docs = list(
            (
                await session.execute(
                    select(CaseDocument).where(
                        CaseDocument.is_deleted.is_(False),
                        CaseDocument.quarantined.is_(False),
                        CaseDocument.ingestion_status.in_(stale_statuses),
                    )
                )
            ).scalars()
        )
        live_job_doc_ids: set[str] = set()
        orphan_jobs: list[IngestionJob] = []
        if broker_provably_idle:
            # Every QUEUED/RUNNING row is an orphan of the dead process; they
            # will be marked superseded once their document is re-queued.
            orphan_jobs = list(
                (
                    await session.execute(
                        select(IngestionJob).where(
                            IngestionJob.status.in_(
                                [JobStatus.QUEUED, JobStatus.RUNNING]
                            )
                        )
                    )
                ).scalars()
            )
        else:
            live_job_doc_ids = set(
                (
                    await session.execute(
                        select(IngestionJob.doc_id).where(
                            IngestionJob.status.in_(
                                [JobStatus.QUEUED, JobStatus.RUNNING]
                            )
                        )
                    )
                ).scalars()
            )

        orphan_by_doc: dict[str, list[IngestionJob]] = {}
        for job in orphan_jobs:
            orphan_by_doc.setdefault(job.doc_id, []).append(job)

        jobs: list[IngestionJob] = []
        for doc in docs:
            if not broker_provably_idle and doc.id in live_job_doc_ids:
                skipped_running += 1
                continue
            if doc.ingestion_status == IngestionStatus.FAILED and int(
                doc.retry_count or 0
            ) >= PIPELINE_MAX_RETRIES:
                # The pipeline would have quarantined this after its final retry;
                # treat it as terminal rather than looping forever.
                skipped_quarantined += 1
                continue
            for orphan in orphan_by_doc.get(doc.id, []):
                orphan.status = JobStatus.FAILED
                orphan.error = (
                    "Superseded by requeue_stale_documents: the process that "
                    "owned this job died before it completed."
                )
            doc.ingestion_status = IngestionStatus.PENDING
            doc.ingestion_stage = 0
            doc.failure_reason = None
            job = IngestionJob(
                id=new_uuid(),
                case_id=doc.case_id,
                doc_id=doc.id,
                status=JobStatus.QUEUED,
                trace_id=new_trace_id(),
                requested_by=doc.uploaded_by,
            )
            session.add(job)
            jobs.append(job)
            requeued.append(doc.id)
        # Commit the PENDING reset and QUEUED job rows before dispatching so a
        # worker thread always observes committed state.
        await session.commit()

        for job in jobs:
            container.broker.dispatch_document_pipeline(
                job_id=job.id,
                doc_id=job.doc_id,
                case_id=job.case_id,
                trace_id=job.trace_id or "",
                user_id=job.requested_by or "",
            )
            log.info(
                "documents.requeued_stale",
                doc_id=job.doc_id,
                job_id=job.id,
                case_id=job.case_id,
            )

    return {
        "requeued": requeued,
        "requeued_count": len(requeued),
        "skipped_running": skipped_running,
        "skipped_terminal_failed": skipped_quarantined,
        "orphans_superseded": sum(len(v) for v in orphan_by_doc.values()),
        "broker_idle": broker_provably_idle,
    }


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


async def provenance_payload(
    session: AsyncSession,
    container: Container,
    document: CaseDocument,
) -> dict[str, Any]:
    """The whole traceable chain for one evidence document.

    ``Finding → Claim → Evidence → Source record → Original file → Case``.

    Every link is resolved from stored data, and every provenance "check" is
    *computed here* rather than asserted by the UI: a check that is not backed
    by a record says so explicitly instead of rendering a green tick.  That is
    the difference between a provenance panel and a decorative one.
    """
    from urllib.parse import quote

    from sqlalchemy import select

    from app.db.models import Case, DatasetFile, InvestigationFinding, SourceReference
    from app.domain.enums import canonical_label
    from app.domain.provenance import content_hash

    meta = document.source_metadata or {}
    relative_path = (meta.get("relative_path") or document.storage_key or "")
    relative_path = relative_path.replace("\\", "/").lstrip("/")

    # --- CASE ---------------------------------------------------------------
    case = await session.get(Case, document.case_id)
    case_row = (
        {
            "id": case.id,
            "case_number": case.case_number,
            "title": case.title,
            "status": case.status.value,
            "jurisdiction_id": case.jurisdiction_id,
        }
        if case is not None
        else None
    )

    # --- SOURCE RECORDS -----------------------------------------------------
    references = list(
        (
            await session.execute(
                select(SourceReference)
                .where(SourceReference.doc_id == document.id)
                .order_by(SourceReference.row_number, SourceReference.text_start)
                .limit(50)
            )
        ).scalars()
    )
    reference_rows = [
        {
            "id": ref.id,
            "origin_file": ref.origin_file,
            "record_id": ref.record_id,
            "row_number": ref.row_number,
            "line_start": ref.line_start,
            "line_end": ref.line_end,
            "excerpt": ref.excerpt,
        }
        for ref in references
    ]

    dataset_file = (
        await session.execute(
            select(DatasetFile).where(DatasetFile.doc_id == document.id).limit(1)
        )
    ).scalars().first()

    # --- ORIGINAL FILE ------------------------------------------------------
    # Actually read the bytes.  "Record available" means the bytes were read,
    # not that a row points somewhere.
    file_row: dict[str, Any] = {
        "storage_key": document.storage_key,
        "relative_path": relative_path or None,
        "available": False,
        "size_bytes": None,
        "media_type": document.mime_type,
        "hash_matches": None,
        "detail": "No storage key recorded for this document.",
    }
    if document.storage_key:
        try:
            raw = container.object_store.get(
                container.settings.minio_bucket_documents, document.storage_key
            )
            computed = content_hash(raw)
            file_row.update(
                {
                    "available": True,
                    "size_bytes": len(raw),
                    "hash_matches": computed == document.content_hash,
                    "computed_hash": computed,
                    "detail": (
                        "Bytes read from object storage."
                        if computed == document.content_hash
                        else "Bytes read, but the SHA-256 no longer matches the recorded hash."
                    ),
                    "preview_url": (
                        f"/api/v1/sources/preview?path={quote(relative_path, safe='')}"
                        if relative_path
                        else None
                    ),
                    "raw_url": (
                        f"/api/v1/sources/raw?path={quote(relative_path, safe='')}"
                        if relative_path
                        else None
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, never guess
            file_row["detail"] = f"The stored object could not be read: {exc}"

    # --- FINDINGS THAT CITE THIS EVIDENCE -----------------------------------
    findings = list(
        (
            await session.execute(
                select(InvestigationFinding).where(
                    InvestigationFinding.case_id == document.case_id
                )
            )
        ).scalars()
    )
    finding_rows = []
    for finding in findings:
        cited = [
            str(item.get("doc_id"))
            for item in (finding.evidence or [])
            if isinstance(item, dict) and item.get("doc_id")
        ]
        if document.id not in cited and document.id not in (finding.evidence or []):
            continue
        finding_rows.append(
            {
                "id": finding.id,
                "title": finding.title,
                "finding_type": finding.finding_type,
                "status": finding.status,
                "confidence": finding.confidence,
                "narrative": finding.narrative,
            }
        )

    # --- PEOPLE THIS DOCUMENT IS RECORDED AGAINST ---------------------------
    people_rows: list[dict[str, Any]] = []
    try:
        snapshot = container.graph_store.snapshot(document.case_id, include_staging=False)
        for node in snapshot.nodes.values():
            if canonical_label(node.label) != "PERSON":
                continue
            props = node.properties or {}
            docs = {str(d) for d in (props.get("source_doc_ids") or [])}
            if props.get("source_doc_id"):
                docs.add(str(props["source_doc_id"]))
            if document.id not in docs:
                continue
            status = props.get("criminal_status")
            people_rows.append(
                {
                    "provenance_key": node.provenance_key,
                    "name": props.get("display_name") or props.get("full_name") or props.get("name")
                    or node.provenance_key,
                    "role": props.get("role"),
                    "criminal_status": status,
                    "is_criminal": str(status or "").strip().lower()
                    in {"confirmed", "convicted", "accused", "chargesheeted", "criminal"},
                }
            )
    except Exception:  # noqa: BLE001 - the chain still holds without the graph
        people_rows = []

    # --- COMPUTED PROVENANCE CHECKS -----------------------------------------
    checks = {
        "source_verified": {
            "ok": document.source_confidence.value == "VERIFIED"
            and document.ingestion_status.value == "COMPLETE",
            "detail": (
                f"source_confidence={document.source_confidence.value}, "
                f"ingestion_status={document.ingestion_status.value}"
            ),
        },
        "record_available": {
            "ok": bool(file_row["available"]),
            "detail": file_row["detail"],
        },
        "traceable_to_original": {
            "ok": bool(relative_path) and (bool(references) or dataset_file is not None),
            "detail": (
                f"{len(references)} source reference(s)"
                + (", dataset file registered" if dataset_file is not None else "")
                if (references or dataset_file is not None)
                else "No source reference or dataset file row points at this document."
            ),
        },
        "hash_matches": {
            "ok": file_row.get("hash_matches"),
            "detail": (
                "recorded hash re-computed from the stored bytes and it matches"
                if file_row.get("hash_matches")
                else "hash could not be confirmed"
            ),
        },
    }

    return {
        "document": {
            "id": document.id,
            "filename": document.filename,
            "document_type": document.document_type.value,
            "media_type": document.mime_type,
            "size_bytes": document.size_bytes,
            "content_hash": document.content_hash,
            "source_confidence": document.source_confidence.value,
            "ingestion_status": document.ingestion_status.value,
            "classification": document.classification.value,
            "quarantined": document.quarantined,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "evidence_id": meta.get("evidence_id"),
            "title": meta.get("title"),
        },
        "case": case_row,
        "file": file_row,
        "source_references": reference_rows,
        "dataset_file": (
            {"id": dataset_file.id, "relative_path": dataset_file.relative_path}
            if dataset_file is not None
            else None
        ),
        "findings": finding_rows,
        "people": people_rows,
        "checks": checks,
        "chain": [
            {"step": "CASE", "resolved": case_row is not None, "ref": case_row["case_number"] if case_row else None},
            {"step": "EVIDENCE", "resolved": True, "ref": document.id},
            {"step": "SOURCE_RECORD", "resolved": bool(reference_rows), "ref": reference_rows[0]["origin_file"] if reference_rows else None},
            {"step": "ORIGINAL_FILE", "resolved": bool(file_row["available"]), "ref": relative_path or None},
            {"step": "FINDING", "resolved": bool(finding_rows), "ref": finding_rows[0]["id"] if finding_rows else None},
        ],
    }
