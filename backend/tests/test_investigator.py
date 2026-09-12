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


def test_a_sentence_opening_question_word_is_not_glued_onto_a_name():
    """A question is not part of the name it opens with.

    "Are Harish Varma and Suresh Pradhan connected?" used to yield the mention
    "Are Harish Varma", which resolves to nothing -- the investigator then saw a
    data gap about a person who does not exist and a next step to "establish the
    identity" of a question word.
    """
    assert ER.extract_mentions("Are Harish Varma and Suresh Pradhan connected?") == [
        "Harish Varma",
        "Suresh Pradhan",
    ]
    assert ER.extract_mentions("Is Sana Iyer connected to Asha Nair?") == ["Sana Iyer", "Asha Nair"]
    assert ER.extract_mentions("Do Sana Iyer and Asha Nair share a vehicle?") == [
        "Sana Iyer",
        "Asha Nair",
    ]
    assert ER.extract_mentions("Who is Sana Iyer?") == ["Sana Iyer"]
    # Only sentence-opening words are dropped: a name that genuinely starts with
    # one of these words mid-sentence is left alone.
    assert "The Estate" in ER.extract_mentions("The Estate at Sana Iyer")


def test_a_possessive_mention_is_the_same_person(rich_snapshot, doc_index):
    """``Sana Iyer's`` names Sana Iyer; it is not a second, unresolvable person."""
    entity = ER.resolve_mention("Sana Iyer's", rich_snapshot)
    assert (entity.canonical_id, entity.resolved, entity.matched_by) == ("p2", True, "name")
    assert entity.confidence == pytest.approx(1.0)
    # An apostrophe inside a word is punctuation, not a quotation.
    assert ER.extract_mentions("Check Sana Iyer's phone and Asha Nair's account") == [
        "Sana Iyer",
        "Asha Nair",
    ]
    assert ER.extract_mentions("Show me 'Ravi Mehta' and Vikram Rao") == ["Ravi Mehta", "Vikram Rao"]


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


def test_roll_up_provenance_is_derived_and_deduped():
    """A finding's flat pointer list is exactly the union of its evidence.

    The console reads the rolled-up list to show how many records stand behind a
    finding, so it must never invent a pointer, duplicate one, or drop the
    contradicting side.
    """
    first = EV.make_evidence(
        "document",
        "one",
        provenance=[EV.metric_pointer(name="m1", label="metric one")],
    )
    second = EV.make_evidence(
        "document",
        "two",
        provenance=[
            EV.metric_pointer(name="m1", label="metric one"),
            EV.source_row_pointer(origin_file="cdr.csv", row_number=4, label="row 4"),
        ],
    )
    rolled = EV.roll_up_provenance([first, second])
    assert [(pointer.kind, pointer.ref) for pointer in rolled] == [
        ("metric", "m1"),
        ("source_row", "cdr.csv#row-4"),
    ]
    assert EV.roll_up_provenance([]) == []
    capped = EV.roll_up_provenance(
        [
            EV.make_evidence(
                "document",
                "many",
                provenance=[
                    EV.metric_pointer(name=f"m{index}", label=f"metric {index}")
                    for index in range(EV.FINDING_PROVENANCE_CAP + 5)
                ],
            )
        ]
    )
    assert len(capped) == EV.FINDING_PROVENANCE_CAP


def test_findings_carry_openable_sources_derived_from_their_evidence(client, investigation_world):
    """Every finding in a live answer exposes the pointers its evidence carries.

    Counting sources from the nested evidence alone left the flat lists empty, so
    a console (or an auditor reading the JSON) had to walk the whole structure to
    see whether a claim was openable at all.
    """
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    checked = 0
    for pattern in body["patterns"]:
        expected = {
            (pointer["kind"], pointer["ref"])
            for item in pattern["evidence"]
            for pointer in item["provenance"]
        }
        assert {(p["kind"], p["ref"]) for p in pattern["provenance"]} == expected
        checked += 1
    for relationship in body["relationships"]:
        expected = {
            (pointer["kind"], pointer["ref"])
            for item in relationship["evidence"]
            for pointer in item["provenance"]
        }
        assert {(p["kind"], p["ref"]) for p in relationship["provenance"]} == expected
        checked += 1
    for hypothesis in body["hypotheses"]:
        expected = {
            (pointer["kind"], pointer["ref"])
            for item in [*hypothesis["supporting"], *hypothesis["contradicting"]]
            for pointer in item["provenance"]
        }
        assert {(p["kind"], p["ref"]) for p in hypothesis["provenance"]} == expected
        checked += 1
    assert checked > 0, "the world must produce findings for this test to mean anything"
    assert any(
        finding["provenance"]
        for finding in [*body["patterns"], *body["relationships"], *body["hypotheses"]]
    ), "at least one finding must expose an openable pointer"


