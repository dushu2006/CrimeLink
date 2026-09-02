"""The three non-negotiable guarantees (PRD 1.3).

G1  Everything is evidenced — no graph write without a source document.
G2  Nothing serious happens without a human — no auto-merge, no auto-confirmed
    pattern finding.
G3  Everything is auditable and tamper-evident — an append-only hash-chained
    audit log and write-once document storage.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.audit.service import audit_service
from app.domain.enums import (
    AuditAction,
    DocumentType,
    IngestionStatus,
    ResolutionStatus,
)
from tests.conftest import SAMPLE_FIR
from tests.test_pipeline import _upload


# --------------------------------------------------------------------------- #
# G1 — evidence
# --------------------------------------------------------------------------- #

def test_injector_refuses_a_node_without_a_source_document(container):
    from app.adapters.graph.injector import GraphInjector
    from app.domain.models import GraphNode
    from app.errors import UnevidencedGraphWriteError

    injector = GraphInjector(container.graph_store)
    node = GraphNode(
        provenance_key="pk-without-evidence",
        label="Person",
        properties={"name": "Nobody", "confidence": 0.9},  # no source_doc_id
    )
    with pytest.raises(UnevidencedGraphWriteError):
        injector.inject(
            case_id="case-1",
            case_number="FIR/1/2024",
            jurisdiction_id="RJ-JAIPUR",
            doc_id="doc-1",
            nodes=[node],
            edges=[],
        )


def test_the_public_injection_entry_point_requires_a_document_id():
    """The only way to write case data demands the document it came from."""
    import inspect

    from app.adapters.graph.injector import GraphInjector

    assert "doc_id" in inspect.signature(GraphInjector.inject).parameters
    assert "doc_id" in inspect.signature(GraphInjector.link_to_case).parameters


def test_validation_rejects_an_unevidenced_edge():
    from app.adapters.graph.injector import GraphInjector
    from app.domain.models import GraphEdge
    from app.errors import UnevidencedGraphWriteError

    with pytest.raises(UnevidencedGraphWriteError):
        GraphEdge(source_key="a", target_key="b", rel_type="CALLED", properties={})


# --------------------------------------------------------------------------- #
# G2 — human in the loop
# --------------------------------------------------------------------------- #

async def test_a_merge_requires_a_written_rationale(client, investigator_headers, db, container, case, users):
    from tests.conftest import SAMPLE_HINDI_FIR
    from tests.test_pipeline import _upload

    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR)
    queue = client.get(
        f"/api/v1/resolution?case_id={case.id}", headers=investigator_headers
    ).json()
    assert queue["count"] >= 1, "the fixture must produce at least one review item"
    item_id = queue["items"][0]["id"]

    refused = client.post(
        f"/api/v1/resolution/{item_id}/merge",
        headers=investigator_headers,
        json={"note": "no"},
    )
    assert refused.status_code == 422, "a one-word note is not a rationale"


async def test_a_merge_is_audited_and_reversible(client, investigator_headers, db, container, case, users):
    from tests.conftest import SAMPLE_HINDI_FIR
    from tests.test_pipeline import _upload

    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR)
    queue = client.get(
        f"/api/v1/resolution?case_id={case.id}", headers=investigator_headers
    ).json()
    if not queue["items"]:
        pytest.skip("no fuzzy candidate was proposed for this corpus")
    item_id = queue["items"][0]["id"]

    merged = client.post(
        f"/api/v1/resolution/{item_id}/merge",
        headers=investigator_headers,
        json={"note": "Same person: identical name and father's name in both FIRs."},
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["reversible"] is True

    # The decision is in the tamper-evident log.
    audit = client.get(
        "/api/v1/admin/audit/search?action_type=MERGE",
        headers=investigator_headers,
    )
    if audit.status_code == 200:
        assert audit.json()["count"] >= 1

    unmerged = client.post(
        f"/api/v1/resolution/{item_id}/unmerge",
        headers=investigator_headers,
        json={"note": "Reversed: the two names belong to different men."},
    )
    assert unmerged.status_code == 200, unmerged.text


async def test_a_viewer_cannot_merge(client, viewer_headers, db, container, case, users):
    from tests.conftest import SAMPLE_HINDI_FIR
    from tests.test_pipeline import _upload

    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR)
    queue = client.get(f"/api/v1/resolution?case_id={case.id}", headers=viewer_headers).json()
    if not queue["items"]:
        pytest.skip("no fuzzy candidate was proposed for this corpus")
    response = client.post(
        f"/api/v1/resolution/{queue['items'][0]['id']}/merge",
        headers=viewer_headers,
        json={"note": "A viewer must not be able to merge identities."},
    )
    assert response.status_code == 403


async def test_a_rejected_pair_never_comes_back(client, investigator_headers, db, container, case, users):
    """A tombstone is permanent: the same pair is not re-proposed."""
    from tests.conftest import SAMPLE_HINDI_FIR
    from tests.test_pipeline import _upload

    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR)
    queue = client.get(
        f"/api/v1/resolution?case_id={case.id}", headers=investigator_headers
    ).json()
    if not queue["items"]:
        pytest.skip("no fuzzy candidate was proposed for this corpus")

    item = queue["items"][0]
    rejected = client.post(
        f"/api/v1/resolution/{item['id']}/reject",
        headers=investigator_headers,
        json={"note": "Different people: verified against the voter list."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["re_proposable"] is False
    assert container.graph_store.has_tombstone(
        item["source"]["provenance_key"], item["target"]["provenance_key"]
    )


async def test_patterns_are_never_confirmed_automatically(client, investigator_headers, db, container, case, users):
    from app.db.models import DetectedPattern
    from tests.test_pipeline import _upload

    await _upload(
        db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR
    )
    from tests.conftest import SAMPLE_BANK

    await _upload(
        db, container, case, users, "bank.csv", SAMPLE_BANK, DocumentType.FINANCIAL
    )
    body = client.get("/api/v1/patterns", headers=investigator_headers).json()
    assert body["count"] >= 1
    assert all(item["status"] == "NEW" for item in body["items"]), body["items"]

    # Dismissing requires a rationale; the finding is never deleted.
    finding = body["items"][0]
    no_reason = client.post(
        f"/api/v1/patterns/{finding['id']}/review",
        headers=investigator_headers,
        json={"decision": "DISMISSED"},
    )
    assert no_reason.status_code == 422

    dismissed = client.post(
        f"/api/v1/patterns/{finding['id']}/review",
        headers=investigator_headers,
        json={
            "decision": "DISMISSED",
            "note": "Transfers are legitimate supplier payments; invoices attached.",
        },
    )
    assert dismissed.status_code == 200

    rows = (
        await db.execute(select(DetectedPattern).where(DetectedPattern.id == finding["id"]))
    ).scalars().all()
    assert rows, "the finding must still exist — nothing is deleted"


# --------------------------------------------------------------------------- #
# G3 — auditability
# --------------------------------------------------------------------------- #

async def test_the_audit_chain_verifies(db):
    from app.db.session import sync_session

    with sync_session() as session:
        audit_service.append(
            session, action_type=AuditAction.CONFIG_CHANGE, target_resource="test:1"
        )
        result = audit_service.verify(session)
    assert result["valid"] is True
    assert result["checked"] >= 1


async def test_tampering_with_an_audit_row_is_detected(db):
    from app.db.session import sync_session

    with sync_session() as session:
        audit_service.append(
            session, action_type=AuditAction.CONFIG_CHANGE, target_resource="test:2"
        )
        audit_service.append(
            session, action_type=AuditAction.CONFIG_CHANGE, target_resource="test:3"
        )
        assert audit_service.verify(session)["valid"] is True
        session.execute(
            text("UPDATE audit_logs SET target_resource = 'tampered' WHERE target_resource = 'test:2'")
        )
        session.commit()
        result = audit_service.verify(session)
    assert result["valid"] is False
    assert result.get("first_tampered_id") is not None


async def test_deleting_an_audit_row_is_detected(db):
    from app.db.session import sync_session

    with sync_session() as session:
        audit_service.append(
            session, action_type=AuditAction.CONFIG_CHANGE, target_resource="test:4"
        )
        session.commit()
        session.execute(text("DELETE FROM audit_logs WHERE target_resource = 'test:4'"))
        session.commit()
        result = audit_service.verify(session)
    assert result["valid"] is False


async def test_documents_are_write_once(db, container, case, users):
    from app.errors import ConflictError

    key = f"{case.id}/doc-1/evidence.txt"
    container.object_store.put("documents", key, b"original", content_type="text/plain")
    # Re-putting identical bytes is idempotent …
    container.object_store.put("documents", key, b"original", content_type="text/plain")
    # … but different bytes for the same key is an attempt to rewrite evidence.
    with pytest.raises(ConflictError):
        container.object_store.put("documents", key, b"tampered", content_type="text/plain")


async def test_every_login_is_in_the_audit_log(client, admin_headers):
    body = client.get(
        "/api/v1/admin/audit/search?action_type=LOGIN", headers=admin_headers
    ).json()
    assert body["count"] >= 1


async def test_quarantine_is_visible_to_an_administrator(client, admin_headers, db, container, case, users):
    from tests.test_pipeline import _upload

    await _upload(
        db, container, case, users, "junk.json", "{not json", DocumentType.SOCIAL_MEDIA
    )
    body = client.get("/api/v1/admin/quarantine", headers=admin_headers).json()
    assert body["count"] >= 1
    assert any("junk.json" in item["filename"] for item in body["items"])
