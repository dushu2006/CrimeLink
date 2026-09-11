"""Investigator reasoning layer: deterministic behavior pinned by tests.

Part 1 pins pure logic over an in-memory snapshot: resolution, every
detector family (including negatives), relationships, hypotheses with
contradiction search, gaps, next steps, labels, language guards, and
memory bounds. Part 2 runs the real HTTP endpoints against imported
datasets: honesty, memory continuation, isolation across datasets with
reused names, provenance chain-link verification, decoy exclusion, and
criminal-safety scans. No test calls a live model or the network.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.analytics.centrality import compute_centrality
from app.analytics.findings import Finding
from app.audit.service import _row_payload
from app.db.base import new_uuid
from app.db.models import AuditLog, Case, Dataset, DetectedPattern, EntityResolutionItem
from app.db.session import async_session
from app.datasets.pipeline import ImportOptions, run_import
from app.domain.enums import MatchBasis, PatternStatus, PatternType, ResolutionStatus
from app.domain.models import CaseGraphSnapshot, GraphEdge, GraphNode
from app.domain.provenance import GENESIS_HASH, canonical_json, chain_hash
from app.investigator import entity_resolution as ER
from app.investigator import evidence as EV
from app.investigator import gaps as G
from app.investigator import hypotheses as H
from app.investigator import labels as L
from app.investigator import memory as M
from app.investigator import next_steps as NS
from app.investigator import patterns as P
from app.investigator import prompts as PR
from app.investigator import relationships as R
from app.investigator.patterns import DetectorContext
from app.investigator.schemas import (
    EvidenceItem,
    Hypothesis,
    ObservationBlock,
    RelationshipFinding,
    ResolvedEntity,
)

JURISDICTION = "RJ-JAIPUR"

GUILT_TERMS = (
    "mastermind",
    "kingpin",
    "ringleader",
    "culprit",
    "perpetrator",
    "offender",
    "criminal network",
    "crime syndicate",
)

COERCIVE_TERMS = ("arrest", "detain", "detention", "interrogat", "raid", "seize", "seizure")


def _node(key, name, *, label="Person", cases=("c1",), **props):
    properties = {"name": name, "case_ids": list(cases)}
    properties.update(props)
    return GraphNode(provenance_key=key, label=label, properties=properties)


def _edge(first, second, rel, *, doc="d1", key=None, **props):
    properties = {"source_doc_id": doc}
    properties.update(props)
    return GraphEdge(
        source_key=first,
        target_key=second,
        rel_type=rel,
        properties=properties,
        key=key or f"{first}|{second}|{rel}|{doc}|{len(props)}",
    )


@pytest.fixture()
def doc_index():
    return {
        f"d{i}": {
            "filename": f"file{i}.csv",
            "document_type": "CDR",
            "source_confidence": "UNVERIFIED",
            "content_hash": "ab" * 32,
        }
        for i in range(1, 7)
    }


@pytest.fixture()
def rich_snapshot():
    """One snapshot exercising every detector and relationship kind."""
    nodes = {
        "p1": _node("p1", "Ravi Mehta", criminal_status="convicted", phone="9811111111"),
        "p2": _node("p2", "Sana Iyer", phone="9822222222", aliases=["Sana"]),
        "p3": _node("p3", "Asha Nair", cases=("c1", "c2"), phone="9833333333"),
        "p4": _node("p4", "Vikram Rao", cases=("c2",)),
        "p5": _node("p5", "Meera Iyer"),
        "p6": _node("p6", "Arjun Rao"),
        "s1": _node("s1", "Social Sam"),
        "s2": _node("s2", "Social Sue"),
        "p7": _node("p7", "Lone Lalit"),
        "p8": _node("p8", "Lone Leena"),
        "p9": _node("p9", "Old Name"),
        "p10": _node("p10", "Sana Iyer"),
        "a1": _node("a1", "Acme Traders", label="BankAccount"),
        "b1": _node("b1", "Bharat Stores", label="BankAccount"),
        "v1": _node("v1", "RJ14AB1234", label="Vehicle", plate="RJ14AB1234"),
        "l1": _node("l1", "Godown Road", label="Location", lat=26.9124, lon=75.7873),
        "l2": _node("l2", "Godown Cross", label="Location", lat=26.9130, lon=75.7880),
        "l3": _node("l3", "Far Market", label="Location", lat=26.9500, lon=75.8200),
    }
    edges = [
        _edge("p2", "p3", "CALLED", doc="d1", count=12, timestamp="2026-08-10T21:10:00"),
        _edge("p1", "p2", "CALLED", doc="d1", count=3, timestamp="2026-08-11T09:15:00"),
        _edge("p2", "p3", "TRANSFER_TO", doc="d2", timestamp="2026-08-12T10:00:00"),
        _edge("p1", "p4", "CALLED", doc="d1", count=1, timestamp="2026-08-09T08:00:00"),
        _edge("p1", "p4", "ASSOCIATE_OF", doc="d4", timestamp="2026-08-09T09:00:00"),
        _edge("p1", "p4", "ASSOCIATE_OF", doc="d5", timestamp="2026-08-09T10:00:00"),
        _edge("p3", "p4", "CALLED", doc="d1", count=1, timestamp="2026-08-10T09:00:00"),
        _edge("p3", "p4", "CALLED", doc="d1", count=1, timestamp="2026-08-10T20:00:00", key="x1"),
        _edge("p3", "p4", "CALLED", doc="d1", count=1, timestamp="2026-08-11T08:00:00", key="x2"),
        _edge("p2", "v1", "ASSOCIATE_OF", doc="d3"),
        _edge("p4", "v1", "OWNS_VEHICLE", doc="d3"),
        _edge("p1", "l1", "LOCATED_AT", doc="d6", timestamp="2026-08-10T18:00:00"),
        _edge("p2", "l2", "LOCATED_AT", doc="d6", timestamp="2026-08-10T18:20:00", key="y1"),
        _edge("p1", "l1", "LOCATED_AT", doc="d6", timestamp="2026-08-12T18:00:00", key="y2"),
        _edge("p2", "l2", "LOCATED_AT", doc="d6", timestamp="2026-08-12T18:05:00", key="y3"),
        _edge("p7", "l3", "LOCATED_AT", doc="d6", timestamp="2026-08-10T12:00:00"),
        _edge("p8", "l3", "LOCATED_AT", doc="d6", timestamp="2026-08-10T12:05:00", key="y4"),
        _edge("p5", "p6", "RELATIVE_OF", doc="d1"),
        _edge("p5", "p6", "RELATIVE_OF", doc="d2", key="z1"),
        _edge("p5", "p6", "RELATIVE_OF", doc="d4", key="z2"),
        _edge("p6", "p1", "ASSOCIATE_OF", doc="d1"),
        _edge("s1", "s2", "LINKED_ON_SOCIAL", doc="d2"),
        _edge("p9", "p1", "MERGED_INTO", doc="d1"),
        _edge("p2", "p10", "POTENTIAL_ALIAS", doc="d1", similarity=0.82),
    ]
    for day in range(1, 6):
        edges.append(
            _edge(
                "a1",
                "b1",
                "TRANSFER_TO",
                doc="d2",
                timestamp=f"2026-08-{day:02d}T11:00:00",
                key=f"t{day}",
            )
        )
    return CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)


def _ctx(snapshot, doc_index, **overrides):
    params = {"snapshot": snapshot, "doc_index": doc_index}
    params.update(overrides)
    return DetectorContext(**params)


def _resolved(key, name, **overrides):
    params = {"canonical_id": key, "label": "Person", "display_name": name}
    params.update(overrides)
    return ResolvedEntity(**params)


# --------------------------------------------------------------------------- #
# Mentions and resolution
# --------------------------------------------------------------------------- #


def test_extract_mentions_prefers_quoted_then_capitalized():
    mentions = ER.extract_mentions('Is "Ravi Mehta" connected to Sana Iyer at all?')
    assert mentions[0] == "Ravi Mehta"
    assert "Sana Iyer" in mentions


def test_extract_mentions_skips_generic_words():
    assert ER.extract_mentions("Is there any connection at all?") == []


def test_exact_name_resolves_with_full_confidence(rich_snapshot, doc_index):
    entity = ER.resolve_mention("Sana Iyer", rich_snapshot)
    assert (entity.canonical_id, entity.confidence, entity.matched_by) == ("p2", 1.0, "name")
    assert entity.resolved is True


def test_alias_matches_below_exact_name(rich_snapshot, doc_index):
    entity = ER.resolve_mention("Sana", rich_snapshot)
    assert (entity.canonical_id, entity.matched_by) == ("p2", "alias")
    assert entity.confidence == pytest.approx(0.9)


def test_hard_identifier_matches_exactly(rich_snapshot, doc_index):
    entity = ER.resolve_mention("9811111111", rich_snapshot)
    assert (entity.canonical_id, entity.confidence) == ("p1", 1.0)
    assert "phone" in entity.matched_by


def test_merge_chain_is_followed_to_canonical(rich_snapshot, doc_index):
    entity = ER.resolve_mention("Old Name", rich_snapshot)
    assert entity.canonical_id == "p1"
    assert "merge" in (entity.ambiguity_note or "").lower()


def test_open_alias_proposal_is_capped_and_flagged(rich_snapshot, doc_index):
    pending = [ER.PendingAlias(source_key="p2", target_key="p10", note="review queue")]
    entity = ER.resolve_mention("Sana Iyer", rich_snapshot, pending_aliases=pending)
    assert entity.confidence <= ER.ALIAS_PROPOSAL_CAP
    assert "proposal" in (entity.ambiguity_note or "").lower()


def test_unknown_mention_stays_unresolved(rich_snapshot, doc_index):
    entity = ER.resolve_mention("Nobody Nowhere", rich_snapshot)
    assert entity.resolved is False
    assert entity.canonical_id.startswith("unresolved:")
    assert "data gap" in (entity.ambiguity_note or "").lower()


def test_criminal_status_is_echoed_from_the_record(rich_snapshot, doc_index):
    assert ER.resolve_mention("Ravi Mehta", rich_snapshot).criminal_status == "convicted"
    assert ER.resolve_mention("Sana Iyer", rich_snapshot).criminal_status is None


# --------------------------------------------------------------------------- #
# Labels, strength, evidence, prompts
# --------------------------------------------------------------------------- #


def test_strength_counts_map_to_levels():
    assert L.score_strength(independent_sources=3)[0] == "STRONG"
    assert L.score_strength(independent_sources=2)[0] == "MODERATE"
    assert L.score_strength(independent_sources=1)[0] == "WEAK"
    assert L.score_strength(independent_sources=0)[0] == "INSUFFICIENT"


def test_strength_downgrades():
    assert L.score_strength(independent_sources=3, contradiction_level="major")[0] == "MODERATE"
    assert L.score_strength(independent_sources=3, entity_certainty="provisional")[0] == "WEAK"
    assert L.score_strength(independent_sources=3, direct=False)[0] == "MODERATE"


def test_strength_factors_record_inputs():
    strength, factors = L.score_strength(
        independent_sources=2, corroborating_records=9, contradiction_level="minor"
    )
    assert strength == "MODERATE"
    assert (factors.independent_sources, factors.corroborating_records) == (2, 9)
    assert factors.contradiction_level == "minor"


def test_normalize_label_folds_legacy_and_unknown():
    assert L.normalize_label("FACT") == "FACT"
    assert L.normalize_label("inference") == "HYPOTHESIS"
    assert L.normalize_label("SOMETHING_NEW") == "LEAD"
    assert L.normalize_label(None) == "LEAD"


def test_confidence_labels_map_documents():
    assert EV.confidence_label("VERIFIED") == "CORROBORATED_LEAD"
    assert EV.confidence_label("UNVERIFIED") == "LEAD"
    assert EV.confidence_label("ANONYMOUS_TIP") == "HYPOTHESIS"
    assert EV.confidence_label("SYNTHETIC") == "COINCIDENCE"
    assert EV.confidence_label("GARBAGE") == "LEAD"
    assert EV.confidence_label(None) == "LEAD"


def test_evidence_builder_defaults_and_pointers():
    item = EV.make_evidence("document", "summary")
    assert (item.inference_label, item.stance, item.provenance) == ("FACT", "supports", [])
    pointer = EV.source_row_pointer(origin_file="cdr.csv", row_number=7, label="cdr.csv · row 7")
    assert (pointer.kind, pointer.row_number, pointer.origin_file) == ("source_row", 7, "cdr.csv")


def test_neutralize_language_rewords_accusations():
    cleaned, edits = PR.neutralize_language("The mastermind and his culprit fled.")
    assert "mastermind" not in cleaned and "culprit" not in cleaned
    assert "key figure" in cleaned and "person involved" in cleaned
    assert len(edits) == 2


def test_neutralize_language_keeps_data_vocabulary():
    text = "criminal_status says convicted; criminal history reviewed."
    cleaned, edits = PR.neutralize_language(text)
    assert cleaned == text and edits == []
    assert PR.find_guilt_terms("persons of interest were named") == []
    assert PR.find_guilt_terms("The suspected kingpin fled.") == ["kingpin"]


def test_prompt_brief_is_capped_and_deterministic():
    brief = PR.build_investigation_prompt(
        question="Q?",
        scope_summary="scope",
        entity_lines=[f"entity {i} " + "x" * 400 for i in range(12)],
        relationship_lines=[],
        pattern_lines=[],
        hypothesis_lines=[],
        convergence_line="converges",
        gap_lines=[],
    )
    assert brief.count("\n- entity") == 8
    assert all(len(line) <= 302 for line in brief.splitlines())
    assert brief == PR.build_investigation_prompt(
        question="Q?",
        scope_summary="scope",
        entity_lines=[f"entity {i} " + "x" * 400 for i in range(12)],
        relationship_lines=[],
        pattern_lines=[],
        hypothesis_lines=[],
        convergence_line="converges",
        gap_lines=[],
    )


# --------------------------------------------------------------------------- #
# Detectors: live firings
# --------------------------------------------------------------------------- #


def test_cross_case_entity_fires_on_multi_case_record(rich_snapshot, doc_index):
    kinds = {(p.kind, p.entities[0]) for p in P.detect_cross_case_entities(_ctx(rich_snapshot, doc_index))}
    assert ("CROSS_CASE_ENTITY", "Asha Nair") in kinds


def test_cross_case_link_fires_on_spanning_edge(rich_snapshot, doc_index):
    found = P.detect_cross_case_links(_ctx(rich_snapshot, doc_index))
    assert found and all(p.kind == "CROSS_CASE_LINK" for p in found)
    assert any(set(p.entities) == {"Ravi Mehta", "Vikram Rao"} for p in found)


def _call_heavy_snapshot():
    """One dominant call pair plus six quiet pairs, so the z-filter fires."""
    nodes = {
        "hub": _node("hub", "Hub H", cases=("c1",)),
        "spoke": _node("spoke", "Spoke S", cases=("c1",)),
    }
    for index in range(6):
        nodes[f"m{index}"] = _node(f"m{index}", f"Minor{index}", cases=("c1",))
        nodes[f"n{index}"] = _node(f"n{index}", f"MinorPeer{index}", cases=("c1",))
    edges = [_edge("hub", "spoke", "CALLED", doc="d1", count=12)]
    for index in range(6):
        edges.append(_edge(f"m{index}", f"n{index}", "CALLED", doc="d1", count=1))
    return CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)


def test_communication_anomaly_fires_on_volume(doc_index):
    found = P.detect_communication_anomalies(_ctx(_call_heavy_snapshot(), doc_index))
    assert [(p.kind, p.entities) for p in found] == [
        ("COMMUNICATION_ANOMALY", ["Hub H", "Spoke S"])
    ]
    assert "12 calls" in found[0].evidence[0].summary


def test_financial_flow_fires_on_weekly_burst(rich_snapshot, doc_index):
    found = P.detect_financial_flows(_ctx(rich_snapshot, doc_index))
    assert len(found) == 2  # one per endpoint account
    assert all(p.kind == "FINANCIAL_FLOW" and "5 transfers" in p.title for p in found)


def test_vehicle_mismatch_fires_on_use_without_title(rich_snapshot, doc_index):
    found = P.detect_vehicle_mismatches(_ctx(rich_snapshot, doc_index))
    assert len(found) == 1
    assert found[0].kind == "VEHICLE_USE_OWNERSHIP_MISMATCH"
    assert "Sana Iyer" in found[0].entities and "Vikram Rao" in found[0].entities


def test_colocation_fires_on_two_dates_near(rich_snapshot, doc_index):
    found = [p for p in P.detect_colocations(_ctx(rich_snapshot, doc_index)) if not p.excluded]
    assert len(found) == 1
    assert found[0].kind == "COLOCATION"
    assert set(found[0].entities) == {"Ravi Mehta", "Sana Iyer"}


def test_temporal_burst_fires_inside_48_hours(rich_snapshot, doc_index):
    found = P.detect_temporal_bursts(_ctx(rich_snapshot, doc_index))
    assert any(p.kind == "TEMPORAL_BURST" and set(p.entities) == {"Asha Nair", "Vikram Rao"} for p in found)


def test_bridge_fires_on_connector_hub(doc_index):
    nodes = {
        "h": _node("h", "Hub", cases=("c1",)),
        **{f"a{i}": _node(f"a{i}", f"A{i}", cases=("c1",)) for i in range(3)},
        **{f"b{i}": _node(f"b{i}", f"B{i}", cases=("c1",)) for i in range(3)},
    }
    edges = [
        _edge("a0", "a1", "ASSOCIATE_OF"), _edge("a1", "a2", "ASSOCIATE_OF"), _edge("a0", "a2", "ASSOCIATE_OF"),
        _edge("b0", "b1", "ASSOCIATE_OF"), _edge("b1", "b2", "ASSOCIATE_OF"), _edge("b0", "b2", "ASSOCIATE_OF"),
        *[_edge("h", f"a{i}", "ASSOCIATE_OF", key=f"ha{i}") for i in range(3)],
        *[_edge("h", f"b{i}", "ASSOCIATE_OF", key=f"hb{i}") for i in range(3)],
    ]
    snapshot = CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)
    found = P.detect_bridge_signals(_ctx(snapshot, doc_index, centrality=compute_centrality(snapshot)))
    assert [p.entities for p in found] == [["Hub"]]
    assert "importance, not criminality" in found[0].explanation


def test_community_signal_fires_on_dense_group(doc_index):
    nodes = {f"t{i}": _node(f"t{i}", f"T{i}", cases=("c1",)) for i in range(3)}
    nodes.update({f"i{i}": _node(f"i{i}", f"I{i}", cases=("c1",)) for i in range(2)})
    edges = [_edge("t0", "t1", "ASSOCIATE_OF"), _edge("t1", "t2", "ASSOCIATE_OF"), _edge("t0", "t2", "ASSOCIATE_OF")]
    snapshot = CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)
    found = P.detect_community_signals(_ctx(snapshot, doc_index, centrality=compute_centrality(snapshot)))
    assert any(set(p.entity_keys) == {"t0", "t1", "t2"} for p in found)


def test_community_signal_includes_high_centrality_flags(rich_snapshot, doc_index):
    flag = Finding(
        finding_type="HIGH_CENTRALITY",
        title="hub",
        narrative="Ravi Mehta ranks #1 by centrality",
        reason="centrality",
        confidence=0.9,
        confidence_band="HIGH",
        entity_keys=["p2", "p3"],
    )
    found = P.detect_community_signals(_ctx(rich_snapshot, doc_index, analytics_findings=[flag]))
    assert any("HIGH_CENTRALITY" in p.title or "ranks #1" in p.explanation for p in found)


def test_repeated_combination_fires_on_three_docs(rich_snapshot, doc_index):
    found = [p for p in P.detect_repeated_combinations(_ctx(rich_snapshot, doc_index)) if not p.excluded]
    assert any(p.kind == "REPEATED_COMBINATION" and set(p.entities) == {"Ravi Mehta", "Vikram Rao"} for p in found)


def test_er_signal_reports_open_proposals(rich_snapshot, doc_index):
    pending = [ER.PendingAlias(source_key="p2", target_key="p10", note="queue")]
    found = P.detect_er_signals(_ctx(rich_snapshot, doc_index, pending_aliases=pending))
    assert len(found) == 1
    assert (found[0].kind, found[0].inference_label) == ("ER_SIGNAL", "HYPOTHESIS")


# --------------------------------------------------------------------------- #
# Detectors: negatives and transparent exclusions
# --------------------------------------------------------------------------- #


def test_sub_threshold_calls_do_not_fire(rich_snapshot, doc_index):
    found = P.detect_communication_anomalies(_ctx(rich_snapshot, doc_index))
    assert all("Ravi Mehta" not in p.entities or "Sana Iyer" not in p.entities for p in found)


def test_balanced_call_volumes_do_not_fire(doc_index):
    nodes = {f"p{i}": _node(f"p{i}", f"P{i}", cases=("c1",)) for i in range(4)}
    edges = [
        _edge("p0", "p1", "CALLED", count=12),
        _edge("p2", "p3", "CALLED", count=12, key="other"),
    ]
    snapshot = CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)
    assert P.detect_communication_anomalies(_ctx(snapshot, doc_index)) == []


def test_single_colocation_is_set_aside_openly(rich_snapshot, doc_index):
    aside = [p for p in P.detect_colocations(_ctx(rich_snapshot, doc_index)) if p.excluded]
    assert len(aside) == 1
    assert aside[0].inference_label == "COINCIDENCE"
    assert "Single co-location" in (aside[0].exclusion_reason or "")


def test_benign_only_repetition_is_set_aside(rich_snapshot, doc_index):
    aside = [p for p in P.detect_repeated_combinations(_ctx(rich_snapshot, doc_index)) if p.excluded]
    assert len(aside) == 1
    assert "Benign-only" in (aside[0].exclusion_reason or "")


def test_social_only_links_are_set_aside(rich_snapshot, doc_index):
    aside = P.detect_social_only_exclusions(_ctx(rich_snapshot, doc_index))
    assert len(aside) == 1
    assert aside[0].excluded and aside[0].inference_label == "COINCIDENCE"


def test_dismissed_combinations_are_excluded_with_reasons(doc_index):
    sig = P.entity_signature(["hub", "spoke"])
    ctx = _ctx(
        _call_heavy_snapshot(),
        doc_index,
        dismissed_signatures={sig},
        dismissed_notes={sig: "wrong numbers, verified"},
    )
    found = P.detect_all_patterns(ctx)
    target = next(p for p in found if p.kind == "COMMUNICATION_ANOMALY")
    assert target.excluded is True
    assert "wrong numbers, verified" in (target.exclusion_reason or "")


def test_two_documents_are_not_repetition(doc_index):
    nodes = {"a": _node("a", "A"), "b": _node("b", "B")}
    edges = [_edge("a", "b", "ASSOCIATE_OF", doc="d1"), _edge("a", "b", "ASSOCIATE_OF", doc="d2", key="q")]
    snapshot = CaseGraphSnapshot(case_id="", nodes=nodes, edges=edges)
    assert P.detect_repeated_combinations(_ctx(snapshot, doc_index)) == []


def test_vehicle_without_a_holder_is_not_a_mismatch(doc_index):
    nodes = {"a": _node("a", "A"), "v": _node("v", "XX00", label="Vehicle", plate="XX00")}
    snapshot = CaseGraphSnapshot(case_id="", nodes=nodes, edges=[_edge("a", "v", "ASSOCIATE_OF")])
    assert P.detect_vehicle_mismatches(_ctx(snapshot, doc_index)) == []


def test_detect_all_caps_and_orders_and_honours_excluded_flag(rich_snapshot, doc_index):
    many = P.detect_all_patterns(_ctx(rich_snapshot, doc_index), max_patterns=2)
    assert len([p for p in many if not p.excluded]) == 2
    ranks = {"STRONG": 0, "MODERATE": 1, "WEAK": 2, "INSUFFICIENT": 3}
    live = [p.strength for p in many if not p.excluded]
    assert live == sorted(live, key=ranks.get)
    hidden = P.detect_all_patterns(_ctx(rich_snapshot, doc_index), include_excluded=False)
    assert all(not p.excluded for p in hidden)


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


def _entities(*keys):
    names = {"p1": "Ravi Mehta", "p2": "Sana Iyer", "p3": "Asha Nair", "p4": "Vikram Rao",
             "p5": "Meera Iyer", "s1": "Social Sam", "s2": "Social Sue", "p10": "Sana Iyer"}
    return [_resolved(key, names[key]) for key in keys]


def test_direct_relationship_is_fact(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p2", "p3"), doc_index=doc_index)
    direct = [r for r in found if r.kind == "direct"]
    assert len(direct) == 1
    assert direct[0].inference_label == "FACT"
    assert direct[0].analysis is not None and direct[0].analysis.observation


def test_repeated_relationship_needs_two_docs(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p1", "p4"), doc_index=doc_index)
    assert any(r.kind == "repeated" and r.inference_label == "LEAD" for r in found)


def test_temporal_relationship_uses_chronology(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p2", "p3"), doc_index=doc_index)
    assert any(r.kind == "temporal" for r in found)


def test_cross_case_relationship_spans_disjoint_cases(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p1", "p4"), doc_index=doc_index)
    assert any(r.kind == "cross_case" for r in found)


def test_suspicious_relationship_needs_calls_and_transfers(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p2", "p3"), doc_index=doc_index)
    assert any(r.kind == "suspicious" for r in found)
    plain = R.discover_relationships(rich_snapshot, _entities("p1", "p2"), doc_index=doc_index)
    assert all(r.kind != "suspicious" for r in plain)


def test_indirect_relationship_reports_its_path(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p5", "p1"), doc_index=doc_index)
    indirect = [r for r in found if r.kind == "indirect"]
    assert len(indirect) == 1
    assert indirect[0].path is not None and "Arjun Rao" in indirect[0].path.nodes


def test_coincidental_relationship_flags_weak_links(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("s1", "s2"), doc_index=doc_index)
    assert any(r.kind == "coincidental" and r.inference_label == "COINCIDENCE" for r in found)


def test_meta_edges_never_form_relationships(rich_snapshot, doc_index):
    found = R.discover_relationships(rich_snapshot, _entities("p2", "p10"), doc_index=doc_index)
    assert all(r.kind != "direct" for r in found)


def test_relationship_output_is_capped(rich_snapshot, doc_index):
    found = R.discover_relationships(
        rich_snapshot, _entities("p1", "p2", "p3", "p4"), doc_index=doc_index, limit=2
    )
    assert len(found) == 2


# --------------------------------------------------------------------------- #
# Hypotheses and convergence
# --------------------------------------------------------------------------- #


def _rel(kind, entities, docs=(), title="t"):
    evidence = []
    for doc in docs:
        item = EvidenceItem(kind="document", summary=f"in {doc}", inference_label="FACT")
        item.provenance.append(EV.doc_pointer(doc_id=doc, label=doc))
        evidence.append(item)
    return RelationshipFinding(
        kind=kind, entities=list(entities), title=title, description="d", evidence=evidence
    )


def test_hypotheses_cap_at_three_pairs():
    pairs = [(_resolved(f"a{i}", f"A{i}"), _resolved(f"b{i}", f"B{i}")) for i in range(5)]
    hyps = H.build_hypotheses(pairs, [], [])
    assert [h.id for h in hyps] == ["H1-association", "H2-association", "H3-association"]


def test_contradiction_search_runs_all_seven_rules():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    rels = [_rel("direct", ["A", "B"], docs=("d1",))]
    (hyp,) = H.build_hypotheses([(pair[0], pair[1])], rels, [])
    assert hyp.analysis is not None and "ran 7 checks" in hyp.analysis.assessment
    assert any("single origin" in c.summary for c in hyp.contradicting)


def test_dismissed_conflict_is_a_major_contradiction():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    (hyp,) = H.build_hypotheses(
        [(pair[0], pair[1])],
        [_rel("direct", ["A", "B"], docs=("d1", "d2"))],
        [],
        dismissed_signatures={"k1|k2"},
        dismissed_notes={"k1|k2": "verified noise"},
    )
    assert hyp.strength_factors.contradiction_level == "major"
    assert any("reviewer already set aside" in c.summary for c in hyp.contradicting)


def test_benign_and_identity_contradictions_fire():
    pair = (_resolved("k1", "A"), _resolved("k9", "Ghost", resolved=False))
    rels = [_rel("coincidental", ["A", "Ghost"], title="family-only contact")]
    (hyp,) = H.build_hypotheses([(pair[0], pair[1])], rels, [])
    summaries = " ".join(c.summary for c in hyp.contradicting)
    assert "ghost" in summaries and "benign" in summaries


def test_low_confidence_contradiction_fires_for_coincidental_only():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    rels = [_rel("coincidental", ["A", "B"], title="family-only contact")]
    (hyp,) = H.build_hypotheses([(pair[0], pair[1])], rels, [])
    assert any("low-confidence" in c.summary for c in hyp.contradicting)


def test_inferential_contradiction_fires_without_shared_records():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    rels = [_rel("indirect", ["A", "B"], title="via C")]
    (hyp,) = H.build_hypotheses([(pair[0], pair[1])], rels, [])
    assert any("inferential" in c.summary for c in hyp.contradicting)


def test_empty_support_is_flagged_as_speculation():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    (hyp,) = H.build_hypotheses([(pair[0], pair[1])], [], [])
    assert any("speculation" in c.summary for c in hyp.contradicting)
    assert hyp.strength == "INSUFFICIENT"


def test_insufficient_baseline_alternative_always_present():
    pair = (_resolved("k1", "A"), _resolved("k2", "B"))
    (hyp,) = H.build_hypotheses(
        [(pair[0], pair[1])], [_rel("direct", ["A", "B"], docs=("d1", "d2"))], []
    )
    assert H.INSUFFICIENT_BASELINE in hyp.innocent_alternatives


def test_convergence_requires_two_streams_and_no_major_hit():
    def _hyp(strength, docs, level="none"):
        supporting = []
        for doc in docs:
            item = EvidenceItem(kind="document", summary=f"in {doc}")
            item.provenance.append(EV.doc_pointer(doc_id=doc, label=doc))
            supporting.append(item)
        _s, factors = L.score_strength(
            independent_sources=len(docs), contradiction_level=level
        )
        return Hypothesis(
            id="H1-association",
            statement="A and B are associated.",
            supporting=supporting,
            strength_factors=factors,
            strength=strength,
        )

    good = _hyp("MODERATE", ["d1", "d2"])
    result = H.build_convergence([good])
    assert result["converges"] is True and result["stream_count"] == 2

    single = _hyp("MODERATE", ["d1"])
    assert H.build_convergence([single])["converges"] is False

    major = _hyp("STRONG", ["d1", "d2", "d3"], level="major")
    result = H.build_convergence([major])
    assert result["converges"] is False and "major contradiction" in result["note"]

    assert H.build_convergence([])["converges"] is False


# --------------------------------------------------------------------------- #
# Gaps and next steps
# --------------------------------------------------------------------------- #


def test_gaps_cover_unresolved_and_missing_sources():
    entities = [_resolved("k1", "A"), _resolved("u:x", "Ghost", resolved=False)]
    gaps = G.build_gaps(entities, set(), [], [], ["c1"])
    categories = {g.category for g in gaps}
    assert {"unresolved-entity", "missing-source"} <= categories
    assert all(g.inference_label == "DATA_GAP" for g in gaps)
    assert any("FIR" in g.description for g in gaps)


def test_gaps_flag_provisional_and_single_source():
    entities = [_resolved("k1", "A", confidence=0.5)]
    _, factors = L.score_strength(independent_sources=1)
    hyps = [Hypothesis(id="H1", statement="s", strength="WEAK", strength_factors=factors)]
    gaps = G.build_gaps(entities, {"FIR", "CDR", "FINANCIAL", "SOCIAL_MEDIA", "CRIMINAL_HISTORY", "SURVEILLANCE"}, hyps, [], ["c1"])
    categories = {g.category for g in gaps}
    assert {"provisional-identity", "single-source"} <= categories


def test_gaps_flag_missing_links_and_cap_output():
    entities = [_resolved("k1", "A"), _resolved("k2", "B")]
    gaps = G.build_gaps(entities, {"FIR"}, [], [], ["c1"])
    assert "missing-link" in {g.category for g in gaps}
    assert len(gaps) <= G.MAX_GAPS


def test_next_steps_are_identity_first_and_stable():
    entities = [_resolved("k1", "A"), _resolved("u:x", "Ghost", resolved=False)]
    gaps = G.build_gaps(entities, set(), [], [], ["c1"])
    steps = NS.build_next_steps(entities, gaps, [], [], ["c1"])
    assert steps[0].priority == "high" and "Ghost" in steps[0].action
    assert [s.priority for s in steps] == sorted(
        [s.priority for s in steps], key={"high": 0, "medium": 1, "low": 2}.get
    )
    assert len(steps) <= NS.MAX_STEPS


def test_next_steps_are_never_coercive():
    entities = [_resolved("k1", "A"), _resolved("u:x", "Ghost", resolved=False)]
    gaps = G.build_gaps(entities, set(), [], [], ["c1"])
    steps = NS.build_next_steps(entities, gaps, [], [], ["c1"])
    text = " ".join(s.action + " " + s.rationale for s in steps).lower()
    assert not any(term in text for term in COERCIVE_TERMS)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #


def test_record_turn_merges_and_bounds():
    state = M.blank_state("why?")
    for i in range(25):
        state = M.record_turn(state, question=f"q{i}", facts=[f"f{i}"])
    assert state["objective"] == "why?"
    assert state["questions"] == [f"q{i}" for i in range(5, 25)]
    assert state["confirmed"] == [f"f{i}" for i in range(25)][-M.MAX_CONFIRMED:]


def test_memory_section_renders_thread_state():
    from app.db.models import InvestigationSession

    row = InvestigationSession(
        id="inv-1",
        dataset_id="ds-1",
        case_id=None,
        scope="master",
        title="t",
        state=M.record_turn(M.blank_state(), question="q1", unresolved=["Ghost"]),
    )
    section = M.memory_section(row)
    assert (section.investigation_id, section.questions_asked) == ("inv-1", 1)
    assert section.unresolved == ["Ghost"]


# --------------------------------------------------------------------------- #
# Part 2: the live API against imported datasets
# --------------------------------------------------------------------------- #

WORLD_CASES = "case_id,case_number,title\nC1,WLD/2026/1,World case one\n"
WORLD_PEOPLE = (
    "person_id,full_name,criminal_status\n"
    "P1,Ravi Mehta,convicted\n"
    "P2,Sana Iyer,\n"
    "P3,Asha Nair,\n"
    "P4,Vikram Rao,\n"
)

WORLD_NOTE = "Field note: Sana Iyer was seen near the harbour godown with Asha Nair.\n"


def _world_calls():
    rows = ["from_person,to_person,call_date,duration"]
    for i in range(12):
        rows.append(f"P2,P3,2026-08-{10 + (i % 5):02d} 21:{10 + i:02d},180")
    for i in range(3):
        rows.append(f"P1,P2,2026-08-1{i} 09:15,60")
    return "\n".join(rows) + "\n"


async def _import_world(root: Path, name: str, *, people: str = WORLD_PEOPLE) -> str:
    folder = root / name.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cases.csv").write_text(WORLD_CASES, encoding="utf-8")
    (folder / "people.csv").write_text(people, encoding="utf-8")
    (folder / "calls.csv").write_text(_world_calls(), encoding="utf-8")
    (folder / "note.txt").write_text(WORLD_NOTE, encoding="utf-8")
    async with async_session() as session:
        report = await run_import(
            session,
            [folder],
            ImportOptions(
                name=name,
                copy_inputs=False,
                activate=True,
                build_graph=True,
                jurisdiction_id=JURISDICTION,
            ),
        )
    assert report.error is None, report.error
    return report.dataset_id


@pytest.fixture()
async def investigation_world(client, investigator_headers, container, tmp_path):
    name = f"World {uuid.uuid4().hex[:6]}"
    dataset_id = await _import_world(tmp_path, name)
    async with async_session() as session:
        row = (
            await session.execute(
                select(Case).where(
                    Case.dataset_id == dataset_id, Case.case_number == "WLD/2026/1"
                )
            )
        ).scalars().first()
    assert row is not None, "world case not imported"
    return {"dataset_id": dataset_id, "case_id": row.id, "headers": investigator_headers}


def _investigate(client, headers, payload):
    res = client.post("/api/v1/investigate", headers=headers, json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def test_investigate_master_shape_and_keyless_honesty(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    for key in (
        "question", "investigation_id", "scope", "entities", "relationships", "patterns",
        "hypotheses", "assessment", "gaps", "next_steps", "timeline", "focused_graph",
        "provenance", "memory", "timing_ms",
    ):
        assert key in body, key
    assert body["scope"]["mode"] == "master"
    assert body["scope"]["dataset_id"] == investigation_world["dataset_id"]
    assert body["investigation_id"]
    model = body["assessment"]["model"]
    assert model["available"] is False
    assert "deterministic analysis only" in model["caveats"][0]


def test_question_names_resolve_to_canonical_records(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    resolved = {e["display_name"]: e for e in body["entities"] if e["resolved"]}
    assert {"Sana Iyer", "Asha Nair"} <= set(resolved)
    assert all(resolved[name]["canonical_id"] for name in ("Sana Iyer", "Asha Nair"))


def test_convicted_status_echoes_from_data_only(client, investigation_world):
    body = _investigate(
        client, investigation_world["headers"], {"question": "What about Ravi Mehta?"}
    )
    echoed = {e["display_name"]: e.get("criminal_status") for e in body["entities"]}
    assert echoed.get("Ravi Mehta") == "convicted"
    assert echoed.get("Sana Iyer", None) in (None, "")


def test_response_carries_no_guilt_terms(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    text = json.dumps(body).lower()
    hits = [t for t in GUILT_TERMS if re.search(r"\b" + re.escape(t) + r"\b", text)]
    assert hits == []


def test_memory_continuation_builds_a_thread(client, investigation_world):
    headers = investigation_world["headers"]
    first = _investigate(client, headers, {"question": "Who is Sana Iyer?"})
    second = _investigate(
        client, headers, {"question": "Who is Asha Nair?", "investigation_id": first["investigation_id"]}
    )
    assert second["investigation_id"] == first["investigation_id"]
    assert second["memory"]["questions_asked"] == 2
    assert second["memory"]["prior_questions"] == ["Who is Sana Iyer?", "Who is Asha Nair?"]


def test_session_endpoint_roundtrip_and_unknown_is_404(client, investigation_world):
    headers = investigation_world["headers"]
    body = _investigate(client, headers, {"question": "Who is Sana Iyer?"})
    res = client.get(f"/api/v1/investigate/sessions/{body['investigation_id']}", headers=headers)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["dataset_id"] == investigation_world["dataset_id"]
    assert payload["memory"]["questions_asked"] == 1
    res = client.get("/api/v1/investigate/sessions/does-not-exist", headers=headers)
    assert res.status_code == 404


def test_patterns_endpoint_master_and_case(client, investigation_world):
    headers = investigation_world["headers"]
    res = client.get("/api/v1/investigate/patterns", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "master"
    res = client.get(
        f"/api/v1/investigate/patterns?case_id={investigation_world['case_id']}", headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "case"


def test_viewer_is_forbidden_and_anonymous_is_rejected(client, investigation_world, viewer_headers):
    res = client.post(
        "/api/v1/investigate", headers=viewer_headers, json={"question": "Who is Sana Iyer?"}
    )
    assert res.status_code == 403
    res = client.post("/api/v1/investigate", json={"question": "Who is Sana Iyer?"})
    assert res.status_code in (401, 403)


def test_conversational_fast_path_answers_politely(client, investigation_world):
    body = _investigate(client, investigation_world["headers"], {"question": "hi there"})
    assert body["patterns"] == [] and body["relationships"] == []
    assert "Hello" in body["assessment"]["assessment"]
    assert body["investigation_id"]


def test_case_scope_and_unknown_case_404(client, investigation_world):
    headers = investigation_world["headers"]
    body = _investigate(
        client, headers, {"question": "Summarise.", "case_id": investigation_world["case_id"]}
    )
    assert body["scope"]["mode"] == "case"
    res = client.post(
        "/api/v1/investigate", headers=headers, json={"question": "Summarise.", "case_id": "nope"}
    )
    assert res.status_code == 404


def test_short_questions_are_rejected(client, investigation_world):
    res = client.post(
        "/api/v1/investigate", headers=investigation_world["headers"], json={"question": "hi"}
    )
    assert res.status_code == 422


async def test_no_dataset_is_a_422(client, investigation_world):
    async with async_session() as session:
        actives = (
            await session.execute(select(Dataset.id).where(Dataset.is_active.is_(True)))
        ).scalars().all()
        for dataset_id in actives:
            row = await session.get(Dataset, dataset_id)
            row.is_active = False
        await session.commit()
    try:
        res = client.post(
            "/api/v1/investigate",
            headers=investigation_world["headers"],
            json={"question": "Who is Sana Iyer?"},
        )
        assert res.status_code == 422
    finally:
        async with async_session() as session:
            for dataset_id in actives:
                row = await session.get(Dataset, dataset_id)
                row.is_active = True
            await session.commit()


async def test_investigate_audit_link_is_hash_valid(client, investigation_world):
    headers = investigation_world["headers"]
    body = _investigate(client, headers, {"question": "Who is Sana Iyer?"})
    async with async_session() as session:
        row = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.action_type == "INVESTIGATE",
                    AuditLog.target_resource == f"investigation:{body['investigation_id']}",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert row is not None
        action = row.action_type.value if hasattr(row.action_type, "value") else str(row.action_type)
        payload = _row_payload(
            user_id=row.user_id,
            badge_number=row.badge_number,
            action_type=action,
            target_resource=row.target_resource,
            case_id=row.case_id,
            jurisdiction_id=row.jurisdiction_id,
            ip_address=row.ip_address,
            trace_id=row.trace_id,
            details=row.details,
            timestamp=row.timestamp,
        )
        assert chain_hash(row.prev_row_hash, canonical_json(payload)) == row.row_hash
        if row.id > 1:
            prev = await session.get(AuditLog, row.id - 1)
            assert prev is not None and prev.row_hash == row.prev_row_hash
        else:
            assert row.prev_row_hash == GENESIS_HASH
        narrative = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action_type == "AI_QUERY",
                    AuditLog.target_resource == f"investigation:{body['investigation_id']}",
                )
            )
        ).scalars().first()
        assert narrative is not None


async def test_dismissed_pair_is_excluded_as_decoy(client, investigation_world):
    headers = investigation_world["headers"]
    question = "Is there any connection between Sana Iyer and Asha Nair?"
    first = _investigate(client, headers, {"question": question})
    live = [p for p in first["patterns"] if not p["excluded"] and p["entity_keys"]]
    assert live, "world yields no live patterns to dismiss"
    target = live[0]
    async with async_session() as session:
        session.add(
            DetectedPattern(
                id=new_uuid(),
                case_id=investigation_world["case_id"],
                pattern_type=PatternType.STRUCTURING,
                confidence=0.9,
                entity_keys=list(target["entity_keys"]),
                evidence_doc_ids=[],
                explanation="decoy",
                details={},
                status=PatternStatus.DISMISSED,
                review_note="verified wrong numbers",
            )
        )
        await session.commit()
    second = _investigate(client, headers, {"question": question})
    match = next(
        p
        for p in second["patterns"]
        if p["kind"] == target["kind"] and sorted(p["entity_keys"]) == sorted(target["entity_keys"])
    )
    assert match["excluded"] is True
    assert "verified wrong numbers" in (match["exclusion_reason"] or "")


async def test_rejected_identity_proposal_is_excluded(client, investigation_world):
    headers = investigation_world["headers"]
    question = "Is there any connection between Sana Iyer and Asha Nair?"
    first = _investigate(client, headers, {"question": question})
    keys = {}
    for entity in first["entities"]:
        if entity["resolved"]:
            keys[entity["display_name"]] = entity["canonical_id"]
    assert {"Sana Iyer", "Asha Nair"} <= set(keys)
    async with async_session() as session:
        session.add(
            EntityResolutionItem(
                id=new_uuid(),
                case_id=investigation_world["case_id"],
                source_node_key=keys["Sana Iyer"],
                target_node_key=keys["Asha Nair"],
                similarity_score=0.71,
                match_basis=MatchBasis.NAME_FUZZY,
                evidence_doc_ids=[],
                status=ResolutionStatus.REJECTED,
                resolution_note="different middle names",
            )
        )
        await session.commit()
    second = _investigate(client, headers, {"question": question})
    pair = sorted([keys["Sana Iyer"], keys["Asha Nair"]])
    for pattern in second["patterns"]:
        if sorted(pattern["entity_keys"]) == pair:
            assert pattern["excluded"] is True


async def test_cross_dataset_isolation_and_thread_pinning(
    client, investigation_world, investigator_headers, container, tmp_path
):
    headers = investigation_world["headers"]
    question = "Is there any connection between Sana Iyer and Asha Nair?"
    before = _investigate(client, headers, {"question": question})
    before_keys = {e["canonical_id"] for e in before["entities"] if e["resolved"]}
    assert before_keys

    people_b = (
        "person_id,full_name,criminal_status\n"
        "P1,Sana Iyer,\n"
        "P9,Quentin Blake,\n"
    )
    dataset_b = await _import_world(tmp_path, f"WorldB {uuid.uuid4().hex[:6]}", people=people_b)
    assert dataset_b != investigation_world["dataset_id"]

    after = _investigate(client, headers, {"question": question})
    assert after["scope"]["dataset_id"] == dataset_b
    after_keys = {e["canonical_id"] for e in after["entities"] if e["resolved"]}
    assert after_keys and after_keys.isdisjoint(before_keys)

    res = client.post(
        "/api/v1/investigate",
        headers=headers,
        json={"question": "And then?", "investigation_id": before["investigation_id"]},
    )
    assert res.status_code == 422

    async with async_session() as session:
        row_b = await session.get(Dataset, dataset_b)
        row_b.is_active = False
        row_a = await session.get(Dataset, investigation_world["dataset_id"])
        row_a.is_active = True
        await session.commit()


def test_include_excluded_flag_hides_set_asides(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {
            "question": "Is there any connection between Sana Iyer and Asha Nair?",
            "include_excluded": False,
        },
    )
    assert all(not p["excluded"] for p in body["patterns"])
