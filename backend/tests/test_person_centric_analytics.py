"""Person-centric analytics: supporting entities are evidence, never the subject.

The reported defect was a People Network analytical finding that read::

    F001 +919000000000 appears in 9 cases
    CROSS CASE ENTITY
    Why: +919000000000 appears in 9 cases was surfaced because elevated
    betweenness centrality (0.00), cross-case presence in 9 case(s), ...

A phone number is not an investigative subject.  It is the *reason* two people
are connected, and belongs in the supporting basis of a PERSON ↔ PERSON
finding.  These tests pin that rule, and the companion rule that the "why" text
may never call a zero metric "elevated".

The entity-level reading is not deleted: the MASTER scope is the deep
evidence/technical graph where entity structure genuinely is the point, and it
keeps those rows — labelled as statements about the evidence graph.
"""

from __future__ import annotations

import pytest

from app.analytics.centrality import compute_centrality
from app.domain.models import CaseGraphSnapshot, GraphEdge, GraphNode
from app.investigator.network_analysis import _cross_case, _top_metric
from app.investigator.patterns import (
    DetectorContext,
    _is_person,
    _persons_linked_through,
    detect_all_patterns,
    detect_cross_case_entities,
)


def _node_name_for(snapshot: CaseGraphSnapshot, key: str) -> str:
    node = snapshot.nodes.get(key)
    return (node.properties or {}).get("name") or key if node else key


def _node(key: str, label: str, name: str, cases: list[str], **extra) -> GraphNode:
    node = GraphNode(provenance_key=key, label=label)
    node.properties = {"name": name, "case_ids": list(cases), **extra}
    return node


def _edge(
    source: str, target: str, rel: str, doc: str = "doc-auto", **extra
) -> GraphEdge:
    """An edge always cites a record: the domain refuses an unevidenced write."""
    return GraphEdge(
        source_key=source,
        target_key=target,
        rel_type=rel,
        properties={"source_doc_id": doc, **extra},
    )


@pytest.fixture
def shared_hub_graph() -> CaseGraphSnapshot:
    """A phone and a bank account, each bridging two people across cases."""
    cases = [f"CASE-{i}" for i in range(1, 10)]
    snapshot = CaseGraphSnapshot(case_id="scope")
    snapshot.nodes.update(
        {
            "PHONE1": _node("PHONE1", "Phone", "+919000000000", cases),
            "ACC1": _node("ACC1", "BankAccount", "0000", cases[:7]),
            "P1": _node("P1", "Person", "Priya Kumar", cases[:5]),
            "P2": _node("P2", "Person", "Dinesh Malhotra", cases[4:]),
            "P3": _node("P3", "Person", "Amit Sharma", cases[:4]),
            "P4": _node("P4", "Person", "Vikram Verma", cases[3:8]),
        }
    )
    snapshot.edges = [
        _edge("P1", "PHONE1", "USES_PHONE", "doc-1"),
        _edge("P2", "PHONE1", "USES_PHONE", "doc-2"),
        _edge("P3", "ACC1", "OWNS_ACCOUNT", "doc-3"),
        _edge("P4", "ACC1", "OWNS_ACCOUNT", "doc-4"),
    ]
    return snapshot


# ---------------------------------------------------------------------------
# 1. The reported defect: a phone surfaced as the analytical entity
# ---------------------------------------------------------------------------


def test_phone_is_never_the_subject_of_a_person_centric_finding(shared_hub_graph) -> None:
    patterns = detect_cross_case_entities(
        DetectorContext(snapshot=shared_hub_graph, subject="PERSON")
    )
    subjects = {name for pattern in patterns for name in pattern.entities}

    assert "+919000000000" not in subjects, "a phone number is not an investigative subject"
    assert "0000" not in subjects, "a bank account is not an investigative subject"

    for pattern in patterns:
        for key in pattern.entity_keys:
            assert _is_person(shared_hub_graph.nodes.get(key)), (
                f"{pattern.kind} named non-person {key} as its subject"
            )


