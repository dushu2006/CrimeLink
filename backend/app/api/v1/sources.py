"""Source and provenance navigation — the backend of the Source Viewer.

These endpoints answer one question precisely: *where exactly did this piece of
information come from?*  They return bounded windows of the real dataset files,
never whole files, and they enforce the same authorisation as ordinary resource
access — a source reference is not a bypass.

Access to a source that belongs to a case is authorised through that case, so an
investigator cannot read material outside their jurisdiction merely by holding
an evidence URL.  Every read is recorded as ``DOC_VIEW`` in the audit log.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseDocument, SourceReference
from app.db.session import get_db_session
from app.errors import NotFoundError
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    get_audit_recorder,
    get_principal,
    get_scope,
    require_roles,
)
from app.services import cases as case_service
from app.services import source_viewer

router = APIRouter(prefix="/sources", tags=["sources"])


def _reference_row(ref: SourceReference) -> dict:
    return {
        "id": ref.id,
        "doc_id": ref.doc_id,
        "case_id": ref.case_id,
        "origin_file": ref.origin_file,
        "source_type": ref.source_type,
        "record_id": ref.record_id,
        "row_number": ref.row_number,
        "field_names": list(ref.field_names or []),
        "field_values": dict(ref.field_values or {}),
        "page_number": ref.page_number,
        "line_start": ref.line_start,
        "line_end": ref.line_end,
        "text_start": ref.text_start,
        "text_end": ref.text_end,
        "excerpt": ref.excerpt,
    }


@router.get("/files")
async def dataset_files(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """List dataset files with the reference counts actually recorded for them."""
    from app.adapters.sources import get_source_adapter

    adapter = get_source_adapter("synthetic_external")
    scan = adapter.scan()

    counts = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(SourceReference.origin_file, func.count(SourceReference.id))
                .group_by(SourceReference.origin_file)
            )
        ).all()
    }

    items = []
    for entry in scan.files:
        items.append(
            {
                "path": entry.relative_path,
                "status": entry.status,
                "section": entry.section,
                "size_bytes": entry.size_bytes,
                "document_type": entry.document_type.value if entry.document_type else None,
                "reason": entry.reason,
                # Only operational material is openable; ground truth is not.
                "readable": entry.status in {"accepted", "reference"},
                "reference_count": counts.get(entry.relative_path, 0),
            }
        )
    summary = scan.summary()
    return {
        "root": summary["root"],
        "dataset_name": summary["dataset_name"],
        "ok": summary["ok"],
        "issues": summary["issues"],
        "warnings": summary["warnings"],
        "counts": summary["counts"],
        "items": items,
    }


@router.get("/reference/{reference_id}")
async def get_reference(
    reference_id: str,
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Open a stored source reference at its exact position."""
    ref = (
        await session.execute(
            select(SourceReference).where(SourceReference.id == reference_id)
        )
    ).scalar_one_or_none()
    if ref is None:
        raise NotFoundError("Source reference not found.")

    # Authorisation flows through the owning case, exactly as for the document.
    case = await case_service.require_case(session, scope, ref.case_id)
    document = await session.get(CaseDocument, ref.doc_id)

    window = source_viewer.read_window(
        ref.origin_file,
        row=ref.row_number,
        line_start=ref.line_start,
        line_end=ref.line_end,
        context=context,
    )
    recorder.record(
        "DOC_VIEW",
        target_resource=f"source:{ref.origin_file}",
        case_id=ref.case_id,
        details={
            "reference_id": ref.id,
            "row": ref.row_number,
            "record_id": ref.record_id,
        },
    )
    await recorder.flush()
    return {
        "reference": _reference_row(ref),
        "window": window.to_dict(),
        "case": {"id": case.id, "case_number": case.case_number},
        "document": (
            {
                "id": document.id,
                "filename": document.filename,
                "document_type": document.document_type.value,
            }
            if document is not None
            else None
        ),
    }


@router.get("/documents/{doc_id}/references")
async def document_references(
    doc_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Every source reference recorded for one ingested document."""
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        raise NotFoundError("Document not found.")
    await case_service.require_case(session, scope, document.case_id)

    total = (
        await session.execute(
            select(func.count(SourceReference.id)).where(SourceReference.doc_id == doc_id)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(SourceReference)
            .where(SourceReference.doc_id == doc_id)
            .order_by(SourceReference.row_number, SourceReference.text_start)
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return {
        "document_id": doc_id,
        "case_id": document.case_id,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": [_reference_row(r) for r in rows],
    }


@router.get("/lookup")
async def lookup_reference(
    origin_file: str = Query(..., description="Dataset-relative path"),
    record_id: str | None = Query(None),
    row: int | None = Query(None, ge=1),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Find stored references for an origin row — used to jump from a file to a case."""
    query = select(SourceReference).where(SourceReference.origin_file == origin_file)
    if record_id:
        query = query.where(SourceReference.record_id == record_id)
    if row is not None:
        query = query.where(SourceReference.row_number == row)
    rows = list((await session.execute(query.limit(50))).scalars())

    allowed = []
    for ref in rows:
        # Silently drop references the caller may not see, rather than leaking
        # their existence through a 403.
        try:
            await case_service.require_case(session, scope, ref.case_id)
        except Exception:  # noqa: BLE001
            continue
        allowed.append(_reference_row(ref))
    return {"origin_file": origin_file, "items": allowed, "count": len(allowed)}


@router.get("/file")
async def read_file(
    path: str = Query(..., description="Dataset-relative path"),
    row: int | None = Query(None, ge=1),
    line_start: int | None = Query(None, ge=1),
    line_end: int | None = Query(None, ge=1),
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    limit: int | None = Query(None, ge=1, le=source_viewer.MAX_WINDOW),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Open a dataset file directly at a position, for dataset exploration.

    Ground-truth and metadata files are refused: they are evaluation material,
    never investigator-visible evidence.
    """
    from app.adapters.sources.synthetic_external import NEVER_INGEST_COMPONENTS

    clean = path.split("#", 1)[0]
    parts = {
        p.lower().replace("-", "").replace("_", "") for p in clean.split("/")[:-1]
    }
    if parts & NEVER_INGEST_COMPONENTS:
        raise NotFoundError(
            "This file is evaluation-only material and is not available as evidence."
        )

    window = source_viewer.read_window(
        path,
        row=row,
        line_start=line_start,
        line_end=line_end,
        context=context,
        limit=limit,
    )
    recorder.record(
        "DOC_VIEW",
        target_resource=f"source:{clean}",
        details={"row": row, "line_start": line_start},
    )
    await recorder.flush()
    return window.to_dict()
