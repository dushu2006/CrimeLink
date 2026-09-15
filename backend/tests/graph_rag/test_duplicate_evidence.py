
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import person_graph_rag_retrieval, deduplicate_relationships, PersonRelationship

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def _make_duplicate_snapshot():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "phone:X": _node("phone:X", "555-0100", "Phone"),
        "phone:Y": _node("phone:Y", "555-0101", "Phone"),
        "doc:D1": _node("doc:D1", "Record 1", "Document"),
    }
    edges = [
        _edge("person:A", "phone:X", "USES_PHONE", doc="doc-001"),
        _edge("phone:X", "phone:Y", "CALLED", doc="doc-001"),
        _edge("phone:Y", "person:B", "USES_PHONE", doc="doc-001"),
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-002"),
        _edge("person:A", "person:B", "SHARED_IDENTIFIER", doc="doc-003"),
    ]
    return CaseGraphSnapshot(case_id="duplicate", nodes=nodes, edges=edges)

def test_deduplication():
    snap = _make_duplicate_snapshot()
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    assert len(result.relationships) <= 2
    if result.relationships:
        r = result.relationships[0]
        assert len(r.evidence_refs) >= 1 or len(r.supporting_evidence) >= 1

def test_deduplication_function():
    rels = [
        PersonRelationship(
            source_person="person:A", target_person="person:B",
            source_real_key="person:A", target_real_key="person:B",
            relationship_type="Communication", classification="FACT", confidence=0.8,
            confidence_label="High", supporting_evidence=[{"edge_key": "e1"}], provenance=[],
            explanation="A↔B", why="phone", reasoning_path=["person:A", "phone:X", "person:B"],
            reasoning_path_typed=[], timeline=[], limitations=[], evidence_refs=["doc-001"],
            source_doc_ids=["doc-001"], hop_count=2, evidence_strength="STRONG"
        ),
        PersonRelationship(
            source_person="person:A", target_person="person:B",
            source_real_key="person:A", target_real_key="person:B",
            relationship_type="Communication", classification="FACT", confidence=0.85,
            confidence_label="High", supporting_evidence=[{"edge_key": "e4"}], provenance=[],
            explanation="A↔B", why="record", reasoning_path=["person:A", "person:B"],
            reasoning_path_typed=[], timeline=[], limitations=[], evidence_refs=["doc-002"],
            source_doc_ids=["doc-002"], hop_count=1, evidence_strength="STRONG"
        ),
    ]
    deduped = deduplicate_relationships(rels)
    assert len(deduped) == 1
    assert len(deduped[0].evidence_refs) >= 2 or len(deduped[0].supporting_evidence) >= 2
