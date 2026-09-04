"""Job status polling and the live WebSocket progress channel (PRD 10 / 13)."""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.session import get_db_session
from app.errors import JurisdictionDeniedError, NotFoundError, PermissionDeniedError
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


@router.websocket("/jobs/ws/{case_id}")
async def job_stream(websocket: WebSocket, case_id: str) -> None:
    """Push per-document pipeline stage progress to the UI.

    Authentication over a WebSocket uses the access token as a query parameter,
    because browsers cannot set headers on the handshake.  The token is short
    lived (15 minutes) and the channel is scoped to one case.

    The path lives under the ``/jobs`` family (``GET /jobs/{id}``,
    ``GET /cases/{id}/jobs``) as documented in the README and as the console
    calls it.  It previously lived at ``/ws/jobs/{case_id}``, which no client
    used — every connection fell through to Starlette's "no route" close and
    the console saw an endless stream of failed handshakes.

    Authorization reuses the REST dependency chain verbatim — the token is
    resolved to an active user (as ``get_principal`` does) and the case is
    checked with :func:`app.services.cases.require_case` against the
    :class:`~app.security.deps.JurisdictionScope` built by the *same*
    ``get_scope`` the REST endpoints depend on, including time-boxed
    cross-jurisdiction grants.  There is deliberately no separate, weaker
    WebSocket authorization model:

    * missing / invalid / expired token, or an unknown / inactive user → ``4401``;
    * authenticated but the case does not exist or is outside the caller's
      scope → ``4403`` (uniformly, so the close code leaks nothing about
      whether the case exists — the same property the REST API guarantees by
      making 403 look like 404).

    The authorization queries run on a dedicated, long-lived event loop
    (``_get_auth_loop``): an asyncio database connection is bound to the loop
    whose futures it serves, so running them on the *socket's* loop would
    create a connection per portal — and in multi-loop embeddings (tests,
    socket servers) a pooled connection could then be handed to a different,
    possibly dead loop and wedge.  One stable loop keeps every handshake
    connection on the same executor; the loop is process-wide and the queries
    are three tiny indexed lookups per connection.

    The socket is accepted *before* any of this is checked and then closed
    with the code above: closing before the accept would abort the handshake
    itself and the browser would report an opaque code 1006, indistinguishable
    from a network failure.  A visible code lets the console renew the access
    token (4401) and reconnect, or stop immediately (4403).
    """
    from app.security.tokens import decode_access_token

    await websocket.accept()

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001
        await websocket.close(code=4401)
        return

    try:
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                _authorize_websocket(str(payload.get("sub")), case_id), _get_auth_loop()
            )
        )
    except _WSNotAuthenticated:
        await websocket.close(code=4401)
        return
    except (NotFoundError, JurisdictionDeniedError, PermissionDeniedError):
        await websocket.close(code=4403)
        return

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


class _WSNotAuthenticated(Exception):
    """Signed token, but the identity behind it is gone or inactive."""


_auth_loop: asyncio.AbstractEventLoop | None = None
_auth_loop_lock = threading.Lock()


def _get_auth_loop() -> asyncio.AbstractEventLoop:
    """The process-wide loop that runs WebSocket authorization checks."""
    global _auth_loop
    if _auth_loop is None:
        with _auth_loop_lock:
            if _auth_loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, name="crimelink-ws-auth", daemon=True
                ).start()
                _auth_loop = loop
    return _auth_loop


async def _authorize_websocket(user_sub: str, case_id: str) -> None:
    """The REST authorization chain, verbatim, on the dedicated auth loop."""
    from app.db.models import User
    from app.db.session import async_session
    from app.services import cases as case_service

    async with async_session() as session:
        user = await session.get(User, user_sub)
        if user is None or not user.is_active:
            raise _WSNotAuthenticated()
        principal = Principal(user)
        scope = await get_scope(principal, session)
        await case_service.require_case(session, scope, case_id)
