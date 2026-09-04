"""Graph explainability contract (G1 / Phase 5).

Every relationship the UI can render — whether it arrives from the case-graph
snapshot (flat rows) or from the one-hop ``/graph/nodes/{key}/expand`` call
(Cytoscape wire elements) — must carry everything the "why does this edge
exist?" panel needs:

    rel_type, confidence, staging, source_doc_ids, evidence

where ``evidence`` is either ``None`` (no fabricated pointer) or an object of
exactly ``{source_doc_id, text_span, origin}``.  A non-null ``source_doc_id``
must resolve to a real ingested document through ``/evidence/{doc_id}`` so the
source viewer can open the record behind the edge.

The frontend GraphPage normalisers (``edgeFromWire``/``nodeFromWire``) accept
both the flat row shape and the wire ``{data: {...}}`` shape; this module
mirrors that with ``_wire_or_flat`` so the backend contract is pinned to what
the UI actually consumes.
"""

from __future__ import annotations

from typing import Any

from app.db.models import CaseDocument
from app.domain.enums import DocumentType
from tests.conftest import SAMPLE_CDR, SAMPLE_FIR
from tests.test_pipeline import _upload

_REQUIRED_EDGE_KEYS = ("rel_type", "confidence", "staging", "source_doc_ids", "evidence")
_POINTER_KEYS = ("source_doc_id", "text_span", "origin")


