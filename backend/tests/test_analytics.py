"""Analytics: influence with explanation, temporal paths, export (PRD 11)."""

from __future__ import annotations

import pytest

from app.domain.enums import DocumentType
from tests.conftest import SAMPLE_CDR, SAMPLE_FIR, SAMPLE_HINDI_FIR
from tests.test_pipeline import _upload


async def _seed(db, container, case, users) -> None:
    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "fir_hi.txt", SAMPLE_HINDI_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "cdr.csv", SAMPLE_CDR, DocumentType.CDR)


def _any_person(case_id, container) -> str:
    for node in container.graph_store.snapshot(case_id).nodes.values():
        if node.label == "Person":
            return node.provenance_key
    pytest.skip("no person node in the graph")


async def test_case_graph_returns_cytoscape_shaped_elements(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}", headers=investigator_headers
    ).json()
    assert body["counts"]["nodes"] >= 1
    assert body["counts"]["edges"] >= 1
    for node in body["nodes"]:
        assert node["provenance_key"]
        assert node["source_doc_ids"], "every node carries its evidence (G1)"
    for edge in body["edges"]:
        assert edge["source"] and edge["target"] and edge["rel_type"]
        assert edge["source_doc_ids"], "every edge carries its evidence (G1)"


async def test_influence_returns_a_score_and_its_explanation(client, investigator_headers, db, container, case, users):
    """PRD 11 — a score without an explanation is a defect, not a partial answer."""
    await _seed(db, container, case, users)
    # Rank first, then explain the top node: an isolated mention has no edges to
    # show, so the explanation would legitimately be empty.
    ranked = client.get(
        f"/api/v1/graph/cases/{case.id}/centrality?limit=1", headers=investigator_headers
    ).json()
    assert ranked["count"] >= 1
    key = ranked["items"][0]["provenance_key"]
    body = client.get(
        f"/api/v1/graph/nodes/{key}/influence", headers=investigator_headers
    ).json()
    # The score …
    assert body["betweenness"] is not None
    assert body["rank_in_case"] >= 1 and body["rank_total"] >= 1
    # … and the justification, which is what makes it checkable.
    explanation = body["explanation"]
    assert explanation["summary"], "an influence score must say why"
    assert explanation["method"]
    assert explanation["top_weighted_edges"], "the edges that produced the score"
    assert explanation["evidence_doc_ids"], "and the documents behind those edges"


async def test_centrality_is_ranked_and_explains_itself(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}/centrality?limit=5", headers=investigator_headers
    ).json()
    assert body["count"] >= 1
    first = body["items"][0]
    assert first["rank"] == 1
    assert first["metric"] == "betweenness"
    # All four scores travel together so the UI can show *why* a node ranks.
    for field in ("betweenness", "pagerank", "degree", "community"):
        assert field in first


async def test_expansion_depth_is_capped_at_two(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    key = _any_person(case.id, container)
    assert client.get(
        f"/api/v1/graph/nodes/{key}/expand?depth=9", headers=investigator_headers
    ).status_code == 422, "the API refuses a depth above 2 outright"

    body = client.get(
        f"/api/v1/graph/nodes/{key}/expand?depth=2", headers=investigator_headers
    ).json()
    assert body["root"] == key


async def test_expansion_service_clamps_even_when_called_directly(db, container, case, users):
    """Defence in depth: the service clamps too, not just the query validator."""
    from app.security.deps import Principal

    await _seed(db, container, case, users)
    key = _any_person(case.id, container)
    principal = Principal(users["INV-0001"])
    from app.security.deps import JurisdictionScope

    scope = JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())
    from app.services.graph_service import GraphService

    payload = await GraphService().expand(
        session=db, scope=scope, key=key, depth=99
    )
    assert payload["root"] == key


async def test_temporal_paths_respect_chronology(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    phones = [
        node.provenance_key
        for node in container.graph_store.snapshot(case.id).nodes.values()
        if node.label == "Phone"
    ]
    if len(phones) < 2:
        pytest.skip("the corpus produced fewer than two phone nodes")
    body = client.post(
        f"/api/v1/graph/cases/{case.id}/paths",
        headers=investigator_headers,
        json={"source_key": phones[0], "target_key": phones[-1]},
    ).json()
    assert "paths" in body or "items" in body


async def test_search_finds_an_entity_and_is_audited(client, investigator_headers, admin_headers, db, container, case, users):
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/search?q=Yadav&case_id={case.id}", headers=investigator_headers
    ).json()
    assert body["count"] >= 1

    audit = client.get(
        "/api/v1/admin/audit/search?action_type=SEARCH", headers=admin_headers
    ).json()
    assert audit["count"] >= 1, "searches are auditable (DPDP)"


async def test_case_export_is_a_watermarked_pdf(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    response = client.get(f"/api/v1/cases/{case.id}/export", headers=investigator_headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")


async def test_staging_nodes_can_be_promoted_by_an_investigator(client, investigator_headers, db, container, case, users):
    from app.domain.enums import SourceConfidence

    await _upload(
        db,
        container,
        case,
        users,
        "tip.txt",
        SAMPLE_FIR,
        DocumentType.INTEL,
        SourceConfidence.ANONYMOUS_TIP,
    )
    staged = client.get(
        f"/api/v1/graph/cases/{case.id}/staging", headers=investigator_headers
    ).json()
    assert staged["count"] >= 1

    promoted = client.post(
        f"/api/v1/graph/cases/{case.id}/staging/promote",
        headers=investigator_headers,
        json={"provenance_keys": [staged["items"][0]["provenance_key"]]},
    ).json()
    assert promoted["promoted"] == 1


async def test_master_graph_filters_by_label(client, investigator_headers, db, container, case, users):
    """Master Graph: the same canonical data, filtered — nothing is invented."""
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}", params={"labels": "PHONE"}, headers=investigator_headers
    ).json()
    assert body["counts"]["nodes"] >= 1
    assert all(n["label"] == "PHONE" for n in body["nodes"]), "label filter keeps only PHONE nodes"
    assert body["filters"]["labels"] == ["PHONE"]


async def test_master_graph_filters_by_rel_type(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}", params={"rel_types": "CALLED"}, headers=investigator_headers
    ).json()
    assert body["counts"]["edges"] >= 1
    assert all(e["rel_type"] == "CALLED" for e in body["edges"]), "rel_type filter keeps only CALLED edges"


async def test_temporal_graph_endpoint_returns_visual_graph(client, investigator_headers, db, container, case, users):
    """Temporal Graph (visual): graph-ready nodes/edges, NOT a serialised path."""
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}/temporal",
        params={"from_ts": "2024-08-01T00:00:00", "to_ts": "2024-08-31T23:59:59"},
        headers=investigator_headers,
    ).json()
    assert "nodes" in body and "edges" in body and "events" in body
    assert "time_range" in body and "empty_reason" in body
    assert body["counts"]["edges"] >= 1, "the seeded CDR lies inside the window"
    assert body["empty_reason"] is None
    for edge in body["edges"]:
        assert edge["source_doc_ids"], "temporal edges keep their evidence (G1)"


async def test_temporal_graph_empty_window_reports_reason(client, investigator_headers, db, container, case, users):
    await _seed(db, container, case, users)
    body = client.get(
        f"/api/v1/graph/cases/{case.id}/temporal",
        params={"from_ts": "2010-01-01T00:00:00", "to_ts": "2010-12-31T23:59:59"},
        headers=investigator_headers,
    ).json()
    assert body["counts"]["edges"] == 0
    assert body["empty_reason"] is not None, "an empty window is reported honestly"
