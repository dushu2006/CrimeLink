"""Review queue 1 — entity resolution decisions (PRD 9.2 / 10).

Merge and reject both require a mandatory written rationale: an investigator
cannot collapse two human identities into one with a single click.  Merges are
reversible, and rejections are tombstoned so the pair never comes back.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.domain.enums import MatchBasis, ResolutionStatus
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
from app.services import resolution as resolution_service

router = APIRouter(prefix="/resolution", tags=["entity resolution"])


class DecisionRequest(BaseModel):
    note: str = Field(
        min_length=5,
        max_length=2000,
        description="Mandatory written rationale recorded in the audit trail",
    )


@router.get("")
async def list_queue(
    case_id: str | None = None,
    status: str | None = Query(None, description="PENDING | MERGED | REJECTED"),
    limit: int = Query(100, ge=1, le=500),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    resolved_status: ResolutionStatus | None = None
    if status:
        try:
            resolved_status = ResolutionStatus(status.strip().upper())
        except ValueError as exc:
            raise ValidationFailedError(f"Unknown status '{status}'.") from exc
    items = await resolution_service.list_queue(
        session, scope, case_id=case_id, status=resolved_status, limit=limit
    )
    return {
        "items": items,
        "count": len(items),
        "sla": resolution_service.sla_summary(items),
    }


@router.post("/{queue_id}/merge")
@audited(
    "MERGE",
    target=lambda result, **kw: f"resolution:{kw.get('queue_id')}",
    details=lambda result, **kw: {
        "kept_key": result.get("kept_key"),
        "absorbed_key": result.get("absorbed_key"),
        "rerouted_edges": result.get("rerouted_edges"),
        "note": kw.get("payload").note if kw.get("payload") else None,
    },
)
async def merge(
    queue_id: str,
    payload: DecisionRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    return await resolution_service.merge(
        session, scope, queue_id, principal=principal, note=payload.note
    )


@router.post("/{queue_id}/reject")
@audited(
    "MERGE_REJECT",
    target=lambda result, **kw: f"resolution:{kw.get('queue_id')}",
    details=lambda result, **kw: {
        "note": kw.get("payload").note if kw.get("payload") else None
    },
)
async def reject(
    queue_id: str,
    payload: DecisionRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    return await resolution_service.reject(
        session, scope, queue_id, principal=principal, note=payload.note
    )


@router.post("/{queue_id}/unmerge")
@audited(
    "MERGE",
    target=lambda result, **kw: f"resolution:{kw.get('queue_id')}",
    details=lambda result, **kw: {
        "reversed": True,
        "note": kw.get("payload").note if kw.get("payload") else None,
    },
)
async def unmerge(
    queue_id: str,
    payload: DecisionRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Reverse a completed merge — wrongful merges must be recoverable."""
    return await resolution_service.unmerge(
        session, scope, queue_id, principal=principal, note=payload.note
    )


def _basis(value: str) -> MatchBasis:
    try:
        return MatchBasis(value)
    except ValueError as exc:
        raise ValidationFailedError(f"Unknown match basis '{value}'.") from exc
