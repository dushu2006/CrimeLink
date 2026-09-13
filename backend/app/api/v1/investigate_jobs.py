"""Long-running investigation jobs: POST start, GET status, WS subscribe.

POST /investigate/jobs starts an investigation job (honest stages, no fake progress).
GET  /investigate/jobs/{job_id} polls status.
WebSocket /ws/investigate/jobs/{job_id} subscribes to progress (reuses event_bus).

Jobs preserve deterministic work even when AI model is unavailable/timeout/invalid.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.session import get_db_session
from app.investigator.schemas import InvestigateRequest
from app.security.deps import JurisdictionScope, Principal, get_scope, require_roles
from app.services.investigation_jobs import (
    InvestigationReporter,
    get_investigation_job,
    job_channel,
    start_investigation_job,
    _running,
)

router = APIRouter(prefix="/investigate/jobs", tags=["investigate-jobs"])


@router.post("", response_model=dict)
async def start_investigation_job_endpoint(
    payload: InvestigateRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Start a long-running investigation job with honest stage reporting.

    Returns job_id immediately; client polls GET or subscribes via WS.
    Deterministic work is preserved even if reasoning model is unavailable.
    """
    from app.db.session import async_session
    from app.investigator.orchestrator import investigate_with_reporter

    # Capture scope snapshot for background task (scope is request-bound)
    # We pass scope object directly but need to re-create jurisdiction inside work
    # For simplicity, we capture scope's user jurisdiction data via closure over scope
    captured_scope = scope
    captured_principal = principal
    captured_payload = payload

    async def work(reporter):
        # Each stage runs inside its own DB session via async_session
        async with async_session() as bg_session:
            # Re-use captured scope/principal (they are plain objects)
            result = await investigate_with_reporter(
                bg_session,
                captured_scope,
                captured_principal,
                question=captured_payload.question,
                case_id=captured_payload.case_id,
                investigation_id=captured_payload.investigation_id,
                objective=captured_payload.objective,
                max_patterns=captured_payload.max_patterns,
                include_excluded=captured_payload.include_excluded,
                reporter=reporter,
            )
            # Persist result to job row via reporter already handled terminal?
            # Ensure terminal update if not already done by orchestrator
            status = result.get("status", "COMPLETED")
            # Map to terminal status
            terminal_status = status if status in (
                "COMPLETED", "FAILED", "AI_UNAVAILABLE", "AI_TIMEOUT",
                "AI_INVALID_RESPONSE", "DATA_ERROR", "GRAPH_ERROR", "INTERNAL_ERROR"
            ) else "COMPLETED"
            await reporter.update(
                status=terminal_status,
                stage="COMPLETED" if terminal_status == "COMPLETED" else status,
                progress_pct=100 if terminal_status == "COMPLETED" else 90,
                message="Investigation completed" if terminal_status == "COMPLETED" else f"Completed with {terminal_status}",
                result=result,
            )
            return result

    row = await start_investigation_job(
        dataset_id=None,  # resolved inside work from active_dataset
        case_id=payload.case_id,
        investigation_id=payload.investigation_id,
        question=payload.question,
        objective=payload.objective,
        requested_by=getattr(principal, "id", None),
        work=work,
    )
    return row


@router.get("/{job_id}")
async def get_job_status(
    job_id: str,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
) -> dict[str, Any]:
    row = await get_investigation_job(job_id)
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Investigation job not found")
    return row


@router.websocket("/ws/job/{job_id}")
async def ws_job_subscribe(websocket: WebSocket, job_id: str):
    """WebSocket subscription for investigation job progress with token auth.

    Mirrors dataset_job_stream: token via query, snapshot first, then live events,
    closes with 1000 on terminal, 4401/4404 on auth/not-found.
    """
    import json as _json
    from app.security.tokens import decode_access_token
    from app.api.v1.jobs import _get_auth_loop, _WSNotAuthenticated, _authorize_job_websocket

    await websocket.accept()

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_access_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    try:
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                _authorize_job_websocket(str(payload.get("sub"))), _get_auth_loop()
            )
        )
    except _WSNotAuthenticated:
        await websocket.close(code=4401)
        return
    except Exception:
        await websocket.close(code=1011)
        return

    row = await get_investigation_job(job_id)
    if row is None:
        await websocket.close(code=4404)
        return

    await websocket.send_text(
        _json.dumps({"type": "job_snapshot", "job_id": job_id, **row}, default=str)
    )
    if row.get("terminal"):
        await websocket.close(code=1000)
        return

    container = get_container()
    try:
        async for message in container.event_bus.subscribe(job_channel(job_id)):
            try:
                await websocket.send_text(_json.dumps(message, default=str))
            except (WebSocketDisconnect, RuntimeError):
                break
            if message.get("terminal") or message.get("type") == "investigation_finished":
                await websocket.close(code=1000)
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


# Also expose under /jobs/ws/investigation/{job_id} via same handler alias for frontend convenience
# Frontend can call /api/v1/jobs/ws/investigation/{job_id} if needed, but we keep primary at
# /api/v1/investigate/jobs/ws/job/{job_id} which is mounted under /investigate/jobs prefix.
# For backward compatibility, add second router without prefix in main.py later if needed.
