"""Investigator-grade explainability — targeted regression tests.

Pins the final-explainability guarantees that answer "why did CrimeLink
surface this finding?":

1. entity-type safety: incompatible canonical types never merge;
2. every live pattern carries the analytical basis / WHY / relevance /
   evidence strength it was surfaced by — only metrics actually computed;
3. relationship strength and evidence confidence stay two distinct
   dimensions (a pair can show WEAK contact on INSUFFICIENT evidence);
4. silent-intermediary detection is deterministic and explainable, and never
   drifts into "silent criminal" language.

No test here calls a live model or the network.
"""

from __future__ import annotations

import pytest

from app.analytics.centrality import compute_centrality
from app.domain.models import CaseGraphSnapshot, GraphEdge, GraphNode
from app.investigator.patterns import DetectorContext, detect_all_patterns
from app.investigator.relationships import discover_relationships
from app.investigator.schemas import ResolvedEntity
from app.investigator.silent_intermediary import detect_silent_intermediaries

GUILT_TERMS = ("silent criminal", "mastermind", "kingpin", "ringleader", "culprit")


def _node(key, name, *, label="Person", cases=("c1",), **props):
    properties = {
        "name": name,
        "case_ids": list(cases),
        "confidence": 1.0,
        "source_doc_id": "d1",
    }
    properties.update(props)
    return GraphNode(provenance_key=key, label=label, properties=properties)


def _edge(first, second, rel, *, doc="d1", **props):
    properties = {"source_doc_id": doc, "confidence": props.pop("confidence", 0.9)}
    properties.update(props)
    return GraphEdge(
        source_key=first,
        target_key=second,
        rel_type=rel,
        properties=properties,
        key=props.get("key") or f"{first}|{second}|{rel}|{doc}",
    )


def _doc_index():
    return {
        "d1": {
            "filename": "cdr.csv",
            "document_type": "CDR",
            "source_confidence": "UNVERIFIED",
            "content_hash": "ab" * 32,
        },
        "d2": {
            "filename": "fin.csv",
            "document_type": "FINANCIAL",
            "source_confidence": "UNVERIFIED",
            "content_hash": "cd" * 32,
        },
    }


# --------------------------------------------------------------------------- #
# 1. Entity-type safety: incompatible types never merge
# --------------------------------------------------------------------------- #


async def test_incompatible_entity_types_never_merge(db, container):
    """A PERSON and a LOCATION must be refused, not collapsed into one node."""
    container.injector.inject_nodes(
        [
            _node("p1", "Ravi Mehta"),
            _node("l1", "New Delhi", label="Location"),
        ]
    )
    with pytest.raises(ValueError, match="different types"):
        container.graph_store.merge_persons("p1", "l1", "actor-1")
    # The refusal leaves both records intact and active.
    person = container.graph_store.get_node("p1")
    location = container.graph_store.get_node("l1")
    assert person is not None and person.label == "Person"
    assert location is not None and location.label == "Location"


async def test_same_type_persons_still_merge(db, container):
    """The guard must not block legitimate same-type merges."""
    container.injector.inject_nodes(
        [
            _node("pa", "Asha Nair"),
            _node("pb", "Asha Nair"),
        ]
    )
    container.injector.inject_edges(
        [_edge("pa", "pb", "ASSOCIATE_OF", doc="d1")]
    )
    result = container.graph_store.merge_persons("pa", "pb", "actor-1")
    assert result.kept_key == "pa" and result.absorbed_key == "pb"


# --------------------------------------------------------------------------- #
# 2. Analytical basis / WHY on every live pattern
# --------------------------------------------------------------------------- #


async def test_live_patterns_carry_analytical_basis_and_why():
    snapshot = CaseGraphSnapshot(
        case_id="",
        nodes={
            "hub": _node("hub", "Hub Person"),
            **{f"leaf{i}": _node(f"leaf{i}", f"Leaf {i}") for i in range(1, 5)},
        },
        edges=[_edge("hub", f"leaf{i}", "ASSOCIATE_OF", doc="d1") for i in range(1, 5)],
    )
    centrality = compute_centrality(snapshot)
    patterns = detect_all_patterns(
        DetectorContext(snapshot=snapshot, doc_index=_doc_index(), centrality=centrality),
        max_patterns=25,
        include_excluded=True,
    )
    bridge = next((p for p in patterns if p.kind == "NETWORK_BRIDGE"), None)
    assert bridge is not None, "a star hub must fire the NETWORK_BRIDGE detector"
    assert not bridge.excluded
    # WHY, deterministic, and free of guilt vocabulary.
    assert bridge.why and "surfaced because" in bridge.why
    assert not any(term in bridge.why.lower() for term in GUILT_TERMS)
    # Analytical basis: only metrics actually computed, with interpretations.
    assert bridge.analytical_basis is not None
    assert bridge.analytical_basis.betweenness_centrality is not None
    assert bridge.analytical_basis.explanations.get("betweenness_centrality")
    # Relevance and evidence strength are present and explainable.
    assert bridge.investigative_relevance is not None
    assert bridge.investigative_relevance.explanation
    assert bridge.evidence_strength is not None
    assert bridge.evidence_strength.explanation


