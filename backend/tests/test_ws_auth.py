"""Regression tests for jobs-WebSocket authentication AND authorization.

The console opens ``/api/v1/jobs/ws/{case_id}?token=<access token>``.  The
endpoint must enforce exactly the same rules as the REST API:

* authentication (missing / invalid / expired token, unknown or inactive
  user) closes the accepted socket with ``4401`` — never a pre-accept close,
  which browsers would report as an opaque code 1006;
* authorization goes through the SAME ``JurisdictionScope`` +
  ``require_case`` chain as every protected REST endpoint, so out-of-scope
  or unknown cases close with ``4403`` and time-boxed cross-jurisdiction
  grants work identically on both surfaces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.db.session import async_session
from tests.conftest import PASSWORD

WS_PATH = "/api/v1/jobs/ws/{case_id}"


def _token_for(user) -> str:
    from app.security.tokens import create_access_token

    return create_access_token(user)


def _expired_access_token() -> str:
    """A syntactically valid access token whose exp is in the past."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "user-000000",
            "badge": "INV-0001",
            "role": "INVESTIGATOR",
            "jurisdiction_id": "RJ-JAIPUR",
            "station_id": "PS-JAIPUR",
            "iat": now - timedelta(minutes=30),
            "exp": now - timedelta(minutes=15),
            "type": "access",
        },
        get_settings().secret_key,
        algorithm="HS256",
    )


def _receive_close(client, url: str) -> int:
    """Connect and return the close code the server ends the socket with."""
    with client.websocket_connect(url) as websocket:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_text()
        return excinfo.value.code


def _ws_url(case_id: str, token: str) -> str:
    return WS_PATH.format(case_id=case_id) + f"?token={token}"


async def _fresh_outsider(client, admin_token: str, badge: str) -> SimpleNamespace:
    """Create an investigator in a jurisdiction no suite test ever grants into.

    Uses the admin API (the same path production uses).  The suite shares one
    database, and its REST grant tests legitimately give INV-0002 time-boxed
    access to RJ-JAIPUR — scope expectations must never depend on test order,
    so out-of-scope assertions use a user nothing in the suite can have
    granted.
    """
    response = client.post(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "badge_number": badge,
            "full_name": f"Inspector {badge}",
            "password": PASSWORD,
            "role": "INVESTIGATOR",
            "station_id": "PS-JODHPUR",
            "jurisdiction_id": "RJ-JODHPUR",
        },
    )
    assert response.status_code in (200, 201), response.text

    def token() -> str:
        """Sign in as the new investigator and return the access token."""
        login = client.post(
            "/api/v1/auth/login",
            json={"badge_number": badge, "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    return SimpleNamespace(id=response.json()["id"], token=token)


# --------------------------------------------------------------------------- #
# Authentication -> 4401
# --------------------------------------------------------------------------- #


def test_ws_without_token_is_closed_4401(client):
    assert _receive_close(client, WS_PATH.format(case_id="any-case")) == 4401


def test_ws_with_garbage_token_is_closed_4401(client):
    assert _receive_close(client, _ws_url("any-case", "not-a-jwt")) == 4401


def test_ws_with_expired_access_token_is_closed_4401(client):
    """The exact regression seen in the console: token older than 15 minutes."""
    assert _receive_close(client, _ws_url("any-case", _expired_access_token())) == 4401


def test_ws_with_unknown_user_is_closed_4401(client):
    """A correctly signed token for a user that no longer exists is still an
    authentication failure — the same verdict as the REST dependency chain."""
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "badge": "GONE-0001",
            "role": "INVESTIGATOR",
            "jurisdiction_id": "RJ-JAIPUR",
            "station_id": "PS-JAIPUR",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "type": "access",
        },
        get_settings().secret_key,
        algorithm="HS256",
    )
    assert _receive_close(client, _ws_url("any-case", token)) == 4401


# --------------------------------------------------------------------------- #
# Authorization -> 4403 (same scope rules as the REST API)
# --------------------------------------------------------------------------- #


def test_ws_unknown_case_is_closed_4403(client, users):
    """Authenticated caller, nonexistent case: 4403, never an open channel."""
    token = _token_for(users["INV-0001"])
    assert _receive_close(client, _ws_url("no-such-case", token)) == 4403


def test_ws_case_outside_jurisdiction_is_closed_4403(client, users, other_case):
    """A Jaipur investigator must not subscribe to a Kota case channel."""
    token = _token_for(users["INV-0001"])
    assert _receive_close(client, _ws_url(other_case.id, token)) == 4403


async def test_ws_case_outside_jurisdiction_is_closed_4403_in_both_directions(
    client, users, case, other_case, db
):
    """A Jaipur investigator must not watch a Kota case, nor vice versa.

    The second direction uses a fresh third-jurisdiction investigator because
    the suite's REST grant tests lawfully give INV-0002 time-boxed access to
    RJ-JAIPUR; asserting 4403 for INV-0002 here would depend on test order.
    """
    from app.security.tokens import create_access_token

    assert _receive_close(client, _ws_url(other_case.id, _token_for(users["INV-0001"]))) == 4403

    admin_token = create_access_token(users["ADM-0001"])
    outsider = await _fresh_outsider(client, admin_token, "INV-JDH-OUT")
    assert _receive_close(client, _ws_url(case.id, outsider.token())) == 4403


