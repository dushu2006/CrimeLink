"""Discarding a document must retire the graph entities it produced (§17).

The gap the live integrity audit found: an upload injects graph nodes and
edges citing the new document.  Soft-deleting the document row used to leave
those records standing, citing a document that no longer resolves — so an
investigator could still see the edge, click "Show Provenance", and land on
"Provenance unavailable" for a record that was still being displayed.

These tests drive the real embedded graph store, not a stand-in.
"""

from __future__ import annotations

import pytest

from app.domain.models import GraphEdge, GraphNode


@pytest.fixture()
def store(container):
    """A throwaway embedded graph store with one document's worth of entities."""
    from scripts.seed_demo_v2 import DEMO_DATASET_ID

    graph = container.graph_store
    nodes = [
        GraphNode(
            provenance_key="case:case-ret-1",
            label="Case",
            properties={
                "case_id": "case-ret-1",
                "case_number": "CR-9001",
                "name": "CR-9001",
                "title": "Retirement fixture case",
                "dataset_id": DEMO_DATASET_ID,
            },
        ),
        # Two documents support this person, so retiring one must keep it.
        GraphNode(
            provenance_key="person:shared",
            label="Person",
            properties={
                "name": "Shared Person",
                "case_ids": ["case-ret-1"],
                "source_doc_ids": ["doc-a", "doc-b"],
                "dataset_id": DEMO_DATASET_ID,
            },
        ),
        GraphNode(
            provenance_key="person:other",
            label="Person",
            properties={
                "name": "Other Person",
                "case_ids": ["case-ret-1"],
                "source_doc_ids": ["doc-b"],
                "dataset_id": DEMO_DATASET_ID,
            },
        ),
        # Only doc-a supports this phone, so retiring doc-a must retire it.
        GraphNode(
            provenance_key="phone:sole",
            label="Phone",
            properties={
                "name": "+91-9000000001",
                "case_ids": ["case-ret-1"],
                "source_doc_ids": ["doc-a"],
                "dataset_id": DEMO_DATASET_ID,
            },
        ),
    ]
    edges = [
        GraphEdge(
            source_key="person:shared",
            target_key="phone:sole",
            rel_type="USES_PHONE",
            properties={
                "source_doc_id": "doc-a",
                "source_doc_ids": ["doc-a"],
                "case_id": "case-ret-1",
            },
        ),
        # Supported by doc-b only, between two nodes that both survive.
        GraphEdge(
            source_key="person:shared",
            target_key="person:other",
            rel_type="ASSOCIATE_OF",
            properties={
                "source_doc_id": "doc-b",
                "source_doc_ids": ["doc-b"],
                "case_id": "case-ret-1",
            },
        ),
    ]
    assert graph.upsert_nodes(nodes) == 4
    assert graph.upsert_edges(edges) == 2
    yield graph
    graph.purge_dataset(DEMO_DATASET_ID)


def _active_keys(store) -> set[str]:
    snap = store.snapshot("case-ret-1", include_inactive=False)
    return set(snap.nodes)


def test_the_fixture_graph_starts_fully_visible(store):
    # snapshot() deliberately omits Case-labelled nodes: the case is the scope
    # of the query, not a member of its result.
    assert _active_keys(store) == {"person:shared", "person:other", "phone:sole"}


def test_retiring_a_document_removes_only_the_entities_it_solely_supported(store):
    retired = store.retire_document("doc-a")

    assert retired == 1, "only the solely-supported phone should be retired"
    active = _active_keys(store)
    assert "phone:sole" not in active, "the unsupported entity must stop being served"
    assert "person:shared" in active, "an entity with other support must survive"
    assert "person:other" in active, "an entity supported by another doc survives"


def test_retiring_a_document_removes_the_edges_it_supported(store):
    store.retire_document("doc-a")

    snap = store.snapshot("case-ret-1", include_inactive=False)
    rels = {e.rel_type for e in snap.edges}
    assert "USES_PHONE" not in rels, "the edge lost its only supporting document"
    assert "ASSOCIATE_OF" in rels, "an edge supported elsewhere must survive"


def test_a_retired_entity_is_kept_for_audit_rather_than_destroyed(store):
    store.retire_document("doc-a")

    node = store.get_node("phone:sole")
    assert node is not None, "retirement must not destroy the record"
    props = node.properties if hasattr(node, "properties") else node
    assert props.get("is_active") is False
    assert props.get("retired_by_document") == "doc-a"
    assert "doc-a" in (props.get("source_doc_ids") or [])

    # …and the case really is still auditable when inactive records are asked for.
    full = store.snapshot("case-ret-1", include_inactive=True)
    assert "phone:sole" in full.nodes


def test_retiring_an_unknown_document_changes_nothing(store):
    before = _active_keys(store)
    assert store.retire_document("doc-does-not-exist") == 0
    assert _active_keys(store) == before


def test_retirement_is_idempotent(store):
    assert store.retire_document("doc-a") == 1
    assert store.retire_document("doc-a") == 0, "a second call must not re-retire"
    assert "phone:sole" not in _active_keys(store)


def test_retiring_an_empty_id_is_a_no_op(store):
    before = _active_keys(store)
    assert store.retire_document("") == 0
    assert _active_keys(store) == before


# --------------------------------------------------------------------------- #
# The discard path must actually call it
# --------------------------------------------------------------------------- #

async def test_discarding_a_document_retires_its_graph_entities(container, store):
    from app.db.models import CaseDocument
    from app.domain.enums import DocumentType, IngestionStatus
    from app.services.documents import discard_quarantined

    doc = CaseDocument(
        id="doc-a",
        case_id="case-ret-1",
        document_type=DocumentType.CDR,
        filename="cdr.csv",
        storage_key="case-ret-1/doc-a/cdr.csv",
        content_hash="0" * 64,
        size_bytes=1,
        ingestion_status=IngestionStatus.QUARANTINED,
        quarantined=True,
    )

    from app.db.session import async_session

    async with async_session() as session:
        result = await discard_quarantined(session, doc, container=container)

    assert result["retire_failed"] is False
    assert result["retired_nodes"] == 1
    assert doc.is_deleted is True, "the row is soft-deleted, never removed"
    assert "phone:sole" not in _active_keys(store)


async def test_discard_reports_a_retirement_failure_instead_of_hiding_it(container, store):
    """If the graph cannot be cleaned, the response must say so."""
    from app.db.models import CaseDocument
    from app.domain.enums import DocumentType, IngestionStatus
    from app.services.documents import discard_quarantined

    doc = CaseDocument(
        id="doc-b",
        case_id="case-ret-1",
        document_type=DocumentType.CDR,
        filename="cdr.csv",
        storage_key="case-ret-1/doc-b/cdr.csv",
        content_hash="0" * 64,
        size_bytes=1,
        ingestion_status=IngestionStatus.QUARANTINED,
        quarantined=True,
    )

    def _boom(_doc_id):
        raise RuntimeError("graph store unavailable")

    from app.db.session import async_session

    container.graph_store.retire_document = _boom  # type: ignore[method-assign]
    async with async_session() as session:
        result = await discard_quarantined(session, doc, container=container)

    assert result["retire_failed"] is True
    assert result["retired_nodes"] == 0
    assert doc.is_deleted is True, "the soft-delete still happened"
