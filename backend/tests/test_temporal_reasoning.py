"""Temporal reasoning: real chronology for real temporal questions.

"What happened before the incident?" must never be answered with a case
summary, "after the payment" must identify the payment first, and no answer may
invent a date the records do not carry.
"""

from __future__ import annotations

from datetime import datetime

from app.ai.claims import EntityVocabulary, extract_claims_from_documents
from app.ai.evidence_boundary import build_evidence_boundary
from app.ai.query_planner import plan_query
from app.ai.response_composer import deterministic_fallback
from app.ai.temporal import (
    RELATION_AFTER,
    RELATION_AROUND,
    RELATION_BEFORE,
    RELATION_BETWEEN,
    RELATION_NEAREST,
    events_from_claims,
    events_from_graph,
    merge_events,
    overlapping_events,
    parse_temporal_query,
    select_temporal_events,
)

NODES = [
    {"provenance_key": "person:1", "label": "Person", "properties": {"name": "Anjali Hussain"}},
    {"provenance_key": "person:2", "label": "Person", "properties": {"name": "Dinesh Malhotra"}},
    {"provenance_key": "location:1", "label": "Location", "properties": {"address": "Central Market"}},
]

INCIDENT = "2025-06-12T21:00:00+00:00"

DOCUMENTS = [
    {
        "doc_id": "DIARY-01",
        "filename": "diary.txt",
        "document_type": "CASE_DIARY",
        "content": "\n".join(
            [
                "Incident: 2025-06-12T21:00:00+00:00",
                "Anjali Hussain was at Central Market at 18:00 on 10 June 2025.",
                "Dinesh Malhotra was at Central Market at 19:30 on 11 June 2025.",
                "2025-06-13T09:00:00+00:00 statement of the witness recorded by the IO.",
            ]
        ),
    },
    {
        "doc_id": "FIN-02",
        "filename": "financial.csv",
        "document_type": "FINANCIAL",
        "content": "\n".join(
            [
                "txn_id,from_account,to_account,timestamp,amount,narrative",
                "TXN-1,50001,50002,2025-06-11T14:00:00+00:00,250000,Transfer per transaction record",
            ]
        ),
    },
]


def _events():
    vocabulary = EntityVocabulary.from_nodes(NODES)
    claims = extract_claims_from_documents(DOCUMENTS, vocabulary=vocabulary)
    graph = events_from_graph(
        [
            {
                "type": "edge",
                "timestamp": "2025-06-10T20:00:00+00:00",
                "source_key": "person:1",
                "target_key": "person:2",
                "rel_type": "CALLED",
                "properties": {"source_doc_ids": ["CDR-03"]},
            }
        ],
        key_to_label={"person:1": "Anjali Hussain", "person:2": "Dinesh Malhotra"},
    )
    return claims, merge_events(events_from_claims(claims), graph)


# --------------------------------------------------------------------------- #
# 17. Before an event
# --------------------------------------------------------------------------- #

def test_events_before_the_incident_are_selected_and_ordered():
    _, events = _events()
    selection = select_temporal_events(
        "What happened before the incident?", events, case_incident_time=INCIDENT
    )
    assert selection.relation == RELATION_BEFORE
    assert selection.anchor_time == INCIDENT
    assert selection.events, "dated records before the incident must be selected"
    timestamps = [event.timestamp for event in selection.events]
    assert timestamps == sorted(timestamps)
    assert all(timestamp < INCIDENT for timestamp in timestamps)


# --------------------------------------------------------------------------- #
# 18. After an anchor event
# --------------------------------------------------------------------------- #

def test_events_after_the_payment_are_anchored_on_the_payment():
    _, events = _events()
    selection = select_temporal_events("What happened after the payment?", events)
    assert selection.relation == RELATION_AFTER
    assert "payment" in selection.anchor_label.lower()
    assert selection.anchor_time, "the financial record must provide the anchor"
    assert selection.anchor_time.startswith("2025-06-11T14:00")
    assert all(event.timestamp > selection.anchor_time for event in selection.events)


# --------------------------------------------------------------------------- #
# 19. Between two dates
# --------------------------------------------------------------------------- #

def test_events_between_two_dates():
    _, events = _events()
    question = "What happened between 10 June 2025 and 11 June 2025?"
    query = parse_temporal_query(question)
    assert query.relation == RELATION_BETWEEN
    selection = select_temporal_events(question, events)
    assert selection.window_start and selection.window_end
    assert selection.events
    for event in selection.events:
        assert selection.window_start <= event.timestamp <= selection.window_end


# --------------------------------------------------------------------------- #
# 20. Nearest event
# --------------------------------------------------------------------------- #

def test_nearest_events_are_ranked_by_distance():
    _, events = _events()
    selection = select_temporal_events(
        "Which events happened closest to the incident?", events, case_incident_time=INCIDENT
    )
    assert selection.relation == RELATION_NEAREST
    assert selection.events
    deltas = []
    for event in selection.events:
        parsed = datetime.fromisoformat(event.timestamp)
        anchor = datetime.fromisoformat(INCIDENT)
        deltas.append(abs((parsed - anchor).total_seconds()))
    assert deltas == sorted(deltas), "nearest-first ordering is required"