def test_shared_phone_becomes_a_person_to_person_finding(shared_hub_graph) -> None:
    patterns = detect_cross_case_entities(
        DetectorContext(snapshot=shared_hub_graph, subject="PERSON")
    )
    links = [p for p in patterns if p.kind == "CROSS_CASE_PERSON_LINK"]

    by_pair = {tuple(sorted(p.entities)): p for p in links}
    assert ("Dinesh Malhotra", "Priya Kumar") in by_pair
    assert ("Amit Sharma", "Vikram Verma") in by_pair

    phone_link = by_pair[("Dinesh Malhotra", "Priya Kumar")]
    # The phone is demoted to the supporting basis — the reason, not the subject.
    assert "+919000000000" in phone_link.title
    assert phone_link.entities == ["Dinesh Malhotra", "Priya Kumar"]
    assert len(phone_link.cases) >= 2, "the pair must actually span case boundaries"
    assert phone_link.evidence, "the finding must cite the records that establish it"
    joined = " ".join(item.summary for item in phone_link.evidence)
    assert "+919000000000" in joined
    assert "USES_PHONE" in joined


def test_shared_account_becomes_a_financial_person_link(shared_hub_graph) -> None:
    patterns = detect_cross_case_entities(
        DetectorContext(snapshot=shared_hub_graph, subject="PERSON")
    )
    links = [p for p in patterns if p.kind == "CROSS_CASE_PERSON_LINK"]
    # Select by the entities it names, not by substring: the phone
    # "+919000000000" also contains "0000".
    account_link = next(
        p for p in links if sorted(p.entities) == ["Amit Sharma", "Vikram Verma"]
    )

    assert account_link.entity_keys and "ACC1" not in account_link.entity_keys
    assert "bank account" in account_link.explanation.lower()
    assert "0000" in account_link.title


def test_person_spanning_cases_is_still_reported_as_that_person(shared_hub_graph) -> None:
    patterns = detect_cross_case_entities(
        DetectorContext(snapshot=shared_hub_graph, subject="PERSON")
    )
    people = {
        pattern.entities[0]
        for pattern in patterns
        if pattern.kind == "CROSS_CASE_ENTITY"
    }
    assert "Priya Kumar" in people
    assert "Dinesh Malhotra" in people


def test_entity_scope_keeps_the_entity_level_reading(shared_hub_graph) -> None:
    """MASTER is the deep evidence graph; there the entity row is the point."""
    patterns = detect_cross_case_entities(
        DetectorContext(snapshot=shared_hub_graph, subject="ENTITY")
    )
    titles = {pattern.title for pattern in patterns}
    assert "+919000000000 appears in 9 cases" in titles
    assert "0000 appears in 7 cases" in titles
    # ...and no person↔person translation is emitted in that scope.
    assert not any(p.kind == "CROSS_CASE_PERSON_LINK" for p in patterns)


# ---------------------------------------------------------------------------
# 2. "Why was this surfaced?" must not overstate the numbers
# ---------------------------------------------------------------------------


def test_zero_betweenness_is_never_called_elevated(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=shared_hub_graph, centrality=centrality, subject="PERSON"
        )
    )
    live = [p for p in patterns if not p.excluded and p.why]
    assert live, "the fixture must produce at least one live pattern"

    for pattern in live:
        assert "elevated" not in pattern.why, (
            f"unsupported adjective in why-text: {pattern.why}"
        )
        if "betweenness centrality 0.00" in pattern.why:
            assert "not a structural bridge" in pattern.why


def test_why_text_names_people_in_person_centric_scope(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=shared_hub_graph, centrality=centrality, subject="PERSON"
        )
    )
    cross_case = [
        p for p in patterns if p.kind == "CROSS_CASE_ENTITY" and not p.excluded
    ]
    assert cross_case
    for pattern in cross_case:
        assert "+919000000000" not in pattern.title
        assert "0000" not in pattern.title