def test_a_document_pointer_always_has_a_document_to_open():
    """Negative control for the provenance contract (§13).

    The console turns a `document` pointer into a link. If a dataset id, a metric
    name or a graph-edge key is ever typed as `document`, that link dead-ends —
    which is exactly the defect this asserts against. The mirror rule matters
    too: a pointer typed `document` with no `doc_id` is unopenable, so the answer
    must not claim one exists.
    """
    unopenable = [
        EV.dataset_pointer(dataset_id="ds-1", label="dataset row"),
        EV.metric_pointer(name="centrality:betweenness", label="betweenness"),
        EV.edge_pointer(edge_key="EdgeKey:abc", label="call edge"),
    ]
    assert all(pointer.kind != "document" for pointer in unopenable)
    assert all(pointer.doc_id is None for pointer in unopenable)
    assert EV.source_pointer(doc_id="dataset:ds-1", label="row").kind == "dataset"
    opened = EV.source_pointer(doc_id="doc-9", label="FIR_009.pdf", origin_file="FIR_009.pdf")
    assert opened.kind == "document" and opened.doc_id == "doc-9"


def test_a_live_answer_never_types_a_non_document_as_openable(client, investigation_world):
    """The provenance contract, over every pointer a real answer carries."""
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    pointers = []

    def walk(node):
        if isinstance(node, dict):
            if {"kind", "ref"} <= set(node) and "label" in node:
                pointers.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    assert pointers, "the answer must expose pointers for this test to mean anything"
    for pointer in pointers:
        if pointer["kind"] == "document":
            assert pointer["doc_id"], f"document pointer without an id: {pointer}"
        else:
            assert not pointer["doc_id"], f"non-document typed as openable: {pointer}"


def test_a_scope_with_call_edges_never_reports_missing_cdr(
    client, investigation_world
):
    """Defect class B, end to end: absence must be read from the data.

    The world's records put call traffic into the graph; an answer that announced
    "No CDR records" here would be making a false statement about its own data.
    """
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    described = [gap["description"] for gap in body["gaps"] if gap["category"] == "missing-source"]
    assert described, "the world is missing families, so gaps must be reported"
    assert not any("CDR" in text for text in described)
    assert all(gap["what_would_help"] for gap in body["gaps"])


def test_dataset_level_records_point_at_the_dataset_not_a_document():
    """A row with no file behind it must not masquerade as a document.

    Operational tables are stamped ``source_doc_id = dataset:<id>`` by the
    importer; rendering that as a document pointer handed the console a link
    that 404s. The pointer now names what it actually is.
    """
    surface = EV.source_pointer(doc_id="dataset:abc-123", label="dataset:abc-123")
    assert surface.kind == "dataset"
    assert surface.ref == "dataset:abc-123"
    assert surface.doc_id is None, "no document link for a dataset-level record"

    document = EV.source_pointer(
        doc_id="doc-1", label="FIR_001.pdf", origin_file="FIR_001.pdf"
    )
    assert document.kind == "document"
    assert document.doc_id == "doc-1"


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


def test_indirect_relationship_opens_every_hop(rich_snapshot, doc_index):
    """A route claim must carry the edges and documents that make it real.

    The finding says "linked via X"; the reader has to be able to open each hop
    and land on the record behind it, so the path is not an assertion.
    """
    found = R.discover_relationships(rich_snapshot, _entities("p5", "p1"), doc_index=doc_index)
    indirect = next(r for r in found if r.kind == "indirect")
    assert indirect.path is not None
    assert len(indirect.path.edges) == len(indirect.path.nodes) - 1
    keys = {edge.key for edge in rich_snapshot.edges}
    assert set(indirect.path.edges) <= keys, "every hop names a stored edge"
    kinds = {item.kind for evidence in indirect.evidence for item in evidence.provenance}
    assert "graph_edge" in kinds, "the hops themselves are openable"
    assert "document" in kinds, "and so is the record each hop came from"
    assert all(
        pointer.doc_id or pointer.origin_file
        for evidence in indirect.evidence
        for pointer in evidence.provenance
        if pointer.kind == "document"
    )


def test_temporal_relationship_carries_its_edges(rich_snapshot, doc_index):
    """The chronological route is evidence-backed like every other claim."""
    found = R.discover_relationships(rich_snapshot, _entities("p3", "p4"), doc_index=doc_index)
    temporal = [r for r in found if r.kind == "temporal"]
    if not temporal:  # pragma: no cover - the fixture provides dated calls
        pytest.skip("fixture produced no chronologically valid route")
    route = temporal[0]
    assert route.path is not None and route.path.edges
    kinds = {item.kind for evidence in route.evidence for item in evidence.provenance}
    assert "graph_edge" in kinds and "document" in kinds


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


def test_present_sources_are_read_from_the_data_not_the_file_labels(rich_snapshot):
    """A gap must never claim an absence the graph contradicts.

    The corpus feeds call traffic, transfers and sightings straight into the
    graph, so a scope can hold 16k CALLED edges and no document typed "CDR".
    Reporting "no CDR records" there would be a false statement about the data.
    """
    present = G.present_sources({"FIR"}, rich_snapshot)
    assert "FIR" in present  # declared by the document types
    assert "CDR" in present  # CALLED edges are call detail records
    assert "FINANCIAL" in present  # TRANSFER_TO edges
    assert "SOCIAL_MEDIA" in present  # LINKED_ON_SOCIAL edges
    assert "CRIMINAL_HISTORY" in present  # the node carries an authoritative status
    assert "SURVEILLANCE" not in present  # nothing marks a sighting here

    # And the gap builder respects that reading.
    gaps = G.build_gaps([], present, [], [], ["c1"])
    sources = {gap.description.split()[1] for gap in gaps}
    assert "CDR" not in sources
    assert "SURVEILLANCE" in sources


