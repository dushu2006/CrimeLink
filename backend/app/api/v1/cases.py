"""Case endpoints (PRD 10)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case
from app.db.session import get_db_session
from app.domain.enums import CaseStatus
from app.errors import ValidationFailedError
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
from app.services import resolution as resolution_service

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseCreate(BaseModel):
    case_number: str = Field(min_length=3, max_length=120)
    title: str = Field(min_length=3, max_length=500)
    jurisdiction_id: str | None = None


class CaseStatusUpdate(BaseModel):
    status: CaseStatus


@router.post("", status_code=201)
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"case:{result['id']}",
    case_id=lambda result, **kw: result["id"],
    details=lambda result, **kw: {"case_number": result["case_number"], "action": "create"},
)
async def create_case(
    payload: CaseCreate,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    case: Case = await case_service.create_case(
        session,
        principal=principal,
        case_number=payload.case_number.strip(),
        title=payload.title.strip(),
        jurisdiction_id=payload.jurisdiction_id,
    )
    return {
        "id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "jurisdiction_id": case.jurisdiction_id,
        "status": case.status.value,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


@router.get("")
async def list_cases(
    principal: Principal = Depends(get_principal),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = await case_service.case_summaries(session, scope, limit=limit, offset=offset)
    return {"items": rows, "count": len(rows)}


@router.get("/{case_id}")
async def get_case(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    case = await case_service.require_case(session, scope, case_id)
    documents = await document_service.list_documents(session, case.id)
    pending = await resolution_service.list_queue(
        session, scope, case_id=case.id, status=None, limit=500
    )
    return {
        "id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "jurisdiction_id": case.jurisdiction_id,
        "status": case.status.value,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "document_count": len(documents),
        "pending_review_count": sum(1 for i in pending if i["status"] == "PENDING"),
        "review_sla": resolution_service.sla_summary(pending),
    }


@router.patch("/{case_id}/status")
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"case:{kw.get('case_id')}",
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {"status": result["status"]},
)
async def update_status(
    case_id: str,
    payload: CaseStatusUpdate,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    case = await case_service.require_case(session, scope, case_id)
    if case.status == CaseStatus.CLOSED and payload.status != CaseStatus.CLOSED:
        raise ValidationFailedError(
            "A closed case is read-only under the retention policy."
        )
    await case_service.update_status(session, case, payload.status)
    return {"id": case.id, "status": case.status.value}


@router.get("/{case_id}/timeline")
async def timeline(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    from_ts: str | None = None,
    to_ts: str | None = None,
    participant: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    from app.services.graph_service import GraphService

    events = await GraphService().timeline(
        session,
        scope,
        case_id,
        from_ts=from_ts,
        to_ts=to_ts,
        participant=participant,
        limit=limit,
    )
    return {"case_id": case_id, "events": events, "count": len(events)}
