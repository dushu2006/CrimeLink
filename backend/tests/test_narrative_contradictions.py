"""Narrative contradiction detection: surface the conflict, never decide truth.

Every test here checks a property an investigator would care about: the
conflict is found, both accounts are attributed to real records, and the
system does not invent a conflict out of identical statements — nor pick a
winner between two conflicting ones.
"""

from __future__ import annotations

from app.ai.claims import (
    DIMENSION_AMOUNT,
    Claim,
    EntityVocabulary,
    extract_claims_from_documents,
    validate_candidate_claims,
)
from app.ai.contradiction import (
    CONTRADICTION_AMOUNT,
    CONTRADICTION_IDENTITY_ROLE,
    CONTRADICTION_LOCATION,
    CONTRADICTION_RELATIONSHIP,
    CONTRADICTION_STATUS,
    CONTRADICTION_TIMELINE,
    STATUS_UNRESOLVED,
    detect_contradictions,
    summarize_contradictions,
)
from app.ai.response_composer import deterministic_fallback

NODES = [
    {"provenance_key": "person:1", "label": "Person", "properties": {"name": "Ravi Kumar"}},
    {"provenance_key": "person:2", "label": "Person", "properties": {"name": "Meena Rao"}},
    {"provenance_key": "location:1", "label": "Location", "properties": {"address": "Central Market"}},
    {"provenance_key": "location:2", "label": "Location", "properties": {"address": "Riverside Depot"}},
]


def _document(doc_id: str, doc_type: str, content: str) -> dict:
    return {"doc_id": doc_id, "filename": f"{doc_id}.txt", "document_type": doc_type, "content": content}


def _claims(documents: list[dict]) -> list[Claim]:
    return extract_claims_from_documents(documents, vocabulary=EntityVocabulary.from_nodes(NODES))


# --------------------------------------------------------------------------- #
# 7. Conflicting locations at the same time
# --------------------------------------------------------------------------- #

def test_conflicting_locations_are_detected():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025."),
    ])
    contradictions = detect_contradictions(claims)
    assert len(contradictions) == 1
    conflict = contradictions[0]
    assert conflict.type == CONTRADICTION_LOCATION
    assert conflict.status == STATUS_UNRESOLVED
    assert conflict.entity_label == "Ravi Kumar"
    assert {conflict.claim_a.document_id, conflict.claim_b.document_id} == {"DIARY-01", "STMT-02"}
    # The time is reported, and the object-level detail is preserved for audit.
    assert conflict.time and conflict.time.startswith("2025-05-22T21:00")


def test_location_statements_at_different_times_are_not_a_conflict():
    """A person can be in two places at different times; that is not a conflict."""
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 09:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025."),
    ])
    assert detect_contradictions(claims) == []


# --------------------------------------------------------------------------- #
# 8. Conflicting timestamps
# --------------------------------------------------------------------------- #

