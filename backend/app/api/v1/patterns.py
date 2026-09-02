"""Review queue 2 — pattern findings (PRD 11.3 / 10).

Findings are always presented as candidates awaiting review, never as confirmed
accusations.  Escalate / dismiss both require a written rationale.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.domain.enums import PatternStatus, PatternType
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
from app.services import patterns as pattern_service

router = APIRouter(prefix="/patterns", tags=["patterns"])


class ReviewRequest(BaseModel):
    decision: str = Field(..., description="REVIEWED | DISMISSED | ESCALATED")
    note: str | None = Field(
        default=None, max_length=2000, description="Mandatory for DISMISSED / ESCALATED"
    )


@router.get("")
async def list_patterns(
    case_id: str | None = None,
    status: str | None = Query(None, description="NEW | REVIEWED | DISMISSED | ESCALATED"),
    pattern_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    resolved_status: PatternStatus | None = None
    if status:
        try:
            resolved_status = PatternStatus(status.strip().upper())
        except ValueError as exc:
            raise ValidationFailedError(f"Unknown status '{status}'.") from exc
    resolved_type: PatternType | None = None
    if pattern_type:
        try:
            resolved_type = PatternType(pattern_type.strip().upper())
        except ValueError as exc:
            raise ValidationFailedError(f"Unknown pattern type '{pattern_type}'.") from exc
    items = await pattern_service.list_patterns(
        session,
        scope,
        case_id=case_id,
        status=resolved_status,
        pattern_type=resolved_type,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.post("/{pattern_id}/review")
@audited(
    "PATTERN_REVIEW",
    target=lambda result, **kw: f"pattern:{kw.get('pattern_id')}",
    case_id=lambda result, **kw: result.get("case_id"),
    details=lambda result, **kw: {
        "decision": result.get("status"),
        "note": kw.get("payload").note if kw.get("payload") else None,
    },
)
async def review(
    pattern_id: str,
    payload: ReviewRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    try:
        decision = PatternStatus(payload.decision.strip().upper())
    except ValueError as exc:
        raise ValidationFailedError(
            "Decision must be REVIEWED, DISMISSED or ESCALATED."
        ) from exc
    return await pattern_service.review(
        session, scope, pattern_id, principal=principal, decision=decision, note=payload.note
    )


@router.get("/dismissal-report")
async def dismissal_report(
    case_id: str | None = None,
    days: int = Query(30, ge=1, le=365),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Alert-fatigue control: dismissal rate per pattern type (PRD 11.3)."""
    return await pattern_service.dismissal_report(
        session, scope, case_id=case_id, days=days
    )