async def test_set_aside_patterns_are_not_enriched():
    """Examined-and-dropped entries answer 'set aside', never 'surfaced'."""
    snapshot = CaseGraphSnapshot(
        case_id="",
        nodes={"a": _node("a", "Anna"), "b": _node("b", "Ben")},
        edges=[_edge("a", "b", "LINKED_ON_SOCIAL", doc="d1", confidence=0.3)],
    )
    centrality = compute_centrality(snapshot)
    patterns = detect_all_patterns(
        DetectorContext(snapshot=snapshot, doc_index=_doc_index(), centrality=centrality),
        max_patterns=25,
        include_excluded=True,
    )
    set_aside = [p for p in patterns if p.excluded]
    assert set_aside, "the social-only link must be set aside openly"
    for pattern in set_aside:
        assert pattern.why is None
        assert pattern.analytical_basis is None


# --------------------------------------------------------------------------- #
# 3. Relationship strength vs evidence confidence
# --------------------------------------------------------------------------- #


async def test_relationship_strength_distinct_from_evidence_confidence():
    snapshot = CaseGraphSnapshot(
        case_id="",
        nodes={"p1": _node("p1", "Ravi"), "p2": _node("p2", "Sana")},
        edges=[_edge("p1", "p2", "LINKED_ON_SOCIAL", doc="d1", confidence=0.3)],
    )
    resolved = [
        ResolvedEntity(canonical_id="p1", label="Person", display_name="Ravi"),
        ResolvedEntity(canonical_id="p2", label="Person", display_name="Sana"),
    ]
    findings = discover_relationships(snapshot, resolved, doc_index=_doc_index())
    direct = next((f for f in findings if f.kind == "direct"), None)
    assert direct is not None
    # Two dimensions, deliberately not collapsed:
    assert direct.relationship_strength == "WEAK"        # one observed record
    assert direct.evidence_strength == "INSUFFICIENT"    # confidence below 0.40
    assert direct.why and "directly link" in direct.why


async def test_well_evidenced_pair_reports_high_confidence():
    snapshot = CaseGraphSnapshot(
        case_id="",
        nodes={"p1": _node("p1", "Ravi"), "p2": _node("p2", "Sana")},
        edges=[
            _edge("p1", "p2", "CALLED", doc="d1", confidence=0.9),
            _edge("p1", "p2", "TRANSFER_TO", doc="d2", confidence=0.95),
        ],
    )
    resolved = [
        ResolvedEntity(canonical_id="p1", label="Person", display_name="Ravi"),
        ResolvedEntity(canonical_id="p2", label="Person", display_name="Sana"),
    ]
    findings = discover_relationships(snapshot, resolved, doc_index=_doc_index())
    direct = next((f for f in findings if f.kind == "direct"), None)
    assert direct is not None
    assert direct.relationship_strength in {"MODERATE", "STRONG"}
    assert direct.evidence_strength == "HIGH"


# --------------------------------------------------------------------------- #
# 4. Silent intermediary — deterministic and explainable
# --------------------------------------------------------------------------- #


async def test_silent_intermediary_is_explainable_and_neutral():
    # Two dense clusters joined only by a low-degree bridge person.
    snapshot = CaseGraphSnapshot(
        case_id="",
        nodes={
            "m": _node("m", "Mediator"),
            **{f"a{i}": _node(f"a{i}", f"Cluster A {i}") for i in range(1, 4)},
            **{f"b{i}": _node(f"b{i}", f"Cluster B {i}") for i in range(1, 4)},
        },
        edges=[
            _edge("m", "a1", "ASSOCIATE_OF", doc="d1"),
            _edge("m", "b1", "ASSOCIATE_OF", doc="d1"),
            _edge("a1", "a2", "ASSOCIATE_OF", doc="d1"),
            _edge("a2", "a3", "ASSOCIATE_OF", doc="d1"),
            _edge("a1", "a3", "ASSOCIATE_OF", doc="d1"),
            _edge("b1", "b2", "ASSOCIATE_OF", doc="d1"),
            _edge("b2", "b3", "ASSOCIATE_OF", doc="d1"),
            _edge("b1", "b3", "ASSOCIATE_OF", doc="d1"),
        ],
    )
    centrality = compute_centrality(snapshot)
    findings = detect_silent_intermediaries(
        snapshot, centrality, doc_index=_doc_index(), limit=5
    )
    assert findings, "a bridge person between two clusters must be surfaced"
    finding = findings[0]
    assert finding.display_name == "Mediator"
    assert finding.why_surfaced, "the finding must explain why it was surfaced"
    assert "betweenness" in finding.why_surfaced.lower()
    assert finding.disclaimer, "the finding must carry the interpretation boundary"
    assert not any(term in finding.why_surfaced.lower() for term in GUILT_TERMS)
    # Low degree, structural importance: the analytical basis records both.
    assert finding.analytical_basis.betweenness_centrality is not None