def test_every_source_family_is_reported_when_the_scope_holds_none():
    """The gap list is a checklist of what an investigation could ask for.

    It must name each family the platform can reason over — including the ones
    this corpus happens to lack (CCTV, vehicle registry, intel) — because an
    investigator's next move depends on knowing which record is absent.
    """
    gaps = G.build_gaps([], set(), [], [], ["c1"])
    families = {gap.description.split()[1] for gap in gaps if gap.category == "missing-source"}
    assert {"FIR", "CDR", "FINANCIAL", "VEHICLE", "CCTV", "SURVEILLANCE",
            "SOCIAL_MEDIA", "INTELLIGENCE", "CRIMINAL_HISTORY"} <= families
    assert all(g.what_would_help for g in gaps if g.category == "missing-source")


def test_a_source_family_present_only_as_files_or_edges_is_not_reported_missing():
    """Each family is recognised from data, not from one naming convention."""
    present = G.present_sources(
        {"CCTV_FOOTAGE", "INTEL_NOTES", "VEHICLE_REGISTRY", "FIR"},
        CaseGraphSnapshot(case_id="", nodes={}, edges=[]),
    )
    assert {"CCTV", "INTELLIGENCE", "VEHICLE", "FIR"} <= present
    gaps = G.build_gaps([], present, [], [], ["c1"])
    reported = {gap.description.split()[1] for gap in gaps if gap.category == "missing-source"}
    assert not ({"CCTV", "INTELLIGENCE", "VEHICLE", "FIR"} & reported)


def test_scope_without_documents_says_so_instead_of_showing_a_clean_result():
    without = G.build_gaps([_resolved("k1", "A")], {"FIR"}, [], [], ["c1"], documents_in_scope=0)
    assert "missing-documents" in {gap.category for gap in without}
    with_docs = G.build_gaps([_resolved("k1", "A")], {"FIR"}, [], [], ["c1"], documents_in_scope=7)
    assert "missing-documents" not in {gap.category for gap in with_docs}


def test_an_incomplete_import_is_its_own_gap():
    """A subset must never be presented as the whole dataset.

    The three cases are distinct and must stay distinguishable: files that could
    not be read (missing evidence), records with no document behind them (thin
    provenance), and files left out on purpose (a boundary, not a hole).
    """
    report = G.ImportReport(
        files_total=1038,
        failed=4,
        without_document=145,
        excluded_on_policy=12,
        examples=["summary.xlsx (UNSUPPORTED)", "notes.json (CORRUPT)"],
    )
    gaps = G.build_gaps([], set(G.MISSING_SOURCE_HELP), [], [], ["c1"], import_report=report)
    incomplete = next(gap for gap in gaps if gap.category == "incomplete-records")
    assert "4 file(s) could not be read" in incomplete.description
    assert "145 produced records without a source document" in incomplete.description
    assert "12 file(s) were excluded by policy" in incomplete.description
    assert "summary.xlsx" in incomplete.description
    # Deliberate exclusions alone are not a gap.
    assert G.ImportReport(files_total=1038, excluded_on_policy=12).incomplete is False
    assert G.ImportReport(files_total=10).incomplete is False
    assert G.ImportReport(files_total=10, failed=1).incomplete is True


def test_gap_ranking_keeps_the_decision_relevant_gaps_under_the_cap():
    """Generic family notices must not crowd out the gaps about this question.

    Nine missing source families, ten unmatched names and a single-source
    reading are more gaps than the cap allows; what survives has to be the part
    the investigator can act on, not nine copies of "this family is absent".
    """
    entities = [_resolved(f"u:{index}", f"Ghost {index}", resolved=False) for index in range(10)]
    entities.append(_resolved("k1", "A", confidence=0.5))
    _, factors = L.score_strength(independent_sources=1)
    hyps = [Hypothesis(id="H1", statement="s", strength="WEAK", strength_factors=factors)]
    gaps = G.build_gaps(entities, set(), hyps, [], ["c1"], documents_in_scope=0)
    assert len(gaps) == G.MAX_GAPS, "the cap must actually bite for this test to mean anything"
    categories = [gap.category for gap in gaps]
    assert categories[0] == "unresolved-entity"
    assert "single-source" in categories
    assert "missing-documents" in categories
    assert categories.index("single-source") < categories.index("missing-source")


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


def test_memory_remembers_what_was_ruled_out_not_only_what_was_found():
    """Continuity includes the negative results.

    A thread that only stored positive findings would let a later turn re-propose
    a reading this investigation already tested and set aside, with no sign that
    it had been answered before.
    """
    state = M.blank_state("who moved the money")
    state = M.record_turn(
        state,
        question="What connects Sana Iyer and Asha Nair?",
        relationships=["direct: Sana Iyer & Asha Nair: directly linked (CALLED)"],
        contradictions=["only one document supports the link"],
        rejected=[{"id": "H1", "statement": "A & B coordinated", "reason": "single source"}],
        findings=["WEAK: Sana Iyer is directly linked to Asha Nair."],
        entities=["Sana Iyer"],
    )
    row = _session_row(state)
    section = M.memory_section(row)
    assert section.objective == "who moved the money"
    assert section.relationships and "directly linked" in section.relationships[0]
    assert section.contradictions == ["only one document supports the link"]
    assert section.rejected_hypotheses[0]["id"] == "H1"
    assert section.prior_findings and section.prior_findings[0].startswith("WEAK:")


