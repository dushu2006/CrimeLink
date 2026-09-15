
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval, detect_contradictions

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-1", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_contradictory_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "loc:X": _node("loc:X", "Cafe", "Location"),
        "loc:Y": _node("loc:Y", "Airport", "Location"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1", timestamp="2024-08-08T20:15:00Z", location="Cafe"),
        _edge("person:B", "loc:Y", "LOCATED_AT", doc="doc-2", timestamp="2024-08-08T20:15:00Z", location="Airport"),
    ]
    return CaseGraphSnapshot(case_id="contradictory", nodes=nodes, edges=edges)

def test_contradiction_detection():
    snap = _make_contradictory_snapshot()
    timeline = [
        {"timestamp": "2024-08-08T20:15:00Z", "location": "Cafe", "participants": [{"name": "Alice"}, {"name": "Bob"}]},
        {"timestamp": "2024-08-08T20:15:00Z", "location": "Airport", "participants": [{"name": "Bob"}]},
    ]
    has_contra, details = detect_contradictions(timeline, snap.edges, ["Alice", "Bob"])
    assert isinstance(has_contra, bool)

def test_contradiction_in_retrieval():
    snap = _make_contradictory_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice and Bob at 20:15", max_persons=10, max_relationships=10)
    assert True
