"""
RBAC tests for Investigator and Viewer
Investigator = investigate and review
Viewer = observe and review only
"""

import pytest

# Authentication

def test_valid_investigator_login(client, users):
    resp = client.post("/api/v1/auth/login", json={"badge_number": "INV-0001", "password": "CrimeLink@Inv1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "INVESTIGATOR"
    assert "access_token" in data

def test_valid_viewer_login(client, users):
    resp = client.post("/api/v1/auth/login", json={"badge_number": "VIW-0001", "password": "CrimeLink@Inv1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "VIEWER"
    assert "access_token" in data

def test_invalid_credentials(client):
    resp = client.post("/api/v1/auth/login", json={"badge_number": "INV-0001", "password": "wrong"})
    assert resp.status_code in (401, 403)

def test_me_endpoint_returns_role(client, investigator_headers, viewer_headers):
    inv = client.get("/api/v1/auth/me", headers=investigator_headers)
    assert inv.status_code == 200
    assert inv.json()["role"] == "INVESTIGATOR"
    
    viw = client.get("/api/v1/auth/me", headers=viewer_headers)
    assert viw.status_code == 200
    assert viw.json()["role"] == "VIEWER"

# Investigator authorization

def test_investigator_can_list_cases(client, investigator_headers):
    resp = client.get("/api/v1/cases", headers=investigator_headers)
    assert resp.status_code == 200

def test_investigator_can_search(client, investigator_headers):
    resp = client.get("/api/v1/search?q=test", headers=investigator_headers)
    assert resp.status_code == 200

def test_investigator_can_global_search(client, investigator_headers):
    resp = client.get("/api/v1/search/global?q=test", headers=investigator_headers)
    assert resp.status_code == 200

def test_investigator_can_view_graph(client, investigator_headers):
    resp = client.get("/api/v1/graph/stats", headers=investigator_headers)
    assert resp.status_code == 200

def test_investigator_can_investigate(client, investigator_headers):
    resp = client.post("/api/v1/investigate", headers=investigator_headers, json={"question": "test"})
    assert resp.status_code != 403

# Viewer authorization — read allowed

def test_viewer_can_list_cases(client, viewer_headers):
    resp = client.get("/api/v1/cases", headers=viewer_headers)
    assert resp.status_code == 200

def test_viewer_can_search(client, viewer_headers):
    resp = client.get("/api/v1/search?q=test", headers=viewer_headers)
    assert resp.status_code == 200

def test_viewer_can_global_search(client, viewer_headers):
    resp = client.get("/api/v1/search/global?q=test", headers=viewer_headers)
    assert resp.status_code == 200

def test_viewer_can_view_graph_stats(client, viewer_headers):
    resp = client.get("/api/v1/graph/stats", headers=viewer_headers)
    assert resp.status_code == 200

def test_viewer_can_view_graph_types(client, viewer_headers):
    resp = client.get("/api/v1/graph/entity-types", headers=viewer_headers)
    assert resp.status_code == 200

def test_viewer_can_view_documents_list(client, viewer_headers):
    # Documents list uses get_principal, should allow viewer (filtered by classification)
    resp = client.get("/api/v1/cases/test-case/documents", headers=viewer_headers)
    # May be 404 if case not found, but not 403
    assert resp.status_code != 403

# Viewer authorization — investigate blocked

def test_viewer_cannot_investigate(client, viewer_headers):
    resp = client.post("/api/v1/investigate", headers=viewer_headers, json={"question": "Is there connection between A and B?"})
    assert resp.status_code == 403

def test_viewer_cannot_create_investigation_job(client, viewer_headers):
    resp = client.post("/api/v1/investigate/jobs", headers=viewer_headers, json={"question": "test"})
    assert resp.status_code == 403

def test_viewer_cannot_access_ai_ask(client, viewer_headers):
    resp = client.post("/api/v1/ai/cases/test-case/ask", headers=viewer_headers, json={"question": "test"})
    assert resp.status_code == 403

def test_viewer_cannot_upload_document(client, viewer_headers):
    resp = client.post("/api/v1/cases/test-case/documents", headers=viewer_headers, files={"file": ("test.txt", b"test")}, data={"document_type": "FIR"})
    assert resp.status_code == 403

def test_viewer_cannot_create_case(client, viewer_headers):
    resp = client.post("/api/v1/cases", headers=viewer_headers, json={"case_number": "TEST/001", "title": "Test"})
    assert resp.status_code == 403

def test_viewer_cannot_analyze_timeline(client, viewer_headers):
    resp = client.post("/api/v1/cases/test-case/timeline/analyze", headers=viewer_headers, json={"question": "test"})
    assert resp.status_code == 403

def test_viewer_cannot_review_pattern(client, viewer_headers):
    resp = client.post("/api/v1/patterns/test-pattern/review", headers=viewer_headers, json={"decision": "REVIEWED", "note": "test rationale for review"})
    assert resp.status_code == 403

# Case-level authorization

def test_unauthorized_case_blocked_for_viewer(client, viewer_headers, kota_headers):
    import uuid
    resp = client.post("/api/v1/cases", headers=kota_headers, json={"case_number": f"KOTA-{uuid.uuid4().hex[:6]}", "title": "Kota case", "jurisdiction_id": "RJ-KOTA"})
    if resp.status_code == 201:
        case_id = resp.json()["id"]
        resp2 = client.get(f"/api/v1/cases/{case_id}", headers=viewer_headers)
        assert resp2.status_code in (403, 404)

# Security — direct API bypass attempts

def test_viewer_cannot_bypass_via_direct_api(client, viewer_headers):
    endpoints = [
        ("/api/v1/investigate", "POST"),
        ("/api/v1/investigate/jobs", "POST"),
        ("/api/v1/ai/cases/123/ask", "POST"),
    ]
    for path, method in endpoints:
        if method == "POST":
            resp = client.post(path, headers=viewer_headers, json={"question": "test"})
            assert resp.status_code == 403, f"{path} should be 403 for viewer, got {resp.status_code}"

def test_unauthenticated_blocked(client):
    resp = client.get("/api/v1/cases")
    assert resp.status_code in (401, 403)

def test_admin_can_access_all(client, admin_headers):
    resp = client.get("/api/v1/cases", headers=admin_headers)
    assert resp.status_code == 200

def test_viewer_permissions_are_read_only(client, viewer_headers):
    # Ensure viewer cannot do any mutating actions
    resp = client.patch("/api/v1/cases/test-case/status", headers=viewer_headers, json={"status": "CLOSED"})
    assert resp.status_code == 403
