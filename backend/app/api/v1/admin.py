"""Administration: audit trail, quarantine, users, thresholds (PRD 10 / 12 / 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.config import get_settings
from app.db.models import CaseDocument
from app.db.session import get_db_session
from app.domain.enums import AuditAction, Role
from app.errors import NotFoundError, ValidationFailedError
from app.security.deps import (
    AuditRecorder,
    Principal,
    audited,
    get_audit_recorder,
    get_principal,
    require_roles,
)
from app.services import admin as admin_service
from app.services import documents as document_service

router = APIRouter(prefix="/admin", tags=["administration"])


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@router.get("/audit/search")
async def audit_search(
    user_id: str | None = None,
    badge_number: str | None = None,
    action: str | None = None,
    case_id: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items = await admin_service.audit_search(
        session,
        user_id=user_id,
        badge_number=badge_number,
        action=action,
        case_id=case_id,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/audit/verify")
async def audit_verify(
    limit: int | None = Query(None, ge=1, le=100000),
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Single linear pass that detects any tampering with the audit chain."""
    return await admin_service.audit_verify(session, limit=limit)


@router.get("/audit/anomalies")
async def audit_anomalies(
    days: int = Query(7, ge=1, le=90),
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await admin_service.audit_anomalies(session, days=days)


@router.get("/audit/actions")
async def audit_actions(principal: Principal = Depends(get_principal)) -> dict:
    return {"items": [a.value for a in AuditAction]}


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


@router.get("/quarantine")
async def quarantine_list(
    case_id: str | None = None,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    documents = await document_service.quarantine_list(session, case_id)
    return {
        "items": [document_service.document_row(d) for d in documents],
        "count": len(documents),
    }


@router.post("/quarantine/{doc_id}/release")
@audited(
    "QUARANTINE_RELEASE",
    target=lambda result, **kw: f"document:{kw.get('doc_id')}",
    case_id=lambda result, **kw: result.get("case_id"),
    details=lambda result, **kw: {"action": "release"},
)
async def quarantine_release(
    doc_id: str,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        raise NotFoundError("Document not found.")
    await document_service.release_from_quarantine(session, document)
    container = _container()
    container.broker.dispatch_document_pipeline(
        job_id=_new_job(session, document, principal),
        doc_id=document.id,
        case_id=document.case_id,
        trace_id="",
        user_id=principal.id,
    )
    return {
        "document_id": document.id,
        "case_id": document.case_id,
        "status": document.ingestion_status.value,
        "message": "Released from quarantine and re-queued for processing.",
    }


@router.post("/quarantine/{doc_id}/discard")
@audited(
    "QUARANTINE_RELEASE",
    target=lambda result, **kw: f"document:{kw.get('doc_id')}",
    case_id=lambda result, **kw: result.get("case_id"),
    details=lambda result, **kw: {"action": "discard", "soft_delete": True},
)
async def quarantine_discard(
    doc_id: str,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        raise NotFoundError("Document not found.")
    await document_service.discard_quarantined(session, document)
    return {
        "document_id": document.id,
        "case_id": document.case_id,
        "status": "DISCARDED",
        "message": "Document soft-deleted. No row is ever physically removed.",
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users")
async def users(
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items = await admin_service.list_users(session)
    return {"items": items, "count": len(items)}


class UserCreate(BaseModel):
    badge_number: str = Field(min_length=2, max_length=64)
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="VIEWER")
    station_id: str = Field(min_length=1, max_length=64)
    jurisdiction_id: str = Field(min_length=1, max_length=64)


@router.post("/users", status_code=201)
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"user:{result['id']}",
    details=lambda result, **kw: {"badge_number": result["badge_number"]},
)
async def create_user(
    payload: UserCreate,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    try:
        role = Role(payload.role.strip().upper())
    except ValueError as exc:
        raise ValidationFailedError(
            f"Unknown role '{payload.role}'. Valid roles: "
            + ", ".join(r.value for r in Role)
        ) from exc
    user = await admin_service.create_user(
        session,
        principal=principal,
        badge_number=payload.badge_number.strip(),
        full_name=payload.full_name.strip(),
        password=payload.password,
        role=role,
        station_id=payload.station_id.strip(),
        jurisdiction_id=payload.jurisdiction_id.strip(),
    )
    return {
        "id": user.id,
        "badge_number": user.badge_number,
        "full_name": user.full_name,
        "role": user.role.value,
        "jurisdiction_id": user.jurisdiction_id,
    }


# ---------------------------------------------------------------------------
# Synthetic development data (generate / external corpus)
# ---------------------------------------------------------------------------


class SyntheticIngestIn(BaseModel):
    """Trigger body for a synthetic-data ingestion run.

    ``adapter`` selects the mode (``generate`` | ``external``); when omitted,
    ``CRIMELINK_SYNTHETIC_DATA_MODE`` decides.  ``root`` overrides
    ``CRIMELINK_SYNTHETIC_DATA_ROOT`` for the external corpus only.
    """

    adapter: str | None = Field(default=None, pattern="^(generate|external)$")
    root: str | None = None
    seed: int | None = None
    persons: int | None = Field(default=None, ge=1, le=10000)
    cases: int | None = Field(default=None, ge=1, le=1000)
    wait_seconds: float = Field(default=0, ge=0, le=3600)
    yes_i_am_sure: bool = False


@router.get("/synthetic/adapters")
async def synthetic_adapters(
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """List registered source adapters and the configured synthetic mode."""
    from app.adapters.sources import available_adapters, get_source_adapter

    items = []
    for name in available_adapters():
        try:
            items.append(get_source_adapter(name).describe())
        except Exception as exc:  # noqa: BLE001 - one bad adapter must not hide the rest
            items.append({"name": name, "error": str(exc)})
    return {
        "synthetic_data_mode": get_settings().synthetic_data_mode,
        "synthetic_data_root": str(get_settings().resolved_synthetic_data_root),
        "items": items,
    }


@router.get("/synthetic/status")
async def synthetic_status(
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Live dataset detection and ingestion progress. Never invents counts."""
    from app.synthetic_corpus.external import corpus_status

    return await corpus_status(container=_container())


@router.get("/synthetic/external/preview")
async def synthetic_external_preview(
    root: str | None = None,
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Dry-run: discover/validate/classify the external corpus, writing nothing."""
    from app.adapters.sources import get_source_adapter

    adapter = get_source_adapter("synthetic_external", root=root)
    scan = adapter.scan()
    payload = scan.summary()
    files = scan.files
    payload["truncated"] = len(files) > 500
    payload["files"] = [
        {
            "relative_path": f.relative_path,
            "status": f.status,
            "document_type": f.document_type.value if f.document_type else None,
            "case_key": f.case_key,
            "size_bytes": f.size_bytes,
            "reason": f.reason,
        }
        for f in files[:500]
    ]
    return payload


@router.post("/synthetic/ingest")
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"synthetic:{result.get('mode', result.get('adapter', 'generate'))}",
    details=lambda result, **kw: {
        "adapter": result.get("mode", "generate"),
        "records_ingested": result.get("records_ingested", result.get("ingested_documents")),
        "records_skipped_duplicates": result.get("records_skipped_duplicates"),
    },
)
async def synthetic_ingest(
    payload: SyntheticIngestIn,
    principal: Principal = Depends(require_roles("ADMIN")),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Explicitly ingest synthetic data through the standard pipeline.

    This is the API counterpart of ``python -m app.cli ingest-synthetic``; it
    never runs at startup.  With the embedded broker the six-stage pipeline
    processes documents in the background after the call returns.
    """
    settings = get_settings()
    mode = payload.adapter or settings.synthetic_data_mode
    if mode == "external":
        from app.adapters.sources.synthetic_external import ExternalCorpusError
        from app.synthetic_corpus.external import ingest_external_corpus

        try:
            report = await ingest_external_corpus(
                root=payload.root,
                safety_confirmed=payload.yes_i_am_sure,
                container=_container(),
            )
        except (ExternalCorpusError, RuntimeError) as exc:
            raise ValidationFailedError(str(exc)) from exc
        if payload.wait_seconds and report.uploaded:
            from app.synthetic_corpus.external import await_pipeline_quiet

            report.pipeline = await await_pipeline_quiet(
                _container(), report, timeout_seconds=payload.wait_seconds
            )
        return report.to_dict()
    if mode == "generate":
        from app.synthetic_corpus.generate import CorpusOptions, generate_corpus

        opts = CorpusOptions.from_settings(settings)
        if payload.seed is not None:
            opts.seed = payload.seed
        if payload.persons is not None:
            opts.person_count = payload.persons
        if payload.cases is not None:
            opts.case_count = payload.cases
        try:
            result = await generate_corpus(opts, safety_confirmed=payload.yes_i_am_sure)
        except RuntimeError as exc:
            raise ValidationFailedError(str(exc)) from exc
        result = dict(result)
        result.setdefault("mode", "generate")
        return result
    raise ValidationFailedError(
        f"Unknown synthetic adapter '{mode}'. Valid values: generate, external."
    )


# ---------------------------------------------------------------------------
# Detection thresholds
# ---------------------------------------------------------------------------


@router.get("/thresholds")
async def thresholds(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    values = await admin_service.get_thresholds(session)
    return {
        "items": [
            {"key": key, "value": value, "description": admin_service.THRESHOLD_HELP.get(key, "")}
            for key, value in values.items()
        ]
    }


class ThresholdIn(BaseModel):
    key: str
    value: float = Field(gt=0)


@router.post("/thresholds")
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"threshold:{result['key']}",
    details=lambda result, **kw: {"value": result["value"]},
)
async def set_threshold(
    payload: ThresholdIn,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    return await admin_service.set_threshold(
        session, principal=principal, key=payload.key, value=payload.value
    )


@router.get("/overview")
async def overview(
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    counts = await admin_service.counts(session)
    counts["graph"] = _container().graph_store.stats()
    counts["audit_head"] = await audit_service.head_hash(session)
    return counts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _container():
    from app.container import get_container

    return get_container()


def _new_job(session: AsyncSession, document: CaseDocument, principal: Principal) -> str:
    """Create and return a fresh ingestion job id for a re-queued document."""
    from app.db.base import new_uuid
    from app.db.models import IngestionJob
    from app.logging import new_trace_id

    job = IngestionJob(
        id=new_uuid(),
        case_id=document.case_id,
        doc_id=document.id,
        trace_id=new_trace_id(),
        requested_by=principal.id,
    )
    session.add(job)
    return job.id


__all__ = ["router", "select"]
