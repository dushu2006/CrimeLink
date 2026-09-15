
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-1", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_multi_hop_snapshot():
    nodes = {
        f"person:{c}": _node(f"person:{c}", c, "Person") for c in ["A","B","C","D"]
    }
    nodes.update({
        "phone:1": _node("phone:1", "P1", "Phone"),
        "phone:2": _node("phone:2", "P2", "Phone"),
        "phone:3": _node("phone:3", "P3", "Phone"),
    })
    edges = [
        _edge("person:A", "phone:1", "USES_PHONE", doc="doc-1"),
        _edge("phone:1", "phone:2", "CALLED", doc="doc-1"),
        _edge("phone:2", "person:B", "USES_PHONE", doc="doc-1"),
        _edge("person:B", "phone:2", "USES_PHONE", doc="doc-2"),
        _edge("phone:2", "phone:3", "CALLED", doc="doc-2"),
        _edge("phone:3", "person:C", "USES_PHONE", doc="doc-2"),
        _edge("person:C", "phone:3", "USES_PHONE", doc="doc-3"),
        _edge("phone:3", "person:D", "CALLED", doc="doc-3"),
    ]
    return CaseGraphSnapshot(case_id="multi-hop", nodes=nodes, edges=edges)

def test_multi_hop_discovery():
    snap = _make_multi_hop_snapshot()
    result = person_graph_rag_retrieval(snap, "connection between A and D", max_persons=10, max_relationships=10, max_hops=4)
    assert len(result.relationships) >= 1

def test_multi_hop_path_validity():
    snap = _make_multi_hop_snapshot()
    result = person_graph_rag_retrieval(snap, "A to D", max_persons=10, max_relationships=10, max_hops=4)
    for r in result.relationships:
        assert r.hop_count >= 1
        assert r.hop_count <= 4
        assert len(r.reasoning_path) >= 2
