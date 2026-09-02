"""Job status polling and the live WebSocket progress channel (PRD 10 / 13)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.session import get_db_session
from app.errors import NotFoundError
from app.security.deps import JurisdictionScope, Principal, get_principal, get_scope
from app.services import documents as document_service

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    job = await document_service.get_job(session, job_id)
    return await document_service.job_row(session, job)


@router.get("/cases/{case_id}/jobs")
async def list_jobs(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    from sqlalchemy import select

    from app.db.models import IngestionJob
    from app.services import cases as case_service

    await case_service.require_case(session, scope, case_id)
    jobs = (
        await session.execute(
            select(IngestionJob)
            .where(IngestionJob.case_id == case_id)
            .order_by(IngestionJob.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {"items": [await document_service.job_row(session, job) for job in jobs]}


@router.websocket("/ws/jobs/{case_id}")
async def job_stream(websocket: WebSocket, case_id: str) -> None:
    """Push per-document pipeline stage progress to the UI.

    Authentication over a WebSocket uses the access token as a query parameter,
    because browsers cannot set headers on the handshake.  The token is short
    lived (15 minutes) and the channel is scoped to one case.
    """
    from app.security.tokens import decode_access_token

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        decode_access_token(token)
    except Exception:  # noqa: BLE001
        await websocket.close(code=4401)
        return

    await websocket.accept()
    container = get_container()
    channel = f"case:{case_id}"
    try:
        async for message in container.event_bus.subscribe(channel):
            try:
                await websocket.send_text(json.dumps(message, default=str))
            except (WebSocketDisconnect, RuntimeError):
                break
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception:  # noqa: BLE001
        try:
            await websocket.close(code=1011)
        except Exception:  # pragma: no cover
            pass
