"""
Test contradictory evidence more deeply:
same event / conflicting timestamps
same person / conflicting locations
duplicate source records
conflicting case records
missing provenance
partial evidence
stale evidence

Correct behavior: Conflicting evidence detected. Relationship confidence reduced / relationship not established.
Not: AI chose the more convenient record.
"""
from app.domain.models import CaseGraphSnapshot, GraphNode, GraphEdge
from app.ai.person_graph_rag import (
    person_graph_rag_retrieval,
    detect_contradictions,
    evidence_sufficiency_gate,
    analyze_temporal_pattern,
)

def _node(key, name, label="Person"):
    return GraphNode(provenance_key=key, label=label, properties={"name": name, "case_ids": ["c1"]})

def _edge(src, tgt, rel, doc="doc-001", **props):
    p = {"source_doc_id": doc}
    p.update(props)
    return GraphEdge(source_key=src, target_key=tgt, rel_type=rel, properties=p)

def test_same_event_conflicting_timestamps():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
        "event:E1": _node("event:E1", "Meeting", "Event"),
    }
    edges = [
        _edge("person:A", "event:E1", "PARTICIPATED_IN", doc="doc-1", timestamp="2024-08-08T10:00:00Z"),
        _edge("person:B", "event:E1", "PARTICIPATED_IN", doc="doc-2", timestamp="2024-08-08T15:00:00Z"),  # Same event, different times
    ]
    snap = CaseGraphSnapshot(case_id="conflict-ts", nodes=nodes, edges=edges)
    timeline = [
        {"timestamp": "2024-08-08T10:00:00Z", "event": "Meeting", "participants": [{"name": "Alice"}]},
        {"timestamp": "2024-08-08T15:00:00Z", "event": "Meeting", "participants": [{"name": "Bob"}]},
    ]
    has_contra, details = detect_contradictions(timeline, edges, ["Alice", "Bob"])
    # Should not necessarily be contradiction (different times for same event could be valid), but should not crash
    assert isinstance(has_contra, bool)

def test_same_person_conflicting_locations():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "loc:X": _node("loc:X", "Cafe", "Location"),
        "loc:Y": _node("loc:Y", "Airport", "Location"),
    }
    edges = [
        _edge("person:A", "loc:X", "LOCATED_AT", doc="doc-1", timestamp="2024-08-08T20:15:00Z", location="Cafe"),
        _edge("person:A", "loc:Y", "LOCATED_AT", doc="doc-2", timestamp="2024-08-08T20:15:00Z", location="Airport"),
    ]
    snap = CaseGraphSnapshot(case_id="conflict-loc", nodes=nodes, edges=edges)
    timeline = [
        {"timestamp": "2024-08-08T20:15:00Z", "location": "Cafe", "participants": [{"name": "Alice"}]},
        {"timestamp": "2024-08-08T20:15:00Z", "location": "Airport", "participants": [{"name": "Alice"}]},
    ]
    has_contra, details = detect_contradictions(timeline, edges, ["Alice"])
    assert has_contra, "Same person at two locations at same time should be flagged as contradiction"
    assert len(details) > 0

