
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval, create_no_connection_result

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-1", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_no_conn_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "loc:X": _node("loc:X", "Park", "Location"),
    }
    edges = [
        _edge("person:A", "loc:X", "LOCATED_AT", doc="doc-1"),
    ]
    return CaseGraphSnapshot(case_id="no-conn", nodes=nodes, edges=edges)

def test_no_connection_result():
    snap = _make_no_conn_snapshot()
    result = person_graph_rag_retrieval(snap, "Is Alice connected to Bob?", max_persons=10, max_relationships=10)
    if len(result.relationships) == 0:
        no_conn = create_no_connection_result(result)
        assert no_conn.people_searched >= 0
        assert no_conn.reliable_relationships_found == 0
        assert "No reliable person-to-person connection" in no_conn.reason
    else:
        for r in result.relationships:
            assert r.classification in ("HYPOTHESIS", "UNKNOWN") or r.confidence < 0.5

def test_no_connection_ui_fields():
    snap = _make_no_conn_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    if not result.relationships:
        assert result.compact_context.get("no_connection") is not None
        nc = result.compact_context["no_connection"]
        assert "people_searched" in nc
        assert "evidence_examined" in nc
        assert nc["reliable_relationships_found"] == 0
