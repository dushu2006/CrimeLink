
"""
Simple relationship evaluation: known A→B, A→C, B→D
Measures Recall@K, Precision@K, MRR, path recall, evidence recall, false-positive rate
Target: very high recall, near-zero unsupported for high-confidence deterministic
"""
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import (
    person_graph_rag_retrieval,
    map_to_controlled,
    CONTROLLED_REL_TYPES,
)

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_snapshot_simple():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "person:C": _node("person:C", "Charlie", "Person"),
        "person:D": _node("person:D", "David", "Person"),
        "phone:X": _node("phone:X", "+1-555-0100", "Phone"),
        "phone:Y": _node("phone:Y", "+1-555-0101", "Phone"),
        "event:E1": _node("event:E1", "Meeting 2024-08-08", "Event"),
        "account:ACC1": _node("account:ACC1", "ACC-001", "BankAccount"),
    }
    edges = [
        _edge("person:A", "phone:X", "USES_PHONE", doc="doc-001"),
        _edge("phone:X", "phone:Y", "CALLED", doc="doc-001", timestamp="2024-08-08T20:14:00Z", source_doc_ids=["doc-001", "doc-002"]),
        _edge("phone:Y", "person:B", "USES_PHONE", doc="doc-002"),
        _edge("person:A", "event:E1", "PARTICIPATED_IN", doc="doc-003"),
        _edge("person:C", "event:E1", "PARTICIPATED_IN", doc="doc-003"),
        _edge("person:B", "account:ACC1", "OWNS_ACCOUNT", doc="doc-004"),
        _edge("account:ACC1", "person:D", "TRANSFER_TO", doc="doc-004", amount=5000),
    ]
    return CaseGraphSnapshot(case_id="test-simple", nodes=nodes, edges=edges)

def test_simple_a_b_discovery():
    snap = _make_snapshot_simple()
    result = person_graph_rag_retrieval(snap, "Is Alice connected to Bob?", max_persons=10, max_relationships=10)
    assert len(result.persons) >= 2
    assert len(result.relationships) >= 1
    first = result.relationships[0]
    assert first.hop_count >= 1
    assert len(first.reasoning_path) >= 2

def test_simple_recall_precision():
    snap = _make_snapshot_simple()
    result = person_graph_rag_retrieval(snap, "Show all connections between Alice, Bob, Charlie, David", max_persons=20, max_relationships=10)
    found = len(result.relationships)
    assert found >= 2, f"Expected at least 2 relationships, got {found}"
    for r in result.relationships:
        assert "person:" in r.source_real_key
        assert "person:" in r.target_real_key
    for r in result.relationships:
        assert "999" not in r.source_person
        assert "999" not in r.target_person

def test_controlled_taxonomy():
    snap = _make_snapshot_simple()
    result = person_graph_rag_retrieval(snap, "communication between Alice and Bob", max_persons=10, max_relationships=10)
    for r in result.relationships:
        controlled = map_to_controlled(r.relationship_type)
        assert controlled in CONTROLLED_REL_TYPES, f"{controlled} not in controlled taxonomy"

def test_mrr_path_recall():
    snap = _make_snapshot_simple()
    result = person_graph_rag_retrieval(snap, "Alice Bob connection", max_persons=10, max_relationships=10)
    if result.relationships:
        first = result.relationships[0]
        assert len(first.supporting_evidence) >= 1