# --------------------------------------------------------------------------- #
# 21. Communications around an event
# --------------------------------------------------------------------------- #

def test_communications_around_an_event_use_a_bounded_window():
    _, events = _events()
    # An anchor whose ±6h window really contains the recorded call: the answer
    # is the contact in that window, and nothing outside it.
    selection = select_temporal_events(
        "What communications occurred around the incident?",
        events,
        case_incident_time="2025-06-10T22:00:00+00:00",
    )
    assert selection.relation == RELATION_AROUND
    assert selection.window_start and selection.window_end
    assert selection.events, "the recorded call sits inside this window"
    for event in selection.events:
        assert selection.window_start <= event.timestamp <= selection.window_end

    # An empty window widens to the nearest records, and says that it did —
    # "nothing in the window" must never be presented as "nothing happened".
    empty = select_temporal_events(
        "What communications occurred around the incident?",
        events,
        case_incident_time="2020-01-01T00:00:00+00:00",
    )
    assert empty.events, "the nearest records are shown instead of an empty chronology"
    assert empty.note and "nearest" in empty.note
    assert empty.window_start and empty.window_end
    assert any(
        not (empty.window_start <= event.timestamp <= empty.window_end)
        for event in empty.events
    ), "the widened selection lies outside the requested window"


# --------------------------------------------------------------------------- #
# 22. Chronological ordering and interval overlap
# --------------------------------------------------------------------------- #

def test_chronological_ordering_is_stable():
    _, events = _events()
    selection = select_temporal_events("Give me the chronological sequence of events", events)
    timestamps = [event.timestamp for event in selection.events]
    assert timestamps == sorted(timestamps)


def test_overlapping_intervals_are_detected():
    _, events = _events()
    overlapping = overlapping_events("2025-06-11T00:00:00+00:00", "2025-06-11T23:59:00+00:00", events)
    assert overlapping
    assert all(event.timestamp.startswith("2025-06-11") for event in overlapping)


def test_relative_time_expressions_are_understood():
    query = parse_temporal_query("What happened two days before the incident?")
    assert query.relation == RELATION_BEFORE
    assert query.offset is not None
    assert query.offset.days == 2
    assert "incident" in query.anchor_text.lower()


# --------------------------------------------------------------------------- #
# Answer shape: chronology, citations, no invented dates
# --------------------------------------------------------------------------- #

def test_temporal_answer_is_chronological_and_cited():
    _, events = _events()
    selection = select_temporal_events(
        "What happened before the incident?", events, case_incident_time=INCIDENT
    )
    question = "What happened before the incident?"
    boundary = build_evidence_boundary(
        case_id="case-t1",
        case_number="CR-2020",
        case_title="Tender case",
        case_status="OPEN",
        jurisdiction="METRO-CENTRAL",
        question=question,
        plan=plan_query(question),
        nodes=NODES,
        edges=[],
        documents=DOCUMENTS,
        all_case_document_ids=[d["doc_id"] for d in DOCUMENTS],
        all_case_documents=DOCUMENTS,
        temporal=selection.as_dict(),
    )
    payload = deterministic_fallback(boundary)
    summary = payload["summary"]
    assert "Before" in summary
    assert "12 Jun 2025" in summary, "the anchor date must be stated"
    assert "10 Jun 2025" in summary or "11 Jun 2025" in summary
    assert "[" in summary and "]" in summary, "every event needs its citation"
    positions = [summary.find(day) for day in ("10 Jun 2025", "11 Jun 2025") if day in summary]
    assert positions == sorted(positions)
    for limitation in ("not proof",):
        assert limitation not in summary.lower()


def test_empty_window_is_reported_honestly():
    _, events = _events()
    selection = select_temporal_events(
        "What happened before the incident?",
        events,
        case_incident_time="2020-01-01T00:00:00+00:00",
    )
    assert selection.events == []
    assert "no dated events" in selection.note
    question = "What happened before the incident?"
    boundary = build_evidence_boundary(
        case_id="case-t1",
        case_number="CR-2020",
        case_title="Tender case",
        case_status="OPEN",
        jurisdiction="METRO-CENTRAL",
        question=question,
        plan=plan_query(question),
        nodes=NODES,
        edges=[],
        documents=DOCUMENTS,
        all_case_document_ids=[d["doc_id"] for d in DOCUMENTS],
        all_case_documents=DOCUMENTS,
        temporal=selection.as_dict(),
    )
    summary = deterministic_fallback(boundary)["summary"]
    assert "no dated case record falls in that window" in summary
    assert "in total" in summary, "the honest denominator must be stated"


def test_planner_marks_temporal_shape_for_the_new_questions():
    cases = {
        "What happened before the incident?": ("TIMELINE", "BEFORE"),
        "What happened after the payment?": ("TIMELINE", "AFTER"),
        "Which events happened closest to the incident?": ("TIMELINE", "NEAREST"),
        "What happened between May 25 and June 1?": ("TIMELINE", "BETWEEN"),
        "What communications occurred around the incident?": ("COMMUNICATION", "AROUND"),
    }
    for question, (intent, relation) in cases.items():
        plan = plan_query(question)
        assert plan.intent == intent, (question, plan.intent)
        assert plan.temporal_relation == relation, (question, plan.temporal_relation)