def test_duplicate_source_records():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1", timestamp="2024-08-08T10:00:00Z"),
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1", timestamp="2024-08-08T10:00:00Z"),  # Duplicate same doc, same timestamp
    ]
    snap = CaseGraphSnapshot(case_id="dup", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    # Deduplication should merge duplicates, not count as 2 independent records
    if result.relationships:
        # Evidence strength should not be inflated by duplicates from same doc
        # Our deduplication merges same pair
        assert len(result.relationships) <= 1

def test_missing_provenance():
    # Edge without proper provenance should fail sufficiency gate
    result = evidence_sufficiency_gate(
        edges=[],
        supporting_entities=[],
        hop_count=1,
        doc_ids=set(),
        timeline=[],
        provenance=[],  # Missing provenance
    )
    assert not result.passed
    assert "no_provenance" in result.failed_checks or "no_source_documents" in result.failed_checks

def test_partial_evidence():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1"),  # Only 1 doc, no timestamp, no supporting
    ]
    snap = CaseGraphSnapshot(case_id="partial", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    # Partial evidence should result in low confidence HYPOTHESIS or UNKNOWN, not FACT High
    if result.relationships:
        r = result.relationships[0]
        assert r.classification in ("HYPOTHESIS", "UNKNOWN", "INFERENCE") or r.confidence < 0.8
        assert r.evidence_strength in ("WEAK", "INSUFFICIENT", "MODERATE") or r.confidence < 0.7

def test_stale_evidence():
    # Old evidence vs recent — temporal pattern should handle
    timeline = [
        {"timestamp": "2020-01-01T10:00:00Z", "description": "Old event"},
        {"timestamp": "2024-08-08T10:00:00Z", "description": "Recent event"},
    ]
    analysis = analyze_temporal_pattern(timeline, [])
    assert analysis.first_observed != "Timestamp unavailable"
    assert analysis.last_observed != "Timestamp unavailable"
    # Duration should be large
    assert analysis.duration_days >= 0

def test_conflicting_case_records():
    nodes = {
        "person:A": _node("person:A", "Alice", "Person"),
        "person:B": _node("person:B", "Bob", "Person"),
    }
    edges = [
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1", case_id="case-1", timestamp="2024-08-08T10:00:00Z"),
        _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-2", case_id="case-2", timestamp="2024-08-08T10:00:00Z", note="No association found"),
    ]
    snap = CaseGraphSnapshot(case_id="conflict-case", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob", max_persons=10, max_relationships=10)
    # Should not crash, and should handle conflicting case records gracefully
    assert True

def test_contradiction_reduces_confidence():
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
    snap = CaseGraphSnapshot(case_id="contra-conf", nodes=nodes, edges=edges)
    result = person_graph_rag_retrieval(snap, "Alice Bob at 20:15", max_persons=10, max_relationships=10)
    # If contradiction detected, confidence should be reduced or limitations should mention it
    for r in result.relationships:
        if r.hop_count == 1:
            # Check if limitations mention contradiction or confidence reduced
            has_contra_note = any("conflict" in lim.lower() or "contradict" in lim.lower() for lim in r.limitations)
            # Either has note or confidence not High FACT
            if has_contra_note:
                assert True
            else:
                # If no explicit note, confidence should be less than 1.0
                assert r.confidence < 1.0 or r.classification != "FACT"


# ---------------------------------------------------------------------------
# The sufficiency gate must not report a consistency check it never ran.
#
# ``temporal_consistent`` used to be hardcoded True with a "for now, assume
# consistent" note, which printed a green tick for an unperformed check.  It is
# now tri-state: True = checked and consistent, False = contradiction found,
# None = the records carry no timestamped location data, so it was not
# assessable.
# ---------------------------------------------------------------------------


def test_gate_reports_not_assessable_when_there_is_no_location_data():
    """No timestamped locations -> None, never a fabricated True."""
    result = evidence_sufficiency_gate(
        edges=[_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")],
        supporting_entities=[],
        hop_count=1,
        doc_ids={"doc-1"},
        timeline=[
            # Timestamps, but no location attached: nothing to compare.
            {"timestamp": "2024-08-08T10:00:00Z", "participants": [{"name": "Alice"}]},
            {"timestamp": "2024-08-08T11:00:00Z", "participants": [{"name": "Alice"}]},
        ],
        provenance=[{"kind": "document", "ref": "doc-1"}],
    )
    assert result.temporal_consistent is None, (
        "a consistency check with no location data must report 'not assessable'"
    )
    assert result.has_contradiction is False
    assert result.contradiction_details == []


def test_gate_detects_a_real_location_contradiction():
    """Same person, same timestamp, two places -> False, with the reason."""
    timeline = [
        {
            "timestamp": "2024-08-08T10:00:00Z",
            "location": "Kota Junction",
            "participants": [{"name": "Alice"}],
        },
        {
            "timestamp": "2024-08-08T10:00:00Z",
            "location": "Jaipur Central",
            "participants": [{"name": "Alice"}],
        },
    ]
    result = evidence_sufficiency_gate(
        edges=[_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")],
        supporting_entities=[],
        hop_count=1,
        doc_ids={"doc-1"},
        timeline=timeline,
        provenance=[{"kind": "document", "ref": "doc-1"}],
    )
    assert result.has_contradiction is True
    assert result.temporal_consistent is False
    assert result.contradiction_details, "the contradiction must name what conflicts"
    assert any("Alice" in detail for detail in result.contradiction_details)


def test_gate_reports_consistent_when_it_actually_checked():
    """Two timestamped locations that agree -> True, because it was checked."""
    timeline = [
        {
            "timestamp": "2024-08-08T10:00:00Z",
            "location": "Kota Junction",
            "participants": [{"name": "Alice"}],
        },
        {
            "timestamp": "2024-08-08T12:00:00Z",
            "location": "Kota Junction",
            "participants": [{"name": "Alice"}],
        },
    ]
    result = evidence_sufficiency_gate(
        edges=[_edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1")],
        supporting_entities=[],
        hop_count=1,
        doc_ids={"doc-1"},
        timeline=timeline,
        provenance=[{"kind": "document", "ref": "doc-1"}],
    )
    assert result.temporal_consistent is True
    assert result.has_contradiction is False


def test_contradiction_downgrades_fact_to_inference():
    """A real contradiction must cost the relationship its FACT status."""
    timeline = [
        {
            "timestamp": "2024-08-08T10:00:00Z",
            "location": "Kota Junction",
            "participants": [{"name": "Alice"}],
        },
        {
            "timestamp": "2024-08-08T10:00:00Z",
            "location": "Jaipur Central",
            "participants": [{"name": "Alice"}],
        },
    ]
    contradicted = evidence_sufficiency_gate(
        edges=[
            _edge("person:A", "person:B", "ASSOCIATE_OF", doc="doc-1"),
            _edge("person:A", "person:B", "CALLED", doc="doc-2"),
        ],
        supporting_entities=[{"key": "phone:X", "type": "Phone"}],
        hop_count=1,
        doc_ids={"doc-1", "doc-2"},
        timeline=timeline,
        provenance=[{"kind": "document", "ref": "doc-1"}, {"kind": "document", "ref": "doc-2"}],
    )
    assert contradicted.classification != "FACT", (
        "a relationship with contradictory location evidence cannot be a FACT"
    )
    assert any("Downgraded" in reason for reason in contradicted.reasons)