# ---------------------------------------------------------------------------
# 3. Community signals name people, not the entity holding them together
# ---------------------------------------------------------------------------


def test_community_signal_names_people_not_the_shared_entity(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=shared_hub_graph, centrality=centrality, subject="PERSON"
        )
    )
    communities = [p for p in patterns if p.kind == "COMMUNITY_SIGNAL" and not p.excluded]
    assert communities, "the fixture must produce at least one community signal"
    for pattern in communities:
        # The subject must be the people themselves.  The reframing layer names
        # them directly (e.g. "Priya Kumar ↔ Dinesh Malhotra"), which is
        # stronger than the earlier "Group of N people" wording -- so assert the
        # intent: a real person's name appears, and no supporting entity does.
        assert any(
            _node_name_for(shared_hub_graph, key) in pattern.title
            for key in pattern.entity_keys
        ), pattern.title
        assert "+919000000000" not in pattern.title
        assert "0000" not in pattern.title
        for key in pattern.entity_keys:
            assert _is_person(shared_hub_graph.nodes.get(key))


# ---------------------------------------------------------------------------
# 4. The traversal helper: how a person is attached to a supporting entity
# ---------------------------------------------------------------------------


def test_persons_linked_through_finds_both_sides_of_a_shared_entity(shared_hub_graph) -> None:
    linked = _persons_linked_through(shared_hub_graph, "PHONE1")
    assert set(linked) == {"P1", "P2"}
    for key, steps in linked.items():
        assert steps, f"{key} must record how it attaches to the entity"
        assert any(rel == "USES_PHONE" for rel, _ in steps)


def test_persons_linked_through_walks_two_hops(shared_hub_graph) -> None:
    """Person → PHONE → PHONE → Person (a recorded call between two numbers)."""
    snapshot = CaseGraphSnapshot(case_id="scope")
    snapshot.nodes.update(
        {
            "PA": _node("PA", "Person", "Person A", ["CASE-1"]),
            "PB": _node("PB", "Person", "Person B", ["CASE-2"]),
            "NA": _node("NA", "Phone", "+911", ["CASE-1"]),
            "NB": _node("NB", "Phone", "+912", ["CASE-2"]),
        }
    )
    snapshot.edges = [
        _edge("PA", "NA", "USES_PHONE", "doc-a"),
        _edge("NA", "NB", "CALLED", "doc-call"),
        _edge("PB", "NB", "USES_PHONE", "doc-b"),
    ]
    linked = _persons_linked_through(snapshot, "NA", max_hops=2)
    assert set(linked) == {"PA", "PB"}


def test_persons_linked_through_does_not_walk_through_people(shared_hub_graph) -> None:
    """A person is a dead end; reaching others is the relationship graph's job."""
    snapshot = CaseGraphSnapshot(case_id="scope")
    snapshot.nodes.update(
        {
            "P1": _node("P1", "Person", "Person 1", ["CASE-1"]),
            "P2": _node("P2", "Person", "Person 2", ["CASE-1"]),
            "P3": _node("P3", "Person", "Person 3", ["CASE-1"]),
        }
    )
    snapshot.edges = [
        _edge("P1", "P2", "ASSOCIATE_OF", "doc-1"),
        _edge("P2", "P3", "ASSOCIATE_OF", "doc-2"),
    ]
    assert _persons_linked_through(snapshot, "P1") == {"P2": [("ASSOCIATE_OF", "Person 1")]}


# ---------------------------------------------------------------------------
# 5. Ranked metrics are person-only in person-centric scope
# ---------------------------------------------------------------------------