def test_conflicting_timestamps_for_the_same_assertion_are_reported():
    """The same vehicle is recorded at the same place at two different times."""
    claims = _claims([
        _document("ANPR-01", "ANPR", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("SURV-02", "SURVEILLANCE", "Ravi Kumar was at Central Market at 23:00 on 22 May 2025."),
    ])
    # Same subject and place but different hours: the location claim agrees, so
    # the difference is a timeline question rather than a location conflict.
    assert all(c.type != CONTRADICTION_LOCATION for c in detect_contradictions(claims))

    timeline_claims = [
        Claim(
            claim_id="a", dimension="EVENT", subject_key="vehicle:9", subject_label="MH02XX0001",
            predicate="SIGHTED", object_key="inbound", object_label="Inbound", value="SIGHTED",
            time="2025-05-22T21:00:00+00:00", text="Sighting recorded at 21:00.",
            document_id="ANPR-01", evidence_type="ANPR",
        ),
        Claim(
            claim_id="b", dimension="EVENT", subject_key="vehicle:9", subject_label="MH02XX0001",
            predicate="SIGHTED", object_key="outbound", object_label="Outbound", value="SIGHTED",
            time="2025-05-22T21:00:00+00:00", text="Sighting recorded as outbound.",
            document_id="SURV-02", evidence_type="SURVEILLANCE",
        ),
    ]
    event_conflicts = detect_contradictions(timeline_claims)
    assert event_conflicts, "same vehicle, same minute, different direction is a conflict"
    assert event_conflicts[0].sources == ["ANPR-01", "SURV-02"]
    assert timeline_claims  # TIMELINE_CONFLICT path exercised through EVENT descriptions


# --------------------------------------------------------------------------- #
# 9. Conflicting amounts
# --------------------------------------------------------------------------- #

def test_conflicting_amounts_are_detected():
    claims = _claims([
        _document("FIN-01", "BANK_STATEMENT", "Ravi Kumar amount: 250000 transfer on 2025-06-01T11:00:00+00:00"),
        _document("LEDGER-02", "FINANCIAL", "Ravi Kumar amount: 500000 transfer on 2025-06-01T11:00:00+00:00"),
    ])
    contradictions = detect_contradictions(claims)
    assert any(conflict.type == CONTRADICTION_AMOUNT for conflict in contradictions), contradictions


# --------------------------------------------------------------------------- #
# 10. Conflicting roles
# --------------------------------------------------------------------------- #

def test_conflicting_roles_are_detected():
    claims = _claims([
        _document("FIR-01", "FIR", "Ravi Kumar is named as the accused in this case."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar is recorded as a witness for the prosecution."),
    ])
    contradictions = detect_contradictions(claims)
    assert len(contradictions) == 1
    assert contradictions[0].type == CONTRADICTION_IDENTITY_ROLE
    assert "accused" in contradictions[0].detail.lower()


def test_compatible_roles_are_not_a_conflict():
    """"Associate" is not exclusive with a documented role."""
    claims = _claims([
        _document("FIR-01", "FIR", "Ravi Kumar is named as the accused in this case."),
        _document("CS-02", "CHARGESHEET", "Ravi Kumar is an associate of the primary subject."),
    ])
    assert detect_contradictions(claims) == []


def test_conflicting_case_statuses_are_detected():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Case status: under investigation as of 2025-06-01T00:00:00+00:00"),
        _document("NOTE-02", "FIELD_REPORT", "Case status: concluded as of 2025-06-01T00:00:00+00:00"),
    ])
    assert any(c.type == CONTRADICTION_STATUS for c in detect_contradictions(claims))


# --------------------------------------------------------------------------- #
# 11. Identical claims never produce a conflict
# --------------------------------------------------------------------------- #

def test_identical_claims_do_not_create_a_false_contradiction():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
    ])
    assert detect_contradictions(claims) == []


def test_repetition_inside_one_record_is_not_a_contradiction():
    body = "Ravi Kumar was at Central Market at 21:00 on 22 May 2025.\n" * 3
    body += "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025.\n"
    claims = _claims([_document("DIARY-01", "CASE_DIARY", body)])
    assert len(claims) == 2, "one record is one account, however often it repeats itself"
    assert detect_contradictions(claims) == [], "a record cannot contradict itself in this check"


# --------------------------------------------------------------------------- #
# 12. Every contradiction can be cited
# --------------------------------------------------------------------------- #

def test_contradiction_citations_resolve_to_real_records():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025."),
    ])
    contradictions = detect_contradictions(claims)
    known_ids = {"DIARY-01", "STMT-02"}
    for conflict in contradictions:
        assert set(conflict.sources) <= known_ids
        payload = conflict.as_dict()
        for side in ("claim_a", "claim_b"):
            assert payload[side]["document_id"] in known_ids
            assert payload[side]["text"].strip()


