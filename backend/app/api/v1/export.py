"""Watermarked case-brief export (PRD 10 / 12.5).

Exporting is an ``EXPORT``-audited action restricted to INVESTIGATOR and ADMIN.
The PDF embeds the SHA-256 of every source document it cites, so the brief is
self-verifying outside the system.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.logging import get_logger
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    audited,
    get_audit_recorder,
    get_scope,
    require_roles,
)
from app.services import cases as case_service
from app.services.export import build_case_brief

log = get_logger("crimelink.api.export")
router = APIRouter(tags=["export"])


@router.get("/cases/{case_id}/export")
@audited(
    "EXPORT",
    target=lambda result, **kw: f"case:{kw.get('case_id')}",
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {"format": "pdf", "watermarked": True},
)
async def export_case(
    case_id: str,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> Response:
    case = await case_service.require_case(session, scope, case_id)
    pdf = await build_case_brief(session, case)
    filename = f"case-brief-{case.case_number.replace('/', '-')}.pdf"
    log.info("case.exported", case_id=case.id, bytes=len(pdf))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
