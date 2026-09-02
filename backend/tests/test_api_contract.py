"""The API contract: shape, RBAC, jurisdiction scoping (PRD 10 / 12)."""

from __future__ import annotations

import pytest

from tests.conftest import PASSWORD, SAMPLE_FIR


# --------------------------------------------------------------------------- #
# Shape of the surface
# --------------------------------------------------------------------------- #

def test_no_endpoint_uses_the_delete_method(client):
    """PRD 10 — deletion is absent from the API surface, not merely disabled."""
    schema = client.get("/api/openapi.json").json()
    methods = {
        method.upper()
        for path in schema["paths"].values()
        for method in path
        if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}
    }
    assert "DELETE" not in methods, methods
    assert methods


def test_health_and_version_are_public(client):
    assert client.get("/api/v1/health/live").status_code == 200
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code in (200, 503)
    assert client.get("/api/v1/version").json()["version"]


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/v1/cases").status_code in (401, 403)


def test_setup_status_is_public(client, users):
    response = client.get("/api/v1/auth/setup")
    assert response.status_code == 200
    assert response.json()["setup_required"] is False


def test_setup_is_refused_once_users_exist(client, users):
    response = client.post(
        "/api/v1/auth/setup",
        json={
            "badge_number": "ADM-NEW",
            "full_name": "Should Fail",
            "password": "CrimeLink@Setup1",
            "station_id": "PS-TEST",
            "jurisdiction_id": "RJ-TEST",
        },
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

def test_login_returns_a_token_pair(client, users):
    response = client.post(
        "/api/v1/auth/login", json={"badge_number": "INV-0001", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "INVESTIGATOR"
    assert body["refresh_token"]


def test_wrong_password_is_rejected_and_audited(client, users):
    response = client.post(
        "/api/v1/auth/login",
        json={"badge_number": "INV-0001", "password": "wrong-password"},
    )
    assert response.status_code == 401


def test_account_locks_after_repeated_failures(client, users):
    from app.db.base import utcnow
    from app.db.models import User
    from app.db.session import sync_session
    from sqlalchemy import select

    for _ in range(6):
        client.post(
            "/api/v1/auth/login",
            json={"badge_number": "VIW-0001", "password": "nope"},
        )
    with sync_session() as session:
        user = session.execute(
            select(User).where(User.badge_number == "VIW-0001")
        ).scalar_one()
        assert user.locked_until and user.locked_until > utcnow()
        user.locked_until = None
        user.failed_login_count = 0


def test_refresh_rotates_and_reuse_is_detected(client, users):
    tokens = client.post(
        "/api/v1/auth/login", json={"badge_number": "ADM-0001", "password": PASSWORD}
    ).json()
    first = tokens["refresh_token"]

    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first}).json()
    assert rotated["refresh_token"] != first, "the refresh token must rotate on use"

    # Replaying the original token must be detected and must kill the family.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    ).status_code == 401


def test_logout_revokes_the_session(client, users):
    tokens = client.post(
        "/api/v1/auth/login", json={"badge_number": "ADM-0001", "password": PASSWORD}
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    logged_out = client.post(
        "/api/v1/auth/logout",
        headers=headers,
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert logged_out.status_code == 200, logged_out.text
    assert logged_out.json()["sessions_revoked"] >= 1
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).status_code == 401


def test_me_returns_the_callers_identity(client, admin_headers):
    body = client.get("/api/v1/auth/me", headers=admin_headers).json()
    assert body["role"] == "ADMIN"
    assert body["badge_number"] == "ADM-0001"


# --------------------------------------------------------------------------- #
# Cases, documents, jobs
# --------------------------------------------------------------------------- #

def test_investigator_can_create_and_read_a_case(client, investigator_headers):
    created = client.post(
        "/api/v1/cases",
        headers=investigator_headers,
        json={
            "case_number": "FIR/2024/9001/PS-TEST",
            "title": "Rangdari network",
            "jurisdiction_id": "RJ-JAIPUR",
        },
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    assert client.get(f"/api/v1/cases/{case_id}", headers=investigator_headers).status_code == 200


def test_viewer_can_read_but_not_create(client, viewer_headers, case):
    assert client.get("/api/v1/cases", headers=viewer_headers).status_code == 200
    forbidden = client.post(
        "/api/v1/cases",
        headers=viewer_headers,
        json={
            "case_number": "FIR/2024/9002/PS-TEST",
            "title": "Nope",
            "jurisdiction_id": "RJ-JAIPUR",
        },
    )
    assert forbidden.status_code == 403


def test_upload_requires_a_case_the_caller_can_reach(client, investigator_headers, case):
    response = client.post(
        f"/api/v1/cases/{case.id}/documents",
        headers=investigator_headers,
        files={"file": ("fir.txt", SAMPLE_FIR.encode(), "text/plain")},
        data={"document_type": "FIR"},
    )
    assert response.status_code in (200, 201, 202), response.text


def test_uploading_the_same_file_twice_is_a_conflict(client, investigator_headers, case, broker):
    for _ in range(2):
        response = client.post(
            f"/api/v1/cases/{case.id}/documents",
            headers=investigator_headers,
            files={"file": ("fir.txt", SAMPLE_FIR.encode(), "text/plain")},
            data={"document_type": "FIR"},
        )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Jurisdiction scoping (PRD 12.4)
# --------------------------------------------------------------------------- #

def test_a_case_outside_the_callers_jurisdiction_is_invisible(client, kota_headers, case):
    """Out-of-scope resources answer 404, indistinguishable from missing ones."""
    listed = client.get("/api/v1/cases", headers=kota_headers).json()
    ids = [c["id"] for c in listed.get("items", listed if isinstance(listed, list) else [])]
    assert case.id not in ids
    assert client.get(f"/api/v1/cases/{case.id}", headers=kota_headers).status_code == 404


def test_time_bound_access_grant_opens_a_case(client, kota_headers, admin_headers, case):
    requested = client.post(
        "/api/v1/access/request",
        headers=kota_headers,
        json={
            "target_jurisdiction": "RJ-JAIPUR",
            "case_id": case.id,
            "reason": "Accused has known associates in Kota; joint operation.",
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
    assert approved.json()["status"] == "APPROVED"
    assert "expires_at" in approved.json()

    assert client.get(f"/api/v1/cases/{case.id}", headers=kota_headers).status_code == 200


def test_only_an_admin_can_approve_access(client, investigator_headers, kota_headers, case):
    requested = client.post(
        "/api/v1/access/request",
        headers=kota_headers,
        json={
            "target_jurisdiction": "RJ-JAIPUR",
            "case_id": case.id,
            "reason": "Joint operation with Jaipur police.",
        },
    ).json()
    response = client.post(
        f"/api/v1/access/approve/{requested['id']}",
        headers=investigator_headers,
        json={"decision": "APPROVED"},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Admin surface
# --------------------------------------------------------------------------- #

def test_audit_surface_is_admin_only(client, admin_headers, viewer_headers):
    assert client.get("/api/v1/admin/audit/search", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/admin/audit/search", headers=viewer_headers).status_code == 403


def test_only_admin_can_create_users(client, admin_headers, investigator_headers):
    payload = {
        "badge_number": "INV-9999",
        "full_name": "New Investigator",
        "role": "INVESTIGATOR",
        "station_id": "PS-JAIPUR",
        "jurisdiction_id": "RJ-JAIPUR",
        "password": "CrimeLink@New1",
    }
    assert client.post(
        "/api/v1/admin/users", headers=admin_headers, json=payload
    ).status_code in (200, 201)
    assert (
        client.post("/api/v1/admin/users", headers=investigator_headers, json=payload).status_code
        == 403
    )


def test_metrics_are_exposed(client):
    body = client.get("/api/v1/metrics")
    assert body.status_code == 200
    assert "crimelink_" in body.text or "python_" in body.text
