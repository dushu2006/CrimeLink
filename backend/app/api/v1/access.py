"""Cross-jurisdiction access requests (PRD 12.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.security.deps import (
    AuditRecorder,
    Principal,
    audited,
    get_audit_recorder,
    get_principal,
    require_roles,
)
from app.services import access as access_service

router = APIRouter(prefix="/access", tags=["access requests"])


class AccessRequestIn(BaseModel):
    target_jurisdiction: str = Field(min_length=1, max_length=64)
    case_id: str | None = None
    reason: str = Field(min_length=10, max_length=2000)


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)
    grant_days: int | None = Field(default=None, ge=1, le=90)


@router.post("/request", status_code=201)
@audited(
    "ACCESS_REQUEST",
    target=lambda result, **kw: f"jurisdiction:{result['target_jurisdiction']}",
    case_id=lambda result, **kw: result.get("case_id"),
    details=lambda result, **kw: {"reason": result["reason"]},
)
async def request_access(
    payload: AccessRequestIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    request = await access_service.request_access(
        session,
        principal=principal,
        target_jurisdiction=payload.target_jurisdiction.strip(),
        reason=payload.reason,
        case_id=payload.case_id,
    )
    return {
        "id": request.id,
        "target_jurisdiction": request.target_jurisdiction,
        "case_id": request.case_id,
        "reason": request.reason,
        "status": request.status.value,
    }


@router.post("/approve/{request_id}")
@audited(
    "ACCESS_APPROVAL",
    target=lambda result, **kw: f"access_request:{kw.get('request_id')}",
    details=lambda result, **kw: {
        "status": result["status"],
        "expires_at": result.get("expires_at"),
    },
)
async def decide(
    request_id: str,
    payload: DecisionIn,
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    request = await access_service.decide(
        session,
        principal=principal,
        request_id=request_id,
        approve=payload.approve,
        note=payload.note,
        grant_days=payload.grant_days,
    )
    return {
        "id": request.id,
        "status": request.status.value,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "decision_note": request.decision_note,
    }


@router.get("/requests")
async def list_requests(
    mine: bool = Query(True),
    pending_for_me: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    items = await access_service.list_requests(
        session, principal=principal, mine=mine, pending_for_me=pending_for_me, limit=limit
    )
    return {"items": items, "count": len(items)}
