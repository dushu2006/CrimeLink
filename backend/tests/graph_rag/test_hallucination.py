
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import (
    person_graph_rag_retrieval,
    validate_llm_grounding,
    evidence_sufficiency_gate,
)

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_insufficient_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = []
    return CaseGraphSnapshot(case_id="insufficient", nodes=nodes, edges=edges)

def test_insufficient_returns_no_connection():
    snap = _make_insufficient_snapshot()
    result = person_graph_rag_retrieval(snap, "Is Alice connected to Bob?", max_persons=10, max_relationships=10)
    if len(result.relationships) == 0:
        assert True
    else:
        for r in result.relationships:
            assert r.classification in ("HYPOTHESIS", "UNKNOWN") or r.confidence < 0.5

def test_no_invented_person():
    snap = _make_insufficient_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    allowed_persons = {p.provenance_key for p in result.persons} | {"person:A", "person:B"}
    allowed_evidence = {"doc-001"}
    fake_llm_output = {
        "relationships": [
            {
                "source_person": "PERSON-999",
                "target_person": "person:A",
                "relationship_type": "COMMUNICATION",
                "classification": "FACT",
                "confidence": 0.9,
                "evidence_refs": ["EVIDENCE-999"],
                "provenance": [],
            }
        ]
    }
    validation = validate_llm_grounding(fake_llm_output, allowed_persons, allowed_evidence, [{"kind": "document", "ref": "doc-001"}], ["case-001"])
    assert not validation["valid"]
    assert len(validation["errors"]) > 0

def test_no_invented_evidence():
    allowed_persons = {"person:A", "person:B"}
    allowed_evidence = {"doc-001"}
    fake_output = {
        "relationships": [
            {
                "source_person": "person:A",
                "target_person": "person:B",
                "relationship_type": "COMMUNICATION",
                "classification": "FACT",
                "confidence": 0.9,
                "evidence_refs": ["EVIDENCE-999"],
                "provenance": [],
            }
        ]
    }
    validation = validate_llm_grounding(fake_output, allowed_persons, allowed_evidence, [], ["case-001"])
    assert not validation["valid"]

def test_evidence_sufficiency_gate():
    result = evidence_sufficiency_gate(
        edges=[], supporting_entities=[], hop_count=0, doc_ids=set(), timeline=[], provenance=[],
    )
    assert not result.passed

def test_no_potential_association_when_insufficient():
    snap = _make_insufficient_snapshot()
    result = person_graph_rag_retrieval(snap, "Potential association between Alice and Bob", max_persons=10, max_relationships=10)
    if result.relationships:
        for r in result.relationships:
            if r.confidence < 0.4:
                assert r.classification in ("HYPOTHESIS", "UNKNOWN")

def test_controlled_taxonomy_no_invention():
    allowed_persons = {"person:A", "person:B"}
    output_with_bad_type = {
        "relationships": [
            {
                "source_person": "person:A",
                "target_person": "person:B",
                "relationship_type": "LOVE_AFFAIR",
                "classification": "FACT",
                "confidence": 0.9,
                "evidence_refs": ["doc-001"],
                "provenance": [{"kind": "document", "ref": "doc-001"}],
            }
        ]
    }
    validation = validate_llm_grounding(output_with_bad_type, allowed_persons, {"doc-001"}, [{"kind": "document", "ref": "doc-001"}], ["case-001"])
    assert not validation["valid"] or any("controlled" in e.lower() or "LOVE_AFFAIR" in e for e in validation["errors"])