def test_a_re_proposed_reading_is_marked_as_already_set_aside():
    """Re-testing a ruled-out hypothesis must show the investigator the loop."""
    from app.investigator.orchestrator import _carry_rejected_hypotheses

    thread = _session_row(
        M.record_turn(
            M.blank_state("q"),
            question="q1",
            rejected=[{"id": "H1-association", "statement": "s", "reason": "single source"}],
        )
    )
    _, factors = L.score_strength(independent_sources=1)
    hypothesis = Hypothesis(
        id="H1-association",
        statement="s",
        strength="WEAK",
        strength_factors=factors,
        analysis=ObservationBlock(observation="o", interpretation="i", assessment="a"),
    )
    _carry_rejected_hypotheses([hypothesis], thread)
    assert any("Re-tested" in note for note in hypothesis.strength_factors.notes)
    assert "Re-tested" in (hypothesis.analysis.assessment if hypothesis.analysis else "")
    assert hypothesis.strength == "WEAK", "memory annotates; it never re-scores"

    fresh = Hypothesis(id="H2-other", statement="s", strength="WEAK")
    _carry_rejected_hypotheses([fresh], thread)
    assert fresh.strength_factors.notes == []


def test_the_narrative_brief_carries_the_threads_own_findings():
    """The model is told what this investigation already decided — and refused."""
    from app.investigator.orchestrator import _memory_lines

    assert _memory_lines(None) == []
    thread = _session_row(
        M.record_turn(
            M.blank_state("objective one"),
            question="who is Sana Iyer?",
            contradictions=["only one document supports the link"],
            rejected=[{"id": "H1", "statement": "A & B coordinated", "reason": "single source"}],
        )
    )
    lines = _memory_lines(thread)
    joined = " | ".join(lines)
    assert "objective one" in joined
    assert "set aside earlier: H1" in joined
    assert "contradiction already recorded" in joined
    brief = PR.build_investigation_prompt(
        question="and Asha Nair?",
        scope_summary="Master Network",
        entity_lines=["Sana Iyer (Person, exact, confidence 1.0)"],
        relationship_lines=[],
        pattern_lines=[],
        hypothesis_lines=[],
        convergence_line="",
        gap_lines=[],
        memory_lines=lines,
    )
    assert "Earlier in this investigation" in brief
    assert "who is Sana Iyer?" in brief
    # A first question must not grow a memory block at all.
    first = PR.build_investigation_prompt(
        question="q",
        scope_summary="s",
        entity_lines=[],
        relationship_lines=[],
        pattern_lines=[],
        hypothesis_lines=[],
        convergence_line="",
        gap_lines=[],
        memory_lines=[],
    )
    assert "Earlier in this investigation" not in first


def _session_row(state):
    from app.db.models import InvestigationSession

    return InvestigationSession(
        id="inv-1", dataset_id="ds-1", case_id=None, scope="master", title="t", state=state
    )


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


# ---------------------------------------------------------------------------
# Part 1b -- the AI narrative contract (deterministic: a stub router stands in
# for the provider). The model explains; it can never add evidence, never reach
# the investigator with accusatory language, and degrades honestly whenever it
# is missing, unreachable, or malformed.
# ---------------------------------------------------------------------------


