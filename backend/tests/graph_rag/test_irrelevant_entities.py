
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-1", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_irrelevant_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "person:Z": _node("person:Z", "Zara", "Person"),
        "loc:Park": _node("loc:Park", "Central Park", "Location"),
        "org:Acme": _node("org:Acme", "Acme Corp", "Organization"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1"),
        _edge("person:Z", "loc:Park", "LOCATED_AT", doc="doc-2"),
        _edge("person:Z", "org:Acme", "MEMBER_OF", doc="doc-3"),
    ]
    return CaseGraphSnapshot(case_id="irrelevant", nodes=nodes, edges=edges)

def test_irrelevant_filtering():
    snap = _make_irrelevant_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice Bob connection", max_persons=10, max_relationships=10)
    person_keys = [p.provenance_key for p in result.persons]
    assert "person:A" in person_keys or "person:B" in person_keys
    for r in result.relationships:
        assert "person:" in r.source_real_key
        assert "person:" in r.target_real_key

def test_person_first():
    snap = _make_irrelevant_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice", max_persons=5, max_relationships=10)
    for p in result.persons:
        assert p.label.upper() == "PERSON" or "person" in p.label.lower()