def test_top_metric_filters_to_people_when_person_centric(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    for metric in ("degree", "betweenness", "pagerank", "weighted_degree"):
        rows = _top_metric(centrality, shared_hub_graph, metric, 15, "PERSON")
        for row in rows:
            assert row["label"] == "PERSON", f"{metric} surfaced {row}"


def test_top_metric_keeps_entities_in_entity_scope(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    rows = _top_metric(centrality, shared_hub_graph, "degree", 15, "ENTITY")
    labels = {row["label"] for row in rows}
    assert "PHONE" in labels or "BANK_ACCOUNT" in labels, (
        "the entity deep-dive must still rank its entities"
    )


def test_cross_case_list_is_person_only_when_person_centric(shared_hub_graph) -> None:
    centrality = compute_centrality(shared_hub_graph)
    rows = _cross_case(shared_hub_graph, centrality, 20, "PERSON")
    assert rows
    for row in rows:
        assert row["label"] == "PERSON", row

    entity_rows = _cross_case(shared_hub_graph, centrality, 20, "ENTITY")
    assert {"PHONE", "BANK_ACCOUNT"} & {row["label"] for row in entity_rows}


def test_subject_defaults_to_person(shared_hub_graph) -> None:
    """The default must be the safe one: person-centric."""
    assert DetectorContext(snapshot=shared_hub_graph).subject == "PERSON"
    patterns = detect_cross_case_entities(DetectorContext(snapshot=shared_hub_graph))
    for pattern in patterns:
        for key in pattern.entity_keys:
            assert _is_person(shared_hub_graph.nodes.get(key))


# ---------------------------------------------------------------------------
# 6. Every detector, not just the two that happened to fire on the demo data.
#
# The first person-centric fix covered cross-case entities and communities.
# Seven more detectors could still put a supporting entity in the subject
# position: communication anomalies titled phone↔phone pairs, financial flows
# titled the account, bridge signals ranked by betweenness over every node,
# cross-case links, temporal bursts and repeated combinations over arbitrary
# edge endpoints.  A single reframing layer now covers all of them.
# ---------------------------------------------------------------------------


@pytest.fixture
def leaking_detector_graph() -> CaseGraphSnapshot:
    """A snapshot built specifically to fire the detectors that leaked."""
    snapshot = CaseGraphSnapshot(case_id="scope")
    snapshot.nodes.update(
        {
            "P1": _node("P1", "Person", "Priya Kumar", ["C1"]),
            "P2": _node("P2", "Person", "Dinesh Malhotra", ["C2"]),
            "P3": _node("P3", "Person", "Amit Sharma", ["C1"]),
            "P4": _node("P4", "Person", "Vikram Verma", ["C3"]),
            "NA": _node("NA", "Phone", "+919000000000", ["C1"]),
            "NB": _node("NB", "Phone", "+919000000137", ["C2"]),
            "ACC": _node("ACC", "BankAccount", "ACC-0000", ["C1", "C2", "C3"]),
            "VEH": _node("VEH", "Vehicle", "RJ-14-CX-1234", ["C1"],
                         plate="RJ-14-CX-1234"),
        }
    )
    edges = [
        _edge("P1", "NA", "USES_PHONE", "d1"),
        _edge("P2", "NB", "USES_PHONE", "d2"),
        _edge("P4", "ACC", "OWNS_ACCOUNT", "d-own"),
        _edge("P1", "VEH", "ASSOCIATE_OF", "d-v1"),
        _edge("P3", "VEH", "OWNS_VEHICLE", "d-v2"),
        _edge("NA", "NB", "CALLED", "d-span"),
    ]
    # 12 call records between the two numbers -> COMMUNICATION_ANOMALY, whose
    # raw subject is the phone pair.
    edges += [
        _edge("NA", "NB", "CALLED", f"d-call-{i}", count=3) for i in range(12)
    ]
    # 6 dated transfers touching one account -> FINANCIAL_FLOW on the account.
    edges += [
        _edge("P3", "ACC", "TRANSFER_TO", f"d-tx-{i}",
              timestamp=f"2024-08-0{i + 1}T10:00:00Z")
        for i in range(6)
    ]
    snapshot.edges = edges
    return snapshot


def test_no_detector_puts_a_supporting_entity_in_the_subject_position(
    leaking_detector_graph,
) -> None:
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="PERSON"
        )
    )
    live = [p for p in patterns if not p.excluded]
    assert live, "the fixture must fire at least one detector"

    for pattern in live:
        people = [
            key for key in (pattern.entity_keys or [])
            if _is_person(leaking_detector_graph.nodes.get(key))
        ]
        assert people, (
            f"{pattern.kind} named no person as its subject: {pattern.title!r}"
        )
        for name in pattern.entities:
            assert name not in {"+919000000000", "+919000000137", "ACC-0000",
                                "RJ-14-CX-1234"}, (
                f"{pattern.kind} surfaced a supporting entity as a subject: {name}"
            )


