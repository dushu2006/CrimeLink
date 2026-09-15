
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval, build_pseudonymized_context_for_llm
from app.ai.pseudonymize import PseudonymMap

def _node(key, name, label="Person", extra=None):
    props = {"name": name, "case_ids": ["c1"]}
    if extra:
        props.update(extra)
    return GraphNode(provenance_key=key, label=label, properties=props)

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_pii_snapshot():
    nodes = {
        "person:A": _node("person:A", "John Doe", "Person", {"phone": "+1-555-0199", "address": "123 Main St"}),
        "person:B": _node("person:B", "Jane Smith", "Person", {"phone": "+1-555-0100"}),
        "phone:X": _node("phone:X", "+1-555-0199", "Phone", {"number": "+1-555-0199"}),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-001"),
    ]
    return CaseGraphSnapshot(case_id="pii-test", nodes=nodes, edges=edges)

def test_pseudonymization_no_raw_pii():
    snap = _make_pii_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe connection to Jane Smith", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    payload_str = str(payload)
    assert "John Doe" not in payload_str, "Real name leaked!"
    assert "Jane Smith" not in payload_str, "Real name leaked!"
    assert "+1-555-0199" not in payload_str, "Phone leaked!"
    assert "123 Main St" not in payload_str, "Address leaked!"
    for person in payload.get("persons", []):
        pid = person.get("id", "")
        assert "John" not in pid
        assert "Jane" not in pid

def test_pseudonymization_evidence_refs():
    snap = _make_pii_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    for rel in payload.get("relationships", []):
        for ref in rel.get("evidence_refs", []):
            assert "John" not in str(ref)
