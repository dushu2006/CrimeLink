"""Model registry administration endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelRegistryEntry
from app.db.session import get_db_session
from app.security.deps import Principal, get_principal, require_roles
from app.services import industry
from app.api.v1.industry import ModelRegisterRequest

router = APIRouter(prefix="/admin/models", tags=["model-registry"])


@router.get("")
async def list_models(principal: Principal = Depends(require_roles("AUDITOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), session: AsyncSession = Depends(get_db_session)):
    rows = list((await session.execute(select(ModelRegistryEntry).order_by(ModelRegistryEntry.role, ModelRegistryEntry.created_at.desc()))).scalars().all())
    return {"items": [industry.model_row(row) for row in rows]}


@router.post("", status_code=201)
async def register_model(payload: ModelRegisterRequest, principal: Principal = Depends(require_roles("ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), session: AsyncSession = Depends(get_db_session)):
    row = await industry.register_model(session, payload.model_dump())
    return industry.model_row(row)
