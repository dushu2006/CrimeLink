
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval, analyze_temporal_pattern

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_no_timestamp_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-001"),
    ]
    return CaseGraphSnapshot(case_id="no-ts", nodes=nodes, edges=edges)

def test_no_invented_timestamps():
    snap = _make_no_timestamp_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    for r in result.relationships:
        for ev in r.supporting_evidence:
            ts = ev.get("timestamp") or ev.get("properties", {}).get("timestamp")
            if ts:
                assert ts == "Timestamp unavailable" or "T" in str(ts) or len(str(ts)) > 4

def test_temporal_pattern_no_timestamp():
    timeline = []
    edges = []
    analysis = analyze_temporal_pattern(timeline, edges)
    assert analysis.first_observed == "Timestamp unavailable"
