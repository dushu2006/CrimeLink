"""Regression coverage for the ontology/evidence boundary."""

from types import SimpleNamespace

import pytest

from app.ai.pseudonymize import PseudonymMap, apply_pseudonymization_to_context
from app.analytics.centrality import build_nx_graph, compute_centrality
from app.analytics.findings import generate_findings
from app.datasets import normalize as nz
from app.datasets import schema_map as sm
from app.datasets.graph_build import _graph_label, build_node
from app.domain.models import CaseGraphSnapshot, GraphEdge, GraphNode


def _node(key: str, label: str, *, entity_type: str | None = None) -> GraphNode:
    return GraphNode(
        provenance_key=key,
        label=label,
        properties={
            "entity_type": entity_type or label.upper(),
            "name": key,
            "source_doc_id": "source-row-1",
        },
    )


def _edge(source: str, target: str, rel_type: str, **props: object) -> GraphEdge:
    return GraphEdge(
        source_key=source,
        target_key=target,
        rel_type=rel_type,
        properties={"source_doc_id": "source-row-1", **props},
        key=f"{source}-{target}-{rel_type}",
    )


def test_document_and_unknown_types_have_no_person_fallback():
    assert _graph_label(sm.DOCUMENT) is None
    assert _graph_label(sm.EVIDENCE) is None
    assert _graph_label("PARSER_ARTIFACT") is None
    entity = SimpleNamespace(
        canonical_id="DOCUMENT:README.md",
        entity_type=sm.DOCUMENT,
        name="README.md",
        display_name="README.md",
        normalized_value="README.md",
        attributes={},
        provenance={},
    )
    with pytest.raises(ValueError, match="provenance-only"):
        build_node("dataset-1", entity)


def test_legacy_document_nodes_are_excluded_from_every_centrality_metric():
    snapshot = CaseGraphSnapshot(
        case_id="case-1",
        nodes={
            "p1": _node("p1", "Person", entity_type="PERSON"),
            "p2": _node("p2", "Person", entity_type="PERSON"),
            "doc": _node("doc", "Event", entity_type="DOCUMENT"),
        },
        edges=[
            _edge("p1", "p2", "ASSOCIATE_OF"),
            _edge("p1", "doc", "MENTIONED_IN"),
            _edge("doc", "p2", "MENTIONED_IN"),
        ],
    )
    graph = build_nx_graph(snapshot)
    assert "doc" not in graph
    result = compute_centrality(snapshot)
    assert result.node_count == 2
    assert "doc" not in result.degree
    assert "doc" not in result.pagerank
    assert all("doc" not in members for members in result.community_members.values())


def test_shared_identifier_is_explicit_derived_relationship_not_associate():
    result = nz.NormalizationResult()
    for person in ("PERSON:P1", "PERSON:P2"):
        result.add_entity(nz.CanonicalEntity(person, sm.PERSON, name=person))
    result.add_entity(nz.CanonicalEntity("PHONE:1", sm.PHONE, name="masked"))
    result.add_relationship(nz.CanonicalRelationship(
        "PERSON:P1", "PHONE:1", nz.REL_USES_PHONE,
        provenance={"doc_id": "cdr-1", "file": "calls.csv", "row": 2},
    ))
    result.add_relationship(nz.CanonicalRelationship(
        "PERSON:P2", "PHONE:1", nz.REL_USES_PHONE,
        provenance={"doc_id": "cdr-1", "file": "calls.csv", "row": 3},
    ))
    assert result.derive_shared_identifier_relationships() == 1
    derived = [r for r in result.relationships.values() if r.rel_type == nz.REL_SHARED_PHONE]
    assert len(derived) == 1
    assert derived[0].attributes["direct_vs_derived"] == "derived"
    assert derived[0].attributes["supporting_edge_keys"]
    assert all(r.rel_type != nz.REL_ASSOCIATE_OF for r in derived)


def test_stable_dataset_pseudonym_and_source_identifier_minimization():
    first = PseudonymMap(dataset_id="dataset-stable")
    second = PseudonymMap(dataset_id="dataset-stable")
    first_id = first.pseudonymize("ds:dataset-stable:PERSON:P1", "Person")
    assert second.pseudonymize("ds:dataset-stable:PERSON:P1", "Person") == first_id

    nodes = [{
        "provenance_key": "ds:dataset-stable:PERSON:P1",
        "label": "Person",
        "properties": {"name": "Secret Name", "phone_number": "+919999999999"},
    }]
    edges = [{
        "source_key": "ds:dataset-stable:PERSON:P1",
        "target_key": "ds:dataset-stable:PHONE:1",
        "rel_type": "USES_PHONE",
        "source_doc_id": "real-document-id",
        "source_doc_ids": ["real-document-id"],
    }]
    safe_nodes, safe_edges = apply_pseudonymization_to_context(nodes, edges, second)
    assert "Secret Name" not in repr(safe_nodes)
    assert "+919999999999" not in repr(safe_nodes)
    assert "real-document-id" not in repr(safe_edges)

def test_hard_identifier_graph_identity_collapses_duplicate_vehicle_rows():
    from app.datasets.graph_build import entity_graph_key

    first = SimpleNamespace(
        canonical_id="VEHICLE:row-001",
        entity_type=sm.VEHICLE,
        normalized_value="AP 39 JK 2086",
    )
    second = SimpleNamespace(
        canonical_id="VEHICLE:row-987",
        entity_type=sm.VEHICLE,
        normalized_value="AP 39 JK 2086",
    )
    assert entity_graph_key("dataset-1", first) == entity_graph_key("dataset-1", second)
    assert entity_graph_key("dataset-1", first) != entity_graph_key("dataset-2", first)


def test_hard_identifier_graph_identity_collapses_duplicate_account_rows():
    from app.datasets.graph_build import entity_graph_key

    first = SimpleNamespace(
        canonical_id="ACCOUNT:row-001",
        entity_type=sm.ACCOUNT,
        normalized_value="001122334455",
    )
    second = SimpleNamespace(
        canonical_id="ACCOUNT:row-987",
        entity_type=sm.ACCOUNT,
        normalized_value="001122334455",
    )
    assert entity_graph_key("dataset-1", first) == entity_graph_key("dataset-1", second)
    assert entity_graph_key("dataset-1", first) != entity_graph_key("dataset-2", first)
