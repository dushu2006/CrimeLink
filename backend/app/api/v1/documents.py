"""Document upload, listing and evidence access (PRD 7 / 10 / 12.5).

Upload returns ``202 Accepted`` with a job id immediately — parsing, OCR and NLP
never block the investigator (PRD principle P2).  Progress is available by
polling ``GET /jobs/{job_id}`` or over the live WebSocket channel.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.models import CaseDocument
from app.db.session import get_db_session
from app.domain.enums import DocumentType, SourceConfidence
from app.errors import NotFoundError, ValidationFailedError
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    audited,
    get_audit_recorder,
    get_principal,
    get_scope,
    require_roles,
)
from app.services import cases as case_service
from app.services import documents as document_service

router = APIRouter(tags=["documents"])

_ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
}


@router.post("/cases/{case_id}/documents", status_code=202)
@audited(
    "DOC_UPLOAD",
    target=lambda result, **kw: f"document:{result['document_id']}",
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {
        "filename": result["filename"],
        "document_type": result["document_type"],
        "content_hash": result["content_hash"],
        "job_id": result["job_id"],
    },
)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    source_confidence: str = Form("UNVERIFIED"),
    language: str | None = Form(None),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    case = await case_service.require_case(session, scope, case_id)

    try:
        doc_type = DocumentType(document_type.strip().upper())
    except ValueError as exc:
        raise ValidationFailedError(
            f"Unknown document type '{document_type}'. Valid types: "
            + ", ".join(t.value for t in DocumentType)
        ) from exc
    try:
        confidence = SourceConfidence(source_confidence.strip().upper())
    except ValueError as exc:
        raise ValidationFailedError(
            f"Unknown source confidence '{source_confidence}'."
        ) from exc

    payload = await file.read()
    filename = file.filename or "upload.bin"
    if file.content_type and file.content_type not in _ALLOWED_MIME:
        raise ValidationFailedError(f"Unsupported file type '{file.content_type}'.")

    document, job = await document_service.upload_document(
        session,
        case=case,
        principal=principal,
        filename=filename,
        payload=payload,
        document_type=doc_type,
        source_confidence=confidence,
        mime_type=file.content_type or "application/octet-stream",
        language_hint=language,
    )
    return {
        "document_id": document.id,
        "job_id": job.id,
        "case_id": case.id,
        "filename": document.filename,
        "document_type": document.document_type.value,
        "content_hash": document.content_hash,
        "status": document.ingestion_status.value,
        "message": "Accepted for processing. Poll /api/v1/jobs/{job_id} for progress.",
    }


@router.get("/cases/{case_id}/documents")
async def list_documents(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    case = await case_service.require_case(session, scope, case_id)
    documents = await document_service.list_documents(session, case.id)
    return {
        "case_id": case.id,
        "items": [document_service.document_row(d) for d in documents],
        "count": len(documents),
    }


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    from sqlalchemy import select

    from app.db.models import CaseDocument as _CD

    document = (
        await session.execute(select(_CD).where(_CD.id == doc_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Document not found.")
    await case_service.require_case(session, scope, document.case_id)
    return document_service.document_row(document)


@router.get("/evidence/{doc_id}")
async def evidence(
    doc_id: str,
    span: str | None = Query(
        None, description="Optional character offsets as 'start,end' to highlight"
    ),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Metadata + a 15-minute signed link + the highlighted source sentence."""
    from sqlalchemy import select

    from app.db.models import CaseDocument as _CD

    document = (
        await session.execute(select(_CD).where(_CD.id == doc_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Document not found.")
    await case_service.require_case(session, scope, document.case_id)

    parsed: tuple[int, int] | None = None
    if span:
        try:
            start, end = (int(part) for part in span.split(",", 1))
            parsed = (start, end)
        except ValueError:
            parsed = None

    payload = await document_service.evidence_payload(
        session, get_container(), document, parsed
    )
    recorder.record(
        "DOC_VIEW",
        target_resource=f"document:{doc_id}",
        case_id=document.case_id,
        details={"span": list(parsed) if parsed else None},
    )
    await recorder.flush()
    return payload


@router.get("/evidence/{doc_id}/verify")
async def verify_evidence(
    doc_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Re-compute the stored document's SHA-256 (court-preparation endpoint)."""
    from sqlalchemy import select

    from app.db.models import CaseDocument as _CD

    document = (
        await session.execute(select(_CD).where(_CD.id == doc_id))
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError("Document not found.")
    await case_service.require_case(session, scope, document.case_id)
    return await document_service.verify_document_hash(get_container(), document)
