"""Graph → source traceability: the security and wire contracts behind it.

The graph detail panels open a node/edge's ``source_doc_ids`` in the existing
source viewer.  For that to be safe and meaningful four things must hold, and
they are asserted here:

1.  The wire rows carry their provenance: every ``_node_row`` /
    ``_edge_row`` emits ``source_doc_ids`` and never leaks internal
    extraction fields (``origin``, ``text_span``, merge bookkeeping) — the UI
    renders exactly this list, so a leak here becomes a fake source.
2.  The element→document jump stays case-scoped even for a hand-supplied
    (stale or malicious) doc id: opening ``/evidence/{doc_id}`` or its
    ``/provenance`` twin for a document of another jurisdiction's case is
    rejected, while the owning jurisdiction can open it.
3.  The endpoints require authentication at all.
4.  The reverse direction works: the document detail of the explorer
    (``/explore/documents/{doc_id}``) reports which graph entities were
    extracted from it, taken from the authoritative graph snapshot — never
    from a guess.
"""

from __future__ import annotations

import pytest

from app.db.session import async_session


# ---------------------------------------------------------------------------
# 1. Wire rows carry provenance — and only provenance
# ---------------------------------------------------------------------------


def _graph_node() -> "object":
    from app.domain.models import GraphNode

    return GraphNode(
        provenance_key="person:trace-1",
        label="Person",
        properties={
            "name": "Trace Kumar",
            "role": "Accused",
            "case_ids": ["case-trace"],
            "source_doc_ids": ["FIR-009", "CDR-010"],
            "aliases": ["Trace"],
            # Extraction internals that must NOT surface as UI properties:
            "origin": {"file": "fir.txt", "row": 3},
            "text_span": [10, 40],
        },
    )


def _graph_edge() -> "object":
    from app.domain.models import GraphEdge

    return GraphEdge(
        source_key="person:trace-1",
        target_key="phone:trace-9",
        rel_type="USES_PHONE",
        properties={
            "key": "person:trace-1->phone:trace-9:USES_PHONE",
            "source_doc_ids": ["CDR-010"],
            "source_doc_id": "CDR-010",
            "origin": {"file": "cdr.csv", "row": 9},
            "text_span": [0, 5],
            "confidence": 0.95,
        },
    )


def test_node_row_carries_source_doc_ids_and_hides_extraction_internals() -> None:
    from app.services.graph_service import _node_row

    row = _node_row(_graph_node())
    assert row["source_doc_ids"] == ["FIR-009", "CDR-010"], "multi-source provenance"
    assert row["case_ids"] == ["case-trace"]
    assert row["evidence"] is not None, "the origin pointer accompanies the list"
    props = row["properties"]
    assert props["role"] == "Accused", "domain properties survive"
    for leaked in ("origin", "text_span", "source_doc_ids", "case_ids"):
        assert leaked not in props, f"{leaked} must not be re-exposed as a property"


def test_edge_row_carries_source_doc_ids_and_hides_extraction_internals() -> None:
    from app.services.graph_service import _edge_row

    row = _edge_row(_graph_edge())
    assert row["source_doc_ids"] == ["CDR-010"]
    assert row["source_doc_id"] == "CDR-010"
    props = row["properties"]
    for leaked in ("origin", "text_span", "source_doc_ids", "source_doc_id"):
        assert leaked not in props, f"{leaked} must not be re-exposed as a property"


# ---------------------------------------------------------------------------
# 2 + 3. The source-open chain stays authenticated and case-scoped
# ---------------------------------------------------------------------------


@pytest.fixture()
async def kota_document(container):
    """One real evidence document in the KOTA jurisdiction's case."""
    from app.datasets import registry
    from app.db.base import new_uuid
    from app.db.models import Case, CaseDocument
    from app.domain.enums import CaseStatus, DocumentType
    from app.domain.provenance import content_hash

    payload = b"KOTA case diary body for the traceability fixture.\n"
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Traceability fixture", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(), case_number="TRC-1042", title="Traceability fixture case",
            jurisdiction_id="RJ-KOTA", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        doc = CaseDocument(
            id="doc-trace-kota", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.CASE_DIARY, filename="TRC-1042_diary.txt",
            storage_key="evidence/TRC-1042/diary.txt",
            content_hash=content_hash(payload),
            size_bytes=len(payload), mime_type="text/plain",
        )
        session.add(doc)
        await session.commit()

        container.object_store.put(
            container.settings.minio_bucket_documents,
            doc.storage_key,
            payload,
            content_type="text/plain",
        )

        yield {"doc_id": doc.id, "case_id": case.id, "dataset_id": ds.id}

        await registry.purge_dataset_data(session, ds.id)
        await session.commit()


def test_source_open_requires_authentication(client, kota_document) -> None:
    doc_id = kota_document["doc_id"]
    for path in (f"/api/v1/evidence/{doc_id}",
                 f"/api/v1/evidence/{doc_id}/provenance",
                 f"/api/v1/explore/documents/{doc_id}"):
        assert client.get(path).status_code in (401, 403), path


def test_source_open_is_rejected_across_jurisdictions(
    client, investigator_headers, kota_document
) -> None:
    """A JAIPUR investigator must not open the KOTA case's document — the
    graph can never be a side channel to another case's evidence."""
    doc_id = kota_document["doc_id"]
    for path in (f"/api/v1/evidence/{doc_id}",
                 f"/api/v1/evidence/{doc_id}/provenance",
                 f"/api/v1/explore/documents/{doc_id}"):
        status = client.get(path, headers=investigator_headers).status_code
        assert status in (403, 404), (path, status)


def test_source_open_works_for_the_owning_jurisdiction(
    client, kota_headers, kota_document
) -> None:
    """The same request succeeds for the case's own investigator — the
    rejection above is scoping, not a broken document."""
    doc_id = kota_document["doc_id"]
    response = client.get(f"/api/v1/evidence/{doc_id}/provenance", headers=kota_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["document"]["id"] == doc_id
    assert payload["case"]["case_number"] == "TRC-1042"


# ---------------------------------------------------------------------------
# 4. Reverse direction: source → extracted graph entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_detail_reports_extracted_entities_from_the_graph(
    client, kota_headers, kota_document, container
) -> None:
    """FIR-009 knows which nodes it produced: source → graph, not just the
    graph → source direction.  The list comes from the authoritative graph
    snapshot, seeded here with one node sourced by the fixture document."""
    from app.db.base import new_uuid
    from app.domain.models import GraphNode

    doc_id = kota_document["doc_id"]
    case_id = kota_document["case_id"]

    # The container fixture isolates the graph snapshot to a tmp file per
    # test, so this seed can never leak into another test's graph.
    store = container.graph_store
    node = GraphNode(
        provenance_key=f"person:{new_uuid()}",
        label="Person",
        properties={
            "name": "Trace Verma",
            "role": "Accused",
            "case_ids": [case_id],
            "source_doc_ids": [doc_id],
        },
    )
    store.upsert_nodes([node])

    response = client.get(
        f"/api/v1/explore/documents/{doc_id}", headers=kota_headers
    )
    assert response.status_code == 200, response.text
    entities = response.json()["entities"]
    keys = {e["provenance_key"] for e in entities}
    assert node.provenance_key in keys, entities
    row = next(e for e in entities if e["provenance_key"] == node.provenance_key)
    assert row["source_doc_ids"] == [doc_id], "entity provenance round-trips"