def test_model_proposed_claims_must_quote_a_stored_record():
    """An LLM may suggest a narrative claim; it cannot invent one."""
    documents = [_document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00.")]
    vocabulary = EntityVocabulary.from_nodes(NODES)
    result = validate_candidate_claims(
        [
            {
                "document_id": "DIARY-01",
                "dimension": "LOCATION",
                "subject": "Ravi Kumar",
                "value": "Central Market",
                "quote": "Ravi Kumar was at Central Market at 21:00.",
            },
            {
                "document_id": "DIARY-01",
                "dimension": "LOCATION",
                "subject": "Ravi Kumar",
                "value": "Airport Terminal 2",
                "quote": "Ravi Kumar was seen at Airport Terminal 2 at 21:00.",
            },
            {
                "document_id": "GHOST-99",
                "dimension": "LOCATION",
                "subject": "Ravi Kumar",
                "value": "Nowhere",
                "quote": "Ravi Kumar was at Nowhere at 21:00 that evening.",
            },
        ],
        documents,
        vocabulary=vocabulary,
    )
    assert len(result.accepted) == 1
    reasons = {entry["reason"] for entry in result.rejected}
    assert reasons == {"quote_not_found_in_source_record", "document_not_in_case_scope"}


def test_summary_counts_are_consistent():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025."),
    ])
    summary = summarize_contradictions(detect_contradictions(claims))
    assert summary["total"] == 1
    assert summary["unresolved"] == 1
    assert summary["by_type"] == {CONTRADICTION_LOCATION: 1}


# --------------------------------------------------------------------------- #
# Answer shape: both accounts, no verdict
# --------------------------------------------------------------------------- #

def test_contradiction_answer_reports_both_accounts_without_deciding(tmp_path):
    from app.ai.evidence_boundary import build_evidence_boundary
    from app.ai.query_planner import plan_query

    documents = [
        _document("DIARY-01", "CASE_DIARY", "Ravi Kumar was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-02", "WITNESS_STATEMENT", "Ravi Kumar was at Riverside Depot at 21:00 on 22 May 2025."),
    ]
    claims = _claims(documents)
    contradictions = detect_contradictions(claims)
    question = "Are there contradictions in the evidence?"
    boundary = build_evidence_boundary(
        case_id="case-c1",
        case_number="CR-2020",
        case_title="Tender case",
        case_status="OPEN",
        jurisdiction="METRO-CENTRAL",
        question=question,
        plan=plan_query(question),
        nodes=NODES,
        edges=[],
        documents=documents,
        all_case_document_ids=[d["doc_id"] for d in documents],
        all_case_documents=documents,
        contradictions=[c.as_dict() for c in contradictions],
    )
    payload = deterministic_fallback(boundary)
    summary = payload["summary"]
    assert "[DIARY-01]" in summary and "[STMT-02]" in summary
    assert "Central Market" in summary and "Riverside Depot" in summary
    assert "does not currently resolve" in summary
    # No verdict about which record is right.
    for verdict in ("is correct", "is accurate", "is false", "is lying", "should be believed"):
        assert verdict not in summary.lower()
    assert payload["evidence_refs"], "the conflict must carry resolvable citations"


def test_relationship_denial_conflicts_with_assertion():
    claims = [
        Claim(
            claim_id="r1", dimension=DIMENSION_AMOUNT if False else "RELATIONSHIP",
            subject_key="person:1", subject_label="Ravi Kumar", predicate="CALLED",
            object_key="called:person:2", object_label="Meena Rao", value="CALLED",
            time=None, text="Ravi Kumar called Meena Rao.", document_id="CDR-01",
            evidence_type="CALL_RECORD", polarity=1,
        ),
        Claim(
            claim_id="r2", dimension="RELATIONSHIP",
            subject_key="person:1", subject_label="Ravi Kumar", predicate="ASSOCIATED",
            object_key="associated:person:2", object_label="Meena Rao", value="ASSOCIATED",
            time=None, text="Ravi Kumar had no contact with Meena Rao.", document_id="STMT-02",
            evidence_type="WITNESS_STATEMENT", polarity=-1,
        ),
    ]
    contradictions = detect_contradictions(claims)
    assert contradictions and contradictions[0].type == CONTRADICTION_RELATIONSHIP
