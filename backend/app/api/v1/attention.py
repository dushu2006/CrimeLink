"""Attention Center — real data only.

Categories: Critical / Investigation / Data quality / Evidence
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.security.deps import JurisdictionScope, Principal, get_principal, get_scope
from app.services.attention_center import get_attention_center

router = APIRouter(prefix="/attention", tags=["attention"])


@router.get("")
async def attention(
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    return await get_attention_center(session, scope)


@router.get("/counts")
async def attention_counts(
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    data = await get_attention_center(session, scope)
    return {"counts": data.get("counts", {}), "total": data.get("total", 0)}