class _StubRouter:
    """Stands in for :class:`AIModelRouter`; records the prompt it was given."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, task, system_prompt, user_prompt, **kwargs):
        self.calls.append({"task": task, "system": system_prompt, "prompt": user_prompt})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _gateway(reply):
    from app.ai.gateway import AIGateway
    from app.config import get_settings

    return AIGateway(settings=get_settings(), router=_StubRouter(reply))


async def _narrate(reply, *, entities=(), brief="FACT: A called B three times."):
    gateway = _gateway(reply)
    return await gateway.investigate_narrative(
        question="Are A and B connected?", brief=brief, investigation_id="inv-1",
        entities=list(entities),
    ), gateway


def _entity(name, canonical_id, label="Person"):
    return ResolvedEntity(
        mention=name, display_name=name, canonical_id=canonical_id, label=label,
        resolved=True, matched_by=MatchBasis.NAME_FUZZY, confidence=1.0,
        criminal_status=None, cases=[], provenance=[],
    )


async def test_a_model_narrative_is_neutralized_before_the_investigator_sees_it():
    reply = {
        "available": True,
        "model": "stub-model",
        "content": json.dumps(
            {
                "summary": "The mastermind is guilty; the culprit confessed.",
                "interpretation": "This looks like a criminal network.",
                "caveats": ["The ringleader may be someone else."],
                "suggested_next_actions": ["Arrest the perpetrator."],
            }
        ),
    }
    section, _ = await _narrate(reply)
    assert section.available is True
    assert section.language_edits, "the substitution itself must be auditable"
    rendered = " ".join(
        [section.summary, section.interpretation, *section.caveats, *section.suggested_next_actions]
    )
    assert PR.find_guilt_terms(rendered) == [], rendered
    assert "key figure" in section.summary
    # The original accusation is not recoverable from what the investigator sees.
    assert "mastermind" not in rendered.lower()
    assert "guilty" not in rendered.lower()


async def test_a_model_narrative_that_is_not_json_degrades_to_the_analysis_only():
    section, _ = await _narrate(
        {"available": True, "model": "stub-model", "content": "Sure! Here is my take: they know each other."}
    )
    assert section.available is False
    assert section.reason == "narrative_unparseable"
    assert section.summary == "" and section.assessment == ""
    assert any("deterministic" in caveat.lower() for caveat in section.caveats)


async def test_a_keyless_or_failed_model_is_reported_not_faked():
    keyless, _ = await _narrate({"available": False, "reason": "no_api_key_for_role_investigation_reasoning"})
    assert keyless.available is False
    assert keyless.reason == "no_api_key_for_role_investigation_reasoning"
    assert any("deterministic" in caveat.lower() for caveat in keyless.caveats)
    assert keyless.summary == ""

    failed, _ = await _narrate({"available": False, "reason": "invocation_failed: timeout"})
    assert failed.available is False and failed.reason == "invocation_failed: timeout"
    assert failed.summary == ""


async def test_a_model_that_returns_junk_is_not_an_exception_for_the_caller():
    section, _ = await _narrate({"available": True, "content": "{not json at all"})
    assert section.available is False and section.reason == "narrative_unparseable"


async def test_the_narrative_contract_can_carry_no_evidence_of_its_own():
    """The model authors prose; structure comes from the deterministic pipeline.

    If a future field let the model carry a pointer, relationship or criminal
    status, it could invent evidence that the orchestrator never computed. The
    field set is therefore pinned: prose and explicit lists of prose only.
    """
    from app.investigator.schemas import InvestigatorNarrative, ModelSection

    expected = {
        "summary", "observation", "interpretation", "assessment", "convergence_note",
        "caveats", "suggested_next_actions",
    }
    assert set(InvestigatorNarrative.model_fields) == expected
    allowed = expected | {"available", "role", "model", "reason", "language_edits"}
    assert set(ModelSection.model_fields) == allowed
    for field in ("provenance", "evidence", "relationships", "hypotheses", "criminal_status"):
        assert field not in ModelSection.model_fields


async def test_real_names_are_pseudonymized_out_and_restored_on_the_way_back():
    entities = [_entity("Sana Iyer", "person-1"), _entity("Asha Nair", "person-2")]
    reply = {
        "available": True,
        "model": "stub-model",
        "content": json.dumps({"summary": "PERSON_001 appears with PERSON_002 in the log."}),
    }
    section, gateway = await _narrate(
        reply,
        entities=entities,
        brief="FACT: Sana Iyer called Asha Nair 12 times.",
    )
    prompt = gateway.router.calls[0]["prompt"]
    assert "Sana Iyer" not in prompt and "Asha Nair" not in prompt
    assert "PERSON_001" in prompt and "PERSON_002" in prompt
    assert section.summary == "Sana Iyer appears with Asha Nair in the log."


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


def test_unresolved_question_never_borrows_the_scope_strength(client, investigation_world):
    """A question about people the records do not contain must not be graded STRONG.

    Patterns are properties of the scope, not answers to the question. Without
    this rule any scope holding one STRONG pattern reported "Overall strength
    STRONG (confidence 90%)" for a question that resolved nothing at all — a
    score nobody earned, which is exactly the kind of number the console must
    never show.
    """
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "What connects Zafar Qureshi and Farah Bano to this network?"},
    )
    assert body["entities"], "the mentions should still be recorded"
    assert all(not entity["resolved"] for entity in body["entities"])
    live = [pattern for pattern in body["patterns"] if not pattern["excluded"]]
    assert live, "the scope must hold patterns for this test to mean anything"

    assessment = body["assessment"]
    assert assessment["overall_strength"] == "INSUFFICIENT"
    assert assessment["overall_confidence"] <= 0.1
    assert any("matched no record" in caveat for caveat in assessment["caveats"])


def test_case_number_scope_reads_the_same_case_as_its_id(client, investigation_world):
    """Scoping by case number must read the case, not an empty snapshot named after it.

    ``require_case`` accepts a human case number, but the scope was built from the
    caller's raw reference, so ``case_id="WLD/2026/1"`` looked up documents and graph
    nodes under that string, found none, and answered "INSUFFICIENT" with a page of
    missing-source gaps about records the case actually holds.
    """
    headers = investigation_world["headers"]
    by_id = _investigate(
        client, headers, {"question": "Who is Sana Iyer?", "case_id": investigation_world["case_id"]}
    )
    by_number = _investigate(
        client, headers, {"question": "Who is Sana Iyer?", "case_id": "WLD/2026/1"}
    )
    assert by_id["scope"]["mode"] == by_number["scope"]["mode"] == "case"
    assert by_number["scope"]["case_id"] == investigation_world["case_id"]
    assert by_number["scope"]["case_number"] == by_id["scope"]["case_number"]
    for field in ("nodes_considered", "edges_considered", "documents_considered"):
        assert by_number["scope"][field] == by_id["scope"][field]
    assert by_number["scope"]["documents_considered"] > 0, "the case must not read as empty"
    assert len(by_number["facts"]) == len(by_id["facts"])
    assert [g["category"] for g in by_number["gaps"]] == [g["category"] for g in by_id["gaps"]]


def test_question_names_resolve_to_canonical_records(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    resolved = {e["display_name"]: e for e in body["entities"] if e["resolved"]}
    assert {"Sana Iyer", "Asha Nair"} <= set(resolved)
    assert all(resolved[name]["canonical_id"] for name in ("Sana Iyer", "Asha Nair"))


def test_a_question_opening_with_a_name_still_resolves_it(client, investigation_world):
    """The most natural phrasing must not cost the investigator the analysis.

    "Are Sana Iyer and Asha Nair connected?" opens with the auxiliary, so this
    pins the whole path: extraction, resolution, and the absence of a data gap
    about the question word itself.
    """
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Are Sana Iyer and Asha Nair connected?", "max_patterns": 3},
    )
    resolved = {e["display_name"]: e for e in body["entities"] if e["resolved"]}
    assert {"Sana Iyer", "Asha Nair"} <= set(resolved), body["entities"]
    assert body["assessment"]["overall_strength"] != "INSUFFICIENT"
    # The question itself naturally contains the phrase; the *findings* must not.
    mentioned = " ".join(
        [*[g["description"] for g in body["gaps"]],
         *[e["display_name"] for e in body["entities"]],
         *[e.get("ambiguity_note") or "" for e in body["entities"]],
         *[s["action"] for s in body["next_steps"]]]
    )
    assert "Are Sana Iyer" not in mentioned, mentioned
    assert "matches no in-scope record" not in " ".join(g["description"] for g in body["gaps"])


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


def test_followup_that_names_nobody_continues_the_thread_entities(
    client, investigation_world
):
    """A pronoun follow-up must not silently analyse the whole scope.

    "Do they share any vehicle?" used to resolve nothing while still scoring the
    scope's own patterns, so the console showed a confident number for a
    question nothing had been examined for. The follow-up now continues with the
    entities the thread established — and says so.
    """
    headers = investigation_world["headers"]
    first = _investigate(client, headers, {"question": "What about Sana Iyer and Asha Nair?"})
    assert all(entity["resolved"] for entity in first["entities"])

    second = _investigate(
        client,
        headers,
        {"question": "Do they share any vehicle?", "investigation_id": first["investigation_id"]},
    )
    names = {entity["display_name"] for entity in second["entities"]}
    assert names == {"Sana Iyer", "Asha Nair"}
    assert all(entity["matched_by"] == "thread-continuation" for entity in second["entities"])
    assert any("No entity was named" in caveat for caveat in second["assessment"]["caveats"])
    assert second["assessment"]["overall_strength"] != "STRONG"


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


def test_a_model_narrative_cannot_add_evidence_to_a_live_answer(
    client, investigation_world, monkeypatch
):
    """The model's prose may reach the investigator; its claims may not become facts.

    A narrative is stubbed in that names a document the pipeline never read and
    accuses the pair. The answer must still carry only the deterministic
    evidence, and the accusation must arrive neutralized.
    """
    from app.ai.gateway import get_ai_gateway
    from app.investigator import orchestrator as ORCH

    reply = {
        "available": True,
        "model": "stub-model",
        "content": json.dumps(
            {
                "summary": "Both men are guilty: the mastermind is Sana Iyer.",
                "observation": "See DOC-9999, which proves the criminal network.",
                "assessment": "Certain.",
                "caveats": [],
                "suggested_next_actions": ["Arrest the culprit."],
            }
        ),
    }
    stub = _gateway(reply)
    monkeypatch.setattr(ORCH, "get_ai_gateway", lambda: stub)

    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Are Sana Iyer and Asha Nair connected?", "max_patterns": 3},
    )
    model = body["assessment"]["model"]
    assert model["available"] is True
    prose = " ".join(
        [model["summary"], model["observation"], model["assessment"], *model["suggested_next_actions"]]
    )
    assert PR.find_guilt_terms(prose) == [], prose
    assert "DOC-9999" in prose  # the model's own text is quoted, not laundered away
    refs = json.dumps(body["provenance"]) + json.dumps([p["provenance"] for p in body["patterns"]])
    assert "DOC-9999" not in refs, "a made-up reference must never become provenance"
    for pointer in body["provenance"]:
        if pointer["kind"] == "document":
            res = client.get(f"/api/v1/documents/{pointer['doc_id']}", headers=investigation_world["headers"])
            assert res.status_code == 200, pointer
    monkeypatch.setattr(ORCH, "get_ai_gateway", get_ai_gateway)


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


# --------------------------------------------------------------------------- #
# Part 3: the investigation objective, facts, alternatives, scope identity
# --------------------------------------------------------------------------- #

def test_objective_defaults_to_the_opening_question(client, investigation_world):
    question = "Is there any connection between Sana Iyer and Asha Nair?"
    body = _investigate(client, investigation_world["headers"], {"question": question})
    assert body["objective"] == question


def test_objective_can_be_stated_explicitly(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {
            "question": "Is there any connection between Sana Iyer and Asha Nair?",
            "objective": "Determine whether Sana Iyer and Asha Nair have a meaningful relationship.",
        },
    )
    assert body["objective"].startswith("Determine whether")


def test_objective_is_sticky_across_the_thread(client, investigation_world):
    """A follow-up stays inside the same investigation objective."""
    headers = investigation_world["headers"]
    first = _investigate(
        client,
        headers,
        {
            "question": "Is there any connection between Sana Iyer and Asha Nair?",
            "objective": "Determine whether the pair have a meaningful operational relationship.",
        },
    )
    second = _investigate(
        client,
        headers,
        {
            "question": "What evidence supports that?",
            "investigation_id": first["investigation_id"],
        },
    )
    assert second["investigation_id"] == first["investigation_id"]
    assert second["objective"] == first["objective"]
    assert second["question"] == "What evidence supports that?"


def test_objective_can_be_replaced_on_a_later_turn(client, investigation_world):
    headers = investigation_world["headers"]
    first = _investigate(client, headers, {"question": "Who is Sana Iyer?"})
    second = _investigate(
        client,
        headers,
        {
            "question": "Who is Asha Nair?",
            "investigation_id": first["investigation_id"],
            "objective": "Compare the two named people across the active dataset.",
        },
    )
    assert second["objective"].startswith("Compare the two named people")


def test_scope_carries_its_human_identity(client, investigation_world):
    headers = investigation_world["headers"]
    master = _investigate(client, headers, {"question": "Who is Sana Iyer?"})
    assert master["scope"]["mode"] == "master"
    assert master["scope"]["label"] == "Master Network"
    assert master["scope"]["dataset_name"]
    assert master["scope"]["case_number"] is None

    case = _investigate(
        client,
        headers,
        {"question": "Summarise the case.", "case_id": investigation_world["case_id"]},
    )
    assert case["scope"]["mode"] == "case"
    assert case["scope"]["case_number"] == "WLD/2026/1"
    assert case["scope"]["label"] == "Case WLD/2026/1"
    assert case["scope"]["case_title"] == "World case one"


def test_independent_sources_count_records_not_metrics():
    """Source counting must not be fooled by pointers that are not records.

    Dressing a dataset-level row up as a document invented a source that did
    not exist; then dropping it from the count entirely made a pair look like it
    rested on nothing. Only records count, one per record set, and never a
    computed metric.
    """
    from app.investigator.hypotheses import _pair_docs

    document = EvidenceItem(
        kind="document",
        summary="Recorded in FIR_001.pdf.",
        provenance=[EV.doc_pointer(doc_id="doc-1", label="FIR_001.pdf")],
    )
    operational = EvidenceItem(
        kind="relationship",
        summary="Two records in one operational table.",
        provenance=[
            EV.dataset_pointer(dataset_id="ds-1", label="dataset:ds-1"),
            EV.metric_pointer(name="case-span:ds-1:ACCOUNT:A1", label="spans 3 cases"),
        ],
    )
    same_file = EvidenceItem(
        kind="relationship",
        summary="Another row of the same file.",
        provenance=[
            EV.source_row_pointer(origin_file="cdr.csv", row_number=11, label="cdr.csv · row 11"),
            EV.source_row_pointer(origin_file="cdr.csv", row_number=12, label="cdr.csv · row 12"),
        ],
    )

    class _Item:
        """The slice of a finding ``_pair_docs`` reads."""

        def __init__(self, *evidence: EvidenceItem) -> None:
            self.evidence = list(evidence)

    assert _pair_docs([_Item(document)]) == {"doc-1"}
    assert _pair_docs([_Item(operational)]) == {"dataset:ds-1"}, "a metric is not a source"
    assert _pair_docs([_Item(same_file)]) == {"cdr.csv"}, "two rows of one file are one origin"
    assert len(_pair_docs([_Item(document), _Item(operational), _Item(same_file)])) == 3


def test_scope_facts_are_not_sold_as_facts_about_the_named_entity():
    """The opening facts must be about the subject of the question.

    A question naming one person in a scope whose cross-case patterns concern
    other people's addresses opened with those addresses: true of the scope,
    but an answer about somebody else reads as though it had answered.
    """
    from app.investigator.orchestrator import collect_facts
    from app.investigator.schemas import SuspiciousPattern

    def pattern(key: str, summary: str) -> SuspiciousPattern:
        return SuspiciousPattern(
            kind="CROSS_CASE_ENTITY",
            title=summary,
            explanation=summary,
            entity_keys=[key],
            evidence=[
                EvidenceItem(kind="record", summary=summary, inference_label="FACT")
            ],
        )

    mine = pattern("k1", "The named person appears in 3 cases.")
    elsewhere = pattern("k9", "An unrelated address appears in 9 cases.")

    named = collect_facts([], [mine, elsewhere], [], entity_keys={"k1"})
    assert [fact.summary for fact in named] == ["The named person appears in 3 cases."]

    # With nobody named the scope itself is the subject, so scope facts stay.
    whole_scope = collect_facts([], [mine, elsewhere], [], entity_keys=set())
    assert len(whole_scope) == 2


def test_facts_are_grounded_in_the_answers_own_evidence(client, investigation_world):
    """Every FACT must already appear in the structured evidence it summarises."""
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    assert body["facts"], "world produces no facts at all"
    pool = {
        evidence["summary"]
        for relationship in body["relationships"]
        for evidence in relationship["evidence"]
    } | {
        evidence["summary"]
        for pattern in body["patterns"]
        if not pattern["excluded"]
        for evidence in pattern["evidence"]
    } | {
        evidence["summary"]
        for hypothesis in body["hypotheses"]
        for evidence in hypothesis["supporting"]
    }
    for fact in body["facts"]:
        assert fact["inference_label"] == "FACT"
        assert fact["summary"] in pool, fact["summary"]


def test_facts_are_deduplicated(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    summaries = [fact["summary"] for fact in body["facts"]]
    assert len(summaries) == len(set(summaries))


def test_alternative_explanations_are_never_empty(client, investigation_world):
    """The weakest honest reading must always be present."""
    body = _investigate(client, investigation_world["headers"], {"question": "Who is Sana Iyer?"})
    assert body["alternative_explanations"]
    assert all(item.strip() for item in body["alternative_explanations"])
    assert len(body["alternative_explanations"]) == len(set(body["alternative_explanations"]))


def test_alternatives_come_from_the_detectors_not_the_model(client, investigation_world):
    """Alternatives are deterministic: they exist keylessly and are grounded."""
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    assert body["assessment"]["model"]["available"] is False
    assert body["alternative_explanations"]
    grounded = {
        alternative
        for hypothesis in body["hypotheses"]
        for alternative in hypothesis["innocent_alternatives"]
    } | {
        alternative
        for pattern in body["patterns"]
        if not pattern["excluded"]
        for alternative in pattern["innocent_alternatives"]
    }
    for alternative in body["alternative_explanations"]:
        assert alternative in grounded, alternative


def test_new_fields_carry_no_guilt_vocabulary(client, investigation_world):
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "Is there any connection between Sana Iyer and Asha Nair?"},
    )
    text = json.dumps(
        {
            "objective": body["objective"],
            "facts": body["facts"],
            "alternatives": body["alternative_explanations"],
            "scope": body["scope"],
        }
    ).lower()
    hits = [term for term in GUILT_TERMS if re.search(r"\b" + re.escape(term) + r"\b", text)]
    assert hits == []


def test_analysis_is_deterministic_for_the_same_question(client, investigation_world):
    """Same question, same dataset, same structured answer (no model involved)."""
    headers = investigation_world["headers"]
    question = "Is there any connection between Sana Iyer and Asha Nair?"
    first = _investigate(client, headers, {"question": question})
    second = _investigate(client, headers, {"question": question})
    for key in ("patterns", "hypotheses", "facts", "alternative_explanations", "entities"):
        assert first[key] == second[key], key
    assert first["assessment"]["overall_strength"] == second["assessment"]["overall_strength"]


def test_pattern_scan_reports_scope_and_set_asides(client, investigation_world):
    headers = investigation_world["headers"]
    res = client.get("/api/v1/investigate/patterns", headers=headers)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["mode"] == "master"
    assert payload["scope_label"] == "Master Network"
    assert payload["dataset_name"]
    assert payload["count"] == len(payload["patterns"])
    assert payload["excluded_count"] == sum(1 for p in payload["patterns"] if p["excluded"])

    res = client.get(
        f"/api/v1/investigate/patterns?case_id={investigation_world['case_id']}", headers=headers
    )
    assert res.json()["scope_label"] == "Case WLD/2026/1"
    assert res.json()["case_number"] == "WLD/2026/1"


def test_criminal_status_is_echoed_never_derived(client, investigation_world):
    """A signal-heavy answer must not manufacture a criminal status.

    Ravi Mehta carries a recorded conviction, so the field is populated from
    the dataset. Sana Iyer is the most central, most patterned entity in the
    world and has no recorded status — the answer must leave it empty rather
    than letting the signals fill it in.
    """
    body = _investigate(
        client,
        investigation_world["headers"],
        {"question": "What links Sana Iyer, Asha Nair and Ravi Mehta?"},
    )
    statuses = {e["display_name"]: e.get("criminal_status") for e in body["entities"]}
    assert statuses.get("Ravi Mehta") == "convicted"
    assert statuses.get("Sana Iyer") in (None, "")

    signal_entities = {
        name
        for pattern in body["patterns"]
        if not pattern["excluded"]
        for name in pattern["entities"]
    }
    assert signal_entities, "no live signal to test against"
    for entity in body["entities"]:
        if entity["display_name"] not in signal_entities:
            continue
        if entity["display_name"] == "Ravi Mehta":
            continue
        assert entity.get("criminal_status") in (None, ""), (
            f"{entity['display_name']} acquired a criminal status from a signal"
        )
