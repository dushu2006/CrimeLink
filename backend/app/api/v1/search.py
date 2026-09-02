"""Entity search (PRD 10).

Jurisdiction-scoped and paginated: a query never crosses a jurisdictional
boundary, because the scope filter is applied to the *case set* the search runs
over, not to the results afterwards.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.domain.enums import EntityType
from app.errors import ValidationFailedError
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    audited,
    get_audit_recorder,
    get_principal,
    get_scope,
)
from app.services.graph_service import GraphService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
@audited(
    "SEARCH",
    target=lambda result, **kw: kw.get("q"),
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {
        "q": kw.get("q"),
        "type": kw.get("entity_type"),
        "results": len(result.get("items", [])),
    },
)
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    type: str | None = Query(None, description="Person | Phone | Vehicle | ..."),
    case_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    entity_type: str | None = None
    if type:
        try:
            entity_type = EntityType(type).value
        except ValueError as exc:
            raise ValidationFailedError(
                f"Unknown entity type '{type}'. Valid types: "
                + ", ".join(t.value for t in EntityType)
            ) from exc
    items = await GraphService().search(
        session, scope, q, entity_type=entity_type, case_id=case_id, limit=limit
    )
    return {"query": q, "items": items, "count": len(items)}