def test_the_leaking_kinds_are_all_covered(leaking_detector_graph) -> None:
    """Name the detectors that used to leak, so a regression is attributable."""
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="PERSON"
        )
    )
    kinds = {p.kind for p in patterns if not p.excluded}
    for expected in (
        "COMMUNICATION_ANOMALY",   # was "+919000000000 ↔ +919000000137: 37 calls"
        "FINANCIAL_FLOW",          # was "ACC-0000: 6 transfers in 7 days"
        "CROSS_CASE_LINK",         # was phone ↔ phone spanning cases
        "REPEATED_COMBINATION",    # was phone ↔ phone across documents
    ):
        assert expected in kinds, f"{expected} did not fire; fixture needs updating"


def test_entity_scope_is_untouched_by_the_reframing(leaking_detector_graph) -> None:
    """The deep evidence view keeps its entity-level findings verbatim."""
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="ENTITY"
        )
    )
    titles = {p.title for p in patterns if not p.excluded}
    assert any("+919000000000" in t for t in titles)
    assert any("ACC-0000" in t for t in titles)


def test_repeated_findings_about_one_pair_are_collapsed(leaking_detector_graph) -> None:
    """13 parallel call records must not produce 13 identical findings."""
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="PERSON"
        )
    )
    live = [p for p in patterns if not p.excluded]
    identities = [(p.kind, tuple(sorted(p.entity_keys or []))) for p in live]
    assert len(identities) == len(set(identities)), (
        f"duplicated person-subject findings: {identities}"
    )
    collapsed = [p for p in live if "collapsed" in p.title]
    assert collapsed, "the 13 parallel call records should collapse into one finding"
    for pattern in collapsed:
        # The suffix is applied once, from the final count.
        assert pattern.title.count("collapsed") == 1, pattern.title


def test_a_finding_with_no_person_is_set_aside_with_a_reason(
    leaking_detector_graph,
) -> None:
    """Never silently dropped, never surfaced with an entity in front."""
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="PERSON"
        )
    )
    aside = [p for p in patterns if p.excluded]
    for pattern in aside:
        assert pattern.exclusion_reason, (
            f"{pattern.kind} was set aside without saying why"
        )


def test_reframing_keeps_the_original_evidence(leaking_detector_graph) -> None:
    """Reframing changes the subject, not what was actually found."""
    centrality = compute_centrality(leaking_detector_graph)
    patterns = detect_all_patterns(
        DetectorContext(
            snapshot=leaking_detector_graph, centrality=centrality, subject="PERSON"
        )
    )
    comms = next(
        p for p in patterns
        if p.kind == "COMMUNICATION_ANOMALY" and not p.excluded
    )
    joined = " ".join(item.summary for item in comms.evidence)
    # The call volume survives, and the phones are named as the mechanism.
    assert "calls" in joined.lower()
    assert "+919000000000" in joined or "+919000000137" in joined
    assert any("reframe" in (ptr.ref or "") for item in comms.evidence
               for ptr in (item.provenance or [])), (
        "the reframing must be disclosed in provenance, not applied silently"
    )
