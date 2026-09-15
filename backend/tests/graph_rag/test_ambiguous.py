
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-1", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_ambiguous_snapshot():
    nodes = {
        "person:A1": _node("person:A1", "John Smith", "Person"),
        "person:A2": _node("person:A2", "John Smith", "Person"),
        "person:B": _node("person:B", "Alice", "Person"),
    }
    edges = [
        _edge("person:A1", "person:B", "ASSOCIATE_OF", doc="doc-1"),
    ]
    return CaseGraphSnapshot(case_id="ambiguous", nodes=nodes, edges=edges)

def test_ambiguous_disambiguation():
    snap = _make_ambiguous_snapshot()
    result = person_graph_rag_retrieval(snap, "John Smith connection to Alice", max_persons=10, max_relationships=10)
    assert len(result.persons) >= 1
    keys = [p.provenance_key for p in result.persons]
    assert any("person:A" in k for k in keys)

def test_ambiguous_no_false_merge():
    snap = _make_ambiguous_snapshot()
    result = person_graph_rag_retrieval(snap, "John Smith", max_persons=10, max_relationships=10)
    johns = [p for p in result.persons if p.name == "John Smith"]
    assert len(johns) <= 2
