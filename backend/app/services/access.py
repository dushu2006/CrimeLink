"""Cross-jurisdiction access requests (PRD 12.4).

Indian policing is jurisdiction-bounded by design: a Kota station officer does
not browse a Jaipur case because they are curious.  Cross-jurisdiction access in
CrimeLink is therefore:

* **requested in writing** — a mandatory reason is stored with the request;
* **approved by an administrator of the target jurisdiction** — not by the
  requester's own chain of command;
* **time-boxed** — default 7 days, with automatic expiry and no renewals without
  a fresh request;
* **audited twice** — at request and at approval, and every access taken under
  the grant is logged.

There are no permanent cross-jurisdiction grants anywhere in the system.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import Case, JurisdictionAccessRequest
from app.domain.enums import AccessRequestStatus, Role
from app.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from app.security.deps import Principal

DEFAULT_GRANT_DAYS = 7


async def request_access(
    session: AsyncSession,
    *,
    principal: Principal,
    target_jurisdiction: str,
    reason: str,
    case_id: str | None = None,
) -> JurisdictionAccessRequest:
    if not reason or len(reason.strip()) < 10:
        raise ValidationFailedError(
            "A written justification of at least 10 characters is required for "
            "cross-jurisdiction access."
        )
    if target_jurisdiction == principal.jurisdiction_id:
        raise ValidationFailedError(
            "You already have access to your own jurisdiction."
        )
    if case_id:
        case = await session.get(Case, case_id)
        if case is None:
            raise NotFoundError("Case not found.")
        jurisdiction = (
            case.jurisdiction_id.value
            if hasattr(case.jurisdiction_id, "value")
            else str(case.jurisdiction_id)
        )
        if jurisdiction != target_jurisdiction:
            raise ValidationFailedError(
                "The requested case does not belong to the target jurisdiction."
            )

    request = JurisdictionAccessRequest(
        requester_id=principal.id,
        target_jurisdiction=target_jurisdiction,
        case_id=case_id,
        reason=reason.strip(),
        status=AccessRequestStatus.PENDING,
    )
    session.add(request)
    await session.flush()
    return request


async def decide(
    session: AsyncSession,
    *,
    principal: Principal,
    request_id: str,
    approve: bool,
    note: str | None = None,
    grant_days: int | None = None,
) -> JurisdictionAccessRequest:
    """Approve or deny a request.  Only an ADMIN of the target jurisdiction may."""
    request = await session.get(JurisdictionAccessRequest, request_id)
    if request is None:
        raise NotFoundError("Access request not found.")
    if request.status != AccessRequestStatus.PENDING:
        raise ValidationFailedError("This request has already been decided.")
    if principal.role is not Role.ADMIN or principal.jurisdiction_id != request.target_jurisdiction:
        raise PermissionDeniedError(
            "Only an administrator of the target jurisdiction can decide this request."
        )
    days = grant_days or DEFAULT_GRANT_DAYS
    request.status = (
        AccessRequestStatus.APPROVED if approve else AccessRequestStatus.DENIED
    )
    request.approved_by = principal.id
    request.decision_note = (note or "").strip() or None
    request.decided_at = utcnow()
    request.expires_at = utcnow() + timedelta(days=days) if approve else None
    await session.flush()
    return request


def _row(request: JurisdictionAccessRequest) -> dict:
    now = utcnow()
    expired = (
        request.status == AccessRequestStatus.APPROVED
        and request.expires_at is not None
        and request.expires_at < now
    )
    return {
        "id": request.id,
        "requester_id": request.requester_id,
        "target_jurisdiction": request.target_jurisdiction,
        "case_id": request.case_id,
        "reason": request.reason,
        "status": AccessRequestStatus.EXPIRED.value if expired else request.status.value,
        "approved_by": request.approved_by,
        "decision_note": request.decision_note,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
    }


async def list_requests(
    session: AsyncSession,
    *,
    principal: Principal,
    pending_for_me: bool = False,
    mine: bool = False,
    limit: int = 100,
) -> list[dict]:
    stmt = select(JurisdictionAccessRequest)
    if pending_for_me:
        # Requests awaiting *this* administrator's decision.
        if principal.role is not Role.ADMIN:
            return []
        stmt = stmt.where(
            JurisdictionAccessRequest.target_jurisdiction == principal.jurisdiction_id,
            JurisdictionAccessRequest.status == AccessRequestStatus.PENDING,
        )
    elif mine:
        stmt = stmt.where(JurisdictionAccessRequest.requester_id == principal.id)
    elif principal.role is not Role.ADMIN:
        stmt = stmt.where(JurisdictionAccessRequest.requester_id == principal.id)
    rows = (
        await session.execute(stmt.order_by(JurisdictionAccessRequest.created_at.desc()).limit(limit))
    ).scalars().all()
    return [_row(r) for r in rows]


def default_grant_days() -> int:
    return DEFAULT_GRANT_DAYS


def settings_snapshot():
    return get_settings()