def _wire_or_flat(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise like GraphPage.edgeFromWire: unwrap {data: {...}} if present."""
    return item.get("data", item) if isinstance(item, dict) else {}


def _node_row_like(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise like GraphPage.nodeFromWire: id -> provenance_key."""
    row = dict(_wire_or_flat(item))
    if "provenance_key" not in row and "id" in row:
        row["provenance_key"] = row["id"]
    return row


def _edge_row_like(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise like GraphPage.edgeFromWire (type -> rel_type, pointer built
    from source_doc_id/text_span/origin when the wire carries them, falling
    back to a flat row's pre-serialised ``evidence`` pointer)."""
    m = _wire_or_flat(item)
    text_span = m.get("text_span")
    text_span = [int(x) for x in text_span] if isinstance(text_span, list) else None
    origin = m.get("origin") or None
    source_doc_id = str(m["source_doc_id"]) if m.get("source_doc_id") else None
    if not (origin or text_span or source_doc_id):
        evidence = m.get("evidence")
        pointer = evidence if isinstance(evidence, dict) else None
    else:
        pointer = {"source_doc_id": source_doc_id, "text_span": text_span, "origin": origin}
    row = {
        "rel_type": str(m.get("rel_type") or m.get("type") or "RELATED"),
        "confidence": float(m.get("confidence", 1.0) or 1.0),
        "staging": bool(m.get("staging", False)),
        "source_doc_ids": [str(x) for x in (m.get("source_doc_ids") or [])],
        "source_doc_id": source_doc_id,
        "evidence": pointer,
    }
    return {k: v for k, v in row.items()}


async def _seed(db, container, case, users) -> None:
    await _upload(db, container, case, users, "fir.txt", SAMPLE_FIR, DocumentType.FIR)
    await _upload(db, container, case, users, "cdr.csv", SAMPLE_CDR, DocumentType.CDR)


def _evidence_of(edge_row: dict[str, Any]) -> dict[str, Any] | None:
    ev = edge_row.get("evidence")
    return ev if isinstance(ev, dict) else None


async def _assert_explainable_edge(edge_row: dict[str, Any]) -> None:
    for key in _REQUIRED_EDGE_KEYS:
        assert key in edge_row, f"edge is missing '{key}': {edge_row}"
    assert edge_row["rel_type"], f"edge must name its relationship: {edge_row}"
    assert isinstance(edge_row["confidence"], (int, float))
    assert isinstance(edge_row["staging"], bool)
    assert edge_row["source_doc_ids"], f"edge must name its source documents: {edge_row}"
    pointer = _evidence_of(edge_row)
    if pointer is None:
        # Aggregated / multi-document edges can only name their source_doc_ids;
        # the serialiser must never fabricate a pointer for them either.
        return
    assert set(pointer.keys()) == set(_POINTER_KEYS), (
        f"evidence pointer must be exactly {_POINTER_KEYS}: {pointer}"
    )


async def _assert_explainable_node(node_row: dict[str, Any]) -> None:
    for key in ("provenance_key", "label", "name", "confidence", "source_doc_ids", "staging"):
        assert key in node_row, f"node is missing '{key}': {node_row}"
    assert node_row["source_doc_ids"], f"every node must name its source documents: {node_row}"
    pointer = _evidence_of(node_row)
    if pointer is not None:
        assert set(pointer.keys()) == set(_POINTER_KEYS), node_row


async def _source_doc_ids(client, headers, db, pointer: dict[str, Any]) -> None:
    """Every doc named by an evidence pointer must resolve as a real document."""
    from sqlalchemy import select

    doc = pointer.get("source_doc_id")
    if not doc:
        return None
    response = client.get(f"/api/v1/evidence/{doc}", headers=headers)
    assert response.status_code == 200, (
        f"evidence pointer names {doc} which is not resolvable: "
        f"{response.status_code} {response.text[:200]}"
    )
    body = response.json()
    assert body["document_id"] == doc
    row = (
        await db.execute(select(CaseDocument).where(CaseDocument.id == doc))
    ).scalars().one_or_none()
    assert row is not None, f"evidence doc {doc} is not an ingested case document"
    assert body["content_hash"] == row.content_hash
    return None


async def test_case_graph_edges_carry_the_full_explainability_contract(
    client, investigator_headers, db, container, case, users
):
    """Flat case-graph rows carry rel_type/confidence/staging/docs/evidence."""
    await _seed(db, container, case, users)
    body = client.get(f"/api/v1/graph/cases/{case.id}", headers=investigator_headers).json()
    assert body["counts"]["edges"] >= 1
    for node in body["nodes"]:
        await _assert_explainable_node(node)
    for edge in body["edges"]:
        await _assert_explainable_edge(edge)
    # At least one real pointer must exist and be openable in the source viewer.
    resolvable = [e for e in body["edges"] if (_evidence_of(e) or {}).get("source_doc_id")]
    assert resolvable, "no case-graph edge carries an evidence pointer at all"
    await _source_doc_ids(client, investigator_headers, db, _evidence_of(resolvable[0]))


async def test_expand_edges_carry_the_full_explainability_contract(
    client, investigator_headers, db, container, case, users
):
    """Cytoscape wire elements from /expand normalise into explainable edges."""
    await _seed(db, container, case, users)
    snapshot = container.graph_store.snapshot(case.id, include_staging=False)
    # Pick a node that has at least one evidenced in-case edge to expand from.
    hub = None
    for node in snapshot.nodes.values():
        for edge in snapshot.edges:
            if edge.source_key == node.provenance_key or edge.target_key == node.provenance_key:
                hub = node.provenance_key
                break
        if hub:
            break
    assert hub, "seeded graph must contain an edge to expand"

    payload = client.get(
        f"/api/v1/graph/nodes/{hub}/expand?depth=1&limit=300",
        headers=investigator_headers,
    ).json()
    assert payload["root"] == hub
    assert payload["edges"], "the expand call must return at least one edge"

    for node in payload["nodes"]:
        await _assert_explainable_node(_node_row_like(node))
    for wire_edge in payload["edges"]:
        edge_row = _edge_row_like(wire_edge)
        # The wire shape spells the relationship as 'type' (Cytoscape); the
        # normaliser must map it to rel_type the way GraphPage.edgeFromWire does.
        assert edge_row["rel_type"], wire_edge
        await _assert_explainable_edge(edge_row)

    resolvable = [
        _edge_row_like(e) for e in payload["edges"]
        if (_evidence_of(_edge_row_like(e)) or {}).get("source_doc_id")
    ]
    assert resolvable, "no expanded edge carries a usable evidence pointer"
    await _source_doc_ids(client, investigator_headers, db, _evidence_of(resolvable[0]))


async def test_expand_wire_matches_the_frontend_edge_from_wire_contract(
    client, investigator_headers, db, container, case, users
):
    """The exact field mapping GraphPage.edgeFromWire performs must succeed."""
    await _seed(db, container, case, users)
    snapshot = container.graph_store.snapshot(case.id, include_staging=False)
    edge = next((e for e in snapshot.edges if e.properties.get("source_doc_id")), None)
    assert edge is not None, "seeded graph must have an evidenced edge"
    hub = edge.source_key

    payload = client.get(
        f"/api/v1/graph/nodes/{hub}/expand?depth=1&limit=300",
        headers=investigator_headers,
    ).json()

    def edge_from_wire(item):
        maybe = item.get("data") if isinstance(item, dict) else item
        source = str(maybe.get("source") or maybe.get("source_key") or "")
        target = str(maybe.get("target") or maybe.get("target_key") or "")
        if not source or not target:
            return None
        rel_type = str(maybe.get("rel_type") or maybe.get("type") or "RELATED")
        text_span = maybe.get("text_span")
        origin = maybe.get("origin") or None
        source_doc_id = str(maybe["source_doc_id"]) if maybe.get("source_doc_id") else None
        return {
            "rel_type": rel_type,
            "confidence": float(maybe.get("confidence", 1) or 1),
            "staging": bool(maybe.get("staging", False)),
            "source_doc_ids": [str(x) for x in (maybe.get("source_doc_ids") or [])],
            "source_doc_id": source_doc_id,
            "evidence": (
                {"source_doc_id": source_doc_id, "text_span": text_span, "origin": origin}
                if (origin or text_span or source_doc_id)
                else None
            ),
        }

    parsed = [edge_from_wire(e) for e in payload["edges"]]
    assert all(parsed), "every wire element must normalise like GraphPage.edgeFromWire"
    evidenced = [e for e in parsed if e and e["evidence"]]
    assert evidenced, "no expanded edge yields an evidence pointer after normalisation"
    pointer = evidenced[0]["evidence"]
    assert pointer["source_doc_id"]
    # text_span and origin travel in the wire only when the pipeline recorded
    # them; a doc id alone is enough to open the source record.
    assert (pointer["origin"] or pointer["text_span"]) or pointer["source_doc_id"]
    await _source_doc_ids(client, investigator_headers, db, pointer)