async def test_ws_grant_expiry_is_enforced(client, users, case, db):
    """An APPROVED grant that has passed its expires_at no longer opens the
    channel — get_scope's lazy expiry applies on the WebSocket path too."""
    from app.db.base import new_uuid, utcnow
    from app.db.models import JurisdictionAccessRequest
    from app.domain.enums import AccessRequestStatus
    from app.security.tokens import create_access_token

    admin_token = create_access_token(users["ADM-0001"])
    outsider = await _fresh_outsider(client, admin_token, "INV-JDH-STALE")

    async with async_session() as session:
        session.add(
            JurisdictionAccessRequest(
                id=new_uuid(),
                requester_id=outsider.id,
                target_jurisdiction="RJ-JAIPUR",
                case_id=case.id,
                reason="Stale joint operation",
                status=AccessRequestStatus.APPROVED,
                expires_at=utcnow() - timedelta(minutes=1),
            )
        )
        await session.commit()

    assert _receive_close(client, _ws_url(case.id, outsider.token())) == 4403


async def test_ws_time_bound_grant_opens_the_case_like_the_rest_api(
    client, admin_headers, users, case
):
    """The lawful cross-jurisdiction flow: request -> approve -> connect.

    The grant is created through the REST endpoints exactly like
    ``test_time_bound_access_grant_opens_a_case``, for a fresh third-
    jurisdiction investigator so the assertion cannot pass because of a grant
    an earlier suite test already made.  The test then asserts the WebSocket
    accepts the SAME caller the REST API accepts — one model, two transports.
    """
    outsider = await _fresh_outsider(
        client, admin_headers["Authorization"].removeprefix("Bearer "), "INV-JDH-GRANT"
    )
    outsider_headers = {"Authorization": f"Bearer {outsider.token()}"}

    requested = client.post(
        "/api/v1/access/request",
        headers=outsider_headers,
        json={
            "target_jurisdiction": "RJ-JAIPUR",
            "case_id": case.id,
            "reason": "Accused has known associates here; joint operation.",
        },
    )
    assert requested.status_code in (200, 201), requested.text
    request_id = requested.json()["id"]

    approved = client.post(
        f"/api/v1/access/approve/{request_id}",
        headers=admin_headers,
        json={"approve": True, "note": "Joint operation approved.", "grant_days": 1},
    )
    assert approved.status_code == 200, approved.text

    # REST verdict for reference: the outside investigator can now read the case.
    assert client.get(f"/api/v1/cases/{case.id}", headers=outsider_headers).status_code == 200

    # WebSocket verdict: identical.
    with client.websocket_connect(_ws_url(case.id, outsider.token())):
        pass  # handshake survived: authorized


# --------------------------------------------------------------------------- #
# Authorized users connect and receive events
# --------------------------------------------------------------------------- #


def test_ws_with_valid_token_receives_channel_events(client, container, users, case):
    """A valid token for an in-scope case subscribes and receives progress."""
    import threading
    import time

    token = _token_for(users["INV-0001"])  # INV-0001 owns `case` (RJ-JAIPUR)
    url = _ws_url(case.id, token)

    message = {"doc_id": "doc-1", "stage": "NLP_EXTRACTION", "progress": 0.5}
    channel = f"case:{case.id}"

    # The subscriber queue is registered a tick after the handshake, so pump
    # the channel until the socket delivers a message instead of racing it.
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            container.event_bus.publish(channel, dict(message))
            time.sleep(0.02)

    publisher = threading.Thread(target=pump, daemon=True)
    publisher.start()
    try:
        with client.websocket_connect(url) as websocket:
            received = websocket.receive_json()
    finally:
        stop.set()
        publisher.join(timeout=2)

    assert received == message


# --------------------------------------------------------------------------- #
# Cross-loop pool regression
# --------------------------------------------------------------------------- #


def test_repeated_ws_auth_does_not_poison_rest_db(client, users, case):
    """Many WebSocket authorizations must leave REST database access intact.

    WS authorization runs on its own event loop.  If it borrowed the
    process-wide async engine — an asyncpg pool bound to the API loop — the
    connections it creates would later be handed back to the API loop and
    raise ``RuntimeError: attached to a different loop`` on the next REST
    call.  This test performs enough handshakes to fill a pool, then proves
    REST still works and that the two code paths use distinct engines.
    """
    import app.api.v1.jobs as jobs_module
    from app.db.session import get_async_engine

    token = _token_for(users["INV-0001"])
    url = _ws_url(case.id, token)
    headers = {"Authorization": f"Bearer {token}"}

    # Authorized handshakes: each runs the full auth chain on the auth loop.
    for _ in range(40):
        with client.websocket_connect(url):
            pass

    # The auth path must own its engine; it must never be the REST pool.
    assert jobs_module._get_auth_engine() is not get_async_engine()

    # REST still works immediately after the handshakes.
    assert client.get(f"/api/v1/cases/{case.id}", headers=headers).status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        json={"badge_number": "INV-0001", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text


def test_ws_auth_never_touches_the_process_wide_sessionmaker(
    client, users, case, monkeypatch
):
    """The WS auth path must not ask the REST sessionmaker for a session.

    ``app.db.session.async_session`` (the REST dependency) is backed by the
    loop-bound process-wide engine.  The auth path must use its own engine.
    Replace the sessionmaker factory with a guard that fails if it is invoked
    from the auth-loop thread, then prove a handshake still authorizes.
    """
    import threading

    from app.db import session as db_session

    real = db_session.get_async_sessionmaker
    touched: list[str] = []

    def guarded():
        if threading.current_thread().name == "crimelink-ws-auth":
            touched.append("auth-loop-touched-rest-sessionmaker")
            raise RuntimeError("WS auth touched the process-wide sessionmaker")
        return real()

    monkeypatch.setattr(db_session, "get_async_sessionmaker", guarded)

    token = _token_for(users["INV-0001"])
    with client.websocket_connect(_ws_url(case.id, token)):
        pass

    assert touched == []
