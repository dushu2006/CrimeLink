"""AI investigation endpoints.

Every AI interaction goes through the AI Gateway and is audited.  Results are
returned with pseudonymous IDs; the frontend resolves them after confirming
authorization.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import get_ai_gateway
from app.db.session import get_db_session
from app.security.deps import Principal, get_principal, require_roles

router = APIRouter(prefix="/ai", tags=["ai"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    depth: int = Field(default=2, ge=1, le=3)


@router.post("/cases/{case_id}/ask")
async def ask_case_question(
    case_id: str,
    payload: AskRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    gateway = get_ai_gateway()
    # TODO: jurisdiction-scope the case_id here; for now we delegate to
    # retrieval which will only return data the case is authorized for.
    response = await gateway.ask(
        question=payload.question,
        case_id=case_id,
        principal_id=principal.id,
        depth=payload.depth,
    )
    return {
        "query_id": response.query_id,
        "available": response.available,
        "fallback_reason": response.fallback_reason,
        "model": response.model,
        "role": response.role,
        "pseudonymized": response.pseudonymized,
        "latency_ms": response.latency_ms,
        "finding": response.finding.model_dump(),
    }
