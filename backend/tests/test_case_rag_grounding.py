"""Regression tests for the hard Case RAG grounding boundary.

These tests intentionally exercise the provenance boundary independently of an
LLM.  A model cannot repair a contaminated context after retrieval, so the
expected behavior is asserted before prompt construction.
"""

from __future__ import annotations

from app.ai.case_context import validate_case_context
from app.ai.gateway import _retrieval_cache_key
from app.domain.models import GraphEdge, GraphNode


def _person(key: str, cases: list[str], doc: str) -> GraphNode:
    return GraphNode(
        provenance_key=key,
        label="Person",
        properties={
            "name": key,
            "case_ids": cases,
            "source_doc_id": doc,
            "source_doc_ids": [doc],
        },
    )


def _edge(source: str, target: str, cases: list[str], doc: str) -> GraphEdge:
    return GraphEdge(
        source_key=source,
        target_key=target,
        rel_type="ASSOCIATE_OF",
        properties={
            "case_ids": cases,
            "case_scope": cases,
            "source_doc_id": doc,
            "source_doc_ids": [doc],
        },
    )


def _dict_node(node: GraphNode) -> dict:
    return {
        "provenance_key": node.provenance_key,
        "label": node.label,
        "properties": dict(node.properties),
    }


def _dict_edge(edge: GraphEdge) -> dict:
    return {
        "source_key": edge.source_key,
        "target_key": edge.target_key,
        "rel_type": edge.rel_type,
        **dict(edge.properties),
    }


def test_context_rejects_cross_case_nodes_edges_and_documents():
    c1 = _person("shared", ["CR-2001", "CR-2019"], "doc-shared")
    c2 = _person("only-cr-2019", ["CR-2019"], "doc-2019")
    rel = _edge(c1.provenance_key, c2.provenance_key, ["CR-2019"], "doc-2019")

    result = validate_case_context(
        "CR-2001",
        nodes=[_dict_node(c1), _dict_node(c2)],
        edges=[_dict_edge(rel)],
        documents=[
            {"doc_id": "doc-shared", "case_id": "CR-2001"},
            {"doc_id": "doc-2019", "case_id": "CR-2019"},
        ],
    )

    assert [node["provenance_key"] for node in result.nodes] == ["shared"]
    assert result.edges == []
    assert [doc["doc_id"] for doc in result.documents] == ["doc-shared"]
    assert result.filtered_out_count == 3


def test_graph_snapshot_filters_relationship_provenance_not_endpoint_membership(
    graph,
):
    shared_a = _person("a", ["CR-2001", "CR-2019"], "doc-a")
    shared_b = _person("b", ["CR-2001", "CR-2019"], "doc-b")
    graph.upsert_nodes([shared_a, shared_b])
    graph.upsert_edges([_edge("a", "b", ["CR-2019"], "doc-2019")])

    assert graph.get_case_snapshot("CR-2001").edges == []
    assert len(graph.get_case_snapshot("CR-2019").edges) == 1


def test_case_counts_are_deterministic_for_same_case_context():
    a = _person("a", ["CR-2001"], "doc-a")
    b = _person("b", ["CR-2001"], "doc-b")
    result_one = validate_case_context(
        "CR-2001",
        nodes=[_dict_node(a), _dict_node(b)],
        edges=[_dict_edge(_edge("a", "b", ["CR-2001"], "doc-a"))],
        documents=[
            {"doc_id": "doc-a", "case_id": "CR-2001"},
            {"doc_id": "doc-b", "case_id": "CR-2001"},
        ],
    )
    result_two = validate_case_context(
        "CR-2001",
        nodes=[_dict_node(b), _dict_node(a)],
        edges=[_dict_edge(_edge("a", "b", ["CR-2001"], "doc-a"))],
        documents=[
            {"doc_id": "doc-b", "case_id": "CR-2001"},
            {"doc_id": "doc-a", "case_id": "CR-2001"},
        ],
    )

    assert result_one.stats.as_dict() == result_two.stats.as_dict() == {
        "case_id": "CR-2001",
        "evidence_count": 2,
        "entity_count": 2,
        "person_count": 2,
        "relationship_count": 1,
    }


def test_empty_case_context_has_no_entities_relationships_or_evidence():
    result = validate_case_context("CR-2001")
    assert result.stats.as_dict() == {
        "case_id": "CR-2001",
        "evidence_count": 0,
        "entity_count": 0,
        "person_count": 0,
        "relationship_count": 0,
    }


def test_history_case_data_cannot_enter_current_case_context():
    result = validate_case_context(
        "CR-2001",
        nodes=[
            {
                "provenance_key": "deepika",
                "label": "Person",
                "properties": {"case_ids": ["CR-2019"], "name": "Deepika"},
            }
        ],
        documents=[{"doc_id": "history-2019", "case_id": "CR-2019"}],
    )
    assert result.nodes == []
    assert result.documents == []


def test_cache_key_isolated_by_case_and_conversation():
    first = _retrieval_cache_key("CR-2001", "Who is connected?", conversation_id="s1")
    second = _retrieval_cache_key("CR-2019", "Who is connected?", conversation_id="s1")
    third = _retrieval_cache_key("CR-2001", "Who is connected?", conversation_id="s2")
    assert first != second
    assert first != third


def test_strict_validation_rejects_missing_provenance():
    result = validate_case_context(
        "CR-2001",
        nodes=[{"provenance_key": "unscoped", "label": "Person", "properties": {}}],
        documents=[{"doc_id": "unscoped-doc"}],
        require_scope=True,
    )
    assert result.nodes == []
    assert result.documents == []
    assert {item["reason"] for item in result.filtered_out} == {"missing_case_provenance"}
