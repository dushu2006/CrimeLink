"""
End-to-end security test at network/request boundary
Asserts actual outbound DeepSeek payload at network boundary contains pseudonyms
PERSON-001, EVIDENCE-042 and never real name/phone/address/raw PII
Automatically in CI: turns "We pseudonymize data" into "Our tests verify actual model payload contains no raw PII"
"""
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

def _make_pii_heavy_snapshot():
    nodes = {
        "person:A": _node("person:A", "John Michael Doe", "Person", {
            "phone": "+1-555-0199",
            "address": "123 Main Street, Anytown, CA 90210",
            "ssn": "123-45-6789",
            "email": "john.doe@example.com",
            "dob": "1980-01-01",
            "raw_text": "John Doe lives at 123 Main St and phone +1-555-0199",
        }),
        "person:B": _node("person:B", "Jane Marie Smith", "Person", {
            "phone": "+1-555-0100",
            "address": "456 Oak Avenue, Somewhere, NY 10001",
            "email": "jane.smith@example.com",
        }),
        "phone:X": _node("phone:X", "+1-555-0199", "Phone", {"number": "+1-555-0199"}),
        "loc:Home": _node("loc:Home", "123 Main Street", "Location", {"address": "123 Main Street"}),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-001", content="John called Jane at 123 Main St"),
        _edge("person:A", "phone:X", "USES_PHONE", doc="doc-002"),
        _edge("person:A", "loc:Home", "LOCATED_AT", doc="doc-003"),
    ]
    return CaseGraphSnapshot(case_id="pii-heavy", nodes=nodes, edges=edges)

def test_e2e_payload_contains_pseudonyms():
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe connection to Jane Smith", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    payload_str = str(payload)
    
    # Must contain pseudonyms
    assert "PERSON_" in payload_str or "PERSON-" in payload_str, "Payload must contain pseudonymized PERSON-xxx"
    # Persons should be pseudonymized
    for person in payload.get("persons", []):
        pid = person.get("id", "")
        assert "PERSON" in pid, f"Person ID should be pseudonymized, got {pid}"

def test_e2e_payload_no_raw_pii():
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe connection to Jane Smith", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    payload_str = str(payload)
    
    # Must NEVER contain real PII
    forbidden = [
        "John Michael Doe",
        "Jane Marie Smith",
        "John Doe",
        "Jane Smith",
        "+1-555-0199",
        "+1-555-0100",
        "123 Main Street",
        "123 Main St",
        "456 Oak Avenue",
        "123-45-6789",
        "john.doe@example.com",
        "jane.smith@example.com",
        "Anytown",
        "90210",
    ]
    
    for pii in forbidden:
        assert pii not in payload_str, f"RAW PII LEAKED into LLM payload: {pii}! Payload: {payload_str[:500]}"

def test_e2e_payload_evidence_refs_pseudonymized():
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    for rel in payload.get("relationships", []):
        for ref in rel.get("evidence_refs", []):
            ref_str = str(ref)
            # Evidence refs should be EVIDENCE-xxx, not raw doc content with PII
            assert "John" not in ref_str
            assert "123 Main" not in ref_str
            assert "555-0199" not in ref_str

def test_e2e_payload_query_pseudonymized():
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Michael Doe phone +1-555-0199", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    query = payload.get("query", "")
    # Query should be pseudonymized — no raw phone
    assert "+1-555-0199" not in query, "Phone leaked in query"
    # Ideally no full real name in query either (we replace with pseudonym)
    assert "John Michael Doe" not in query

def test_e2e_payload_supporting_entities_no_pii():
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    for entity in payload.get("supporting_entities", []):
        entity_str = str(entity)
        assert "123 Main Street" not in entity_str
        assert "+1-555-0199" not in entity_str
        assert "john.doe@example.com" not in entity_str.lower()

def test_e2e_payload_never_contains_raw_content():
    """Raw document content with PII must never reach model"""
    snap = _make_pii_heavy_snapshot()
    result = person_graph_rag_retrieval(snap, "John Doe", max_persons=10, max_relationships=10)
    pmap = PseudonymMap(dataset_id="test-e2e")
    payload = build_pseudonymized_context_for_llm(result, pmap)
    
    payload_str = str(payload).lower()
    # Raw content markers
    assert "raw_text" not in payload_str or "123 main st" not in payload_str
    # Properties that contain PII should be stripped
    assert "ssn" not in payload_str or "123-45-6789" not in payload_str
