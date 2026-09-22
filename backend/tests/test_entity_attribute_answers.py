"""Direct entity-attribute answers: the precise question, the precise answer.

"What is Dinesh Malhotra's phone number?" must return the recorded number,
cited, and nothing else — no case summary, no evidence inventory, no
template.  The values must come verbatim from the case-scoped records, which
is why the lookup path is deterministic even when a model is configured.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.evidence_boundary import EvidenceBoundary, build_evidence_boundary
from app.ai.gateway import AIGateway
from app.ai.query_planner import (
    INTENT_ENTITY_LOOKUP,
    plan_query,
    resolve_plan_entities,
)
from app.ai.response_composer import (
    attribute_answer,
    deterministic_fallback,
)
from app.config import Settings


CASE_ID = "case-test-001"


def _nodes() -> list[dict]:
    return [
        {"provenance_key": "person:1", "label": "Person",
         "properties": {"name": "Anjali Hussain", "role": "Witness",
                        "case_ids": [CASE_ID], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "person:2", "label": "Person",
         "properties": {"name": "Dinesh Malhotra", "role": "Accused",
                        "case_ids": [CASE_ID], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "phone:1", "label": "Phone",
         "properties": {"number": "+919812345670", "case_ids": [CASE_ID],
                        "source_doc_ids": ["CDR-001"]}},
        {"provenance_key": "account:1", "label": "BankAccount",
         "properties": {"account_number": "50100234567890", "bank_name": "SBI",
                        "case_ids": [CASE_ID], "source_doc_ids": ["BANK-003"]}},
        {"provenance_key": "vehicle:1", "label": "Vehicle",
         "properties": {"plate": "RJ14AB1234", "case_ids": [CASE_ID],
                        "source_doc_ids": ["FIR-001"]}},
    ]


def _edges() -> list[dict]:
    return [
        {"source_key": "person:2", "target_key": "person:1", "rel_type": "CALLED",
         "confidence": 0.9, "timestamp": "2020-03-11T20:14:00Z",
         "source_doc_ids": ["CDR-001"], "call_count": 14, "case_ids": [CASE_ID]},
        {"source_key": "person:2", "target_key": "phone:1", "rel_type": "USES_PHONE",
         "confidence": 0.95, "source_doc_ids": ["CDR-001"], "case_ids": [CASE_ID]},
        {"source_key": "person:2", "target_key": "account:1", "rel_type": "OWNS_ACCOUNT",
         "confidence": 0.9, "source_doc_ids": ["BANK-003"], "case_ids": [CASE_ID]},
        {"source_key": "person:2", "target_key": "vehicle:1", "rel_type": "USES_VEHICLE",
         "confidence": 0.9, "source_doc_ids": ["FIR-001"], "case_ids": [CASE_ID]},
    ]


def _documents() -> list[dict]:
    return [
        {"doc_id": "FIR-001", "filename": "FIR_001.txt", "document_type": "FIR",
         "content": "Incident date: 2020-03-14. FIR naming Dinesh Malhotra as accused, "
                    "Anjali Hussain as witness.",
         "case_ids": [CASE_ID]},
        {"doc_id": "CDR-001", "filename": "CDR.csv", "document_type": "CALL_RECORD",
         "content": "14 calls between the two numbers.", "case_ids": [CASE_ID]},
        {"doc_id": "BANK-003", "filename": "BANK.csv", "document_type": "BANK_STATEMENT",
         "content": "Transfer of Rs 4,50,000.", "case_ids": [CASE_ID]},
    ]


def _boundary(question: str) -> EvidenceBoundary:
    plan = plan_query(question, known_person_names=["Anjali Hussain", "Dinesh Malhotra"])
    plan = resolve_plan_entities(plan, _nodes())
    return build_evidence_boundary(
        case_id=CASE_ID,
        case_number="CR-2020",
        case_title="Suspected extortion",
        case_status="UNDER_INVESTIGATION",
        jurisdiction="RJ-JAIPUR",
        question=question,
        plan=plan,
        nodes=_nodes(),
        edges=_edges(),
        documents=_documents(),
        all_case_document_ids=[d["doc_id"] for d in _documents()],
        available_evidence_types=["BANK_STATEMENT", "CALL_RECORD", "FIR"],
        missing_evidence_types=["CHARGESHEET"],
        case_stats={"case_id": CASE_ID, "evidence_count": 3, "entity_count": 5,
                    "person_count": 2, "relationship_count": 4},
    )


# ---------------------------------------------------------------------------
# Planner: attribute questions get the entity-lookup intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "attribute"),
    [
        ("What is Dinesh Malhotra's phone number?", "phone"),
        ("What is the phone number of Dinesh?", "phone"),
        ("What vehicle is associated with Dinesh?", "vehicle"),
        ("What is the account number of Dinesh?", "account"),
        ("Where does Dinesh live?", "address"),
        ("What is the FIR number?", "fir_number"),
        ("What is the case status?", "case_status"),
        ("Who is Dinesh Malhotra?", "identity"),
    ],
)
def test_planner_marks_attribute_questions(question: str, attribute: str):
    plan = plan_query(question)
    assert plan.intent == INTENT_ENTITY_LOOKUP
    assert plan.requested_attribute == attribute


# ---------------------------------------------------------------------------
# Deterministic answers: exact values, cited, nothing else
# ---------------------------------------------------------------------------


def test_phone_number_answer_is_exact_and_cited():
    question = "What is Dinesh Malhotra's phone number?"
    boundary = _boundary(question)
    plan = plan_query(question, known_person_names=["Anjali Hussain", "Dinesh Malhotra"])
    payload = attribute_answer(boundary, plan)
    assert payload is not None
    assert "+919812345670" in payload["summary"]
    assert "[CDR-001]" in payload["summary"]
    # No generic case-summary padding.
    assert "evidence type" not in payload["summary"].lower()
    assert "relationships" not in payload["summary"].lower()
    assert payload["claims"] and payload["claims"][0]["evidence_refs"]


def test_identity_answer_names_the_documented_role_not_guilt():
    question = "Who is Dinesh Malhotra?"
    boundary = _boundary(question)
    plan = plan_query(question, known_person_names=["Anjali Hussain", "Dinesh Malhotra"])
    payload = attribute_answer(boundary, plan)
    assert payload is not None
    assert "Dinesh Malhotra" in payload["summary"]
    assert "accused" in payload["summary"].lower()
    # Never convert "accused" into "criminal"/"guilty".
    lowered = payload["summary"].lower()
    assert "criminal" not in lowered and "guilty" not in lowered
    # And the documented associations come along, cited.
    assert "+919812345670" in payload["summary"]
    assert "[CDR-001]" in payload["summary"]


def test_fir_number_answer_uses_case_metadata():
    question = "What is the FIR number?"
    boundary = _boundary(question)
    plan = plan_query(question)
    payload = attribute_answer(boundary, plan)
    assert payload is not None
    assert "CR-2020" in payload["summary"]
    assert "FIR-001" in payload["summary"]


def test_case_status_answer_uses_case_metadata():
    question = "What is the case status?"
    boundary = _boundary(question)
    plan = plan_query(question)
    payload = attribute_answer(boundary, plan)
    assert payload is not None
    assert "UNDER_INVESTIGATION" in payload["summary"]
    assert "CR-2020" in payload["summary"]


def test_incident_date_is_read_from_the_record_not_invented():
    question = "What is the date of the incident?"
    boundary = _boundary(question)
    plan = plan_query(question)
    payload = attribute_answer(boundary, plan)
    assert payload is not None
    assert "2020-03-14" in payload["summary"]


def test_missing_attribute_is_a_scoped_absence_not_a_case_summary():
    question = "What is Anjali Hussain's phone number?"
    boundary = _boundary(question)
    plan = plan_query(question, known_person_names=["Anjali Hussain", "Dinesh Malhotra"])
    payload = attribute_answer(boundary, plan, allow_absence=True)
    assert payload is not None
    assert "no case record" in payload["summary"].lower()
    assert "Anjali Hussain" in payload["summary"]
    # Absence is not proof of absence — the wording must keep that honest.
    assert "not proof" in payload["summary"].lower()


def test_unknown_person_is_not_substituted_with_someone_else():
    question = "What is Suresh Kumar's phone number?"
    boundary = _boundary(question)
    plan = plan_query(question, known_person_names=["Anjali Hussain", "Dinesh Malhotra"])
    payload = attribute_answer(boundary, plan, allow_absence=True)
    assert payload is not None
    assert "Suresh" in payload["summary"]
    # Must NOT answer with Dinesh's phone number.
    assert "+919812345670" not in payload["summary"]


def test_no_model_deterministic_fallback_also_answers_directly():
    question = "What is Dinesh Malhotra's phone number?"
    boundary = _boundary(question)
    payload = deterministic_fallback(boundary)
    assert "+919812345670" in payload["summary"]
    assert "[CDR-001]" in payload["summary"]


# ---------------------------------------------------------------------------
# Gateway level: the deterministic path runs end-to-end without any model
# ---------------------------------------------------------------------------


async def test_gateway_answers_attribute_question_without_a_model():
    settings = Settings(ai_allow_raw_pii=True, ai_pseudonymize=False)
    mock_router = MagicMock()
    mock_router.chat = AsyncMock(return_value={
        "available": False, "reason": "no_api_key_for_role_reasoning",
    })
    gateway = AIGateway(settings=settings, router=mock_router)

    nodes = _nodes()
    edges = _edges()
    docs = _documents()

    gateway._resolve_case_keys = AsyncMock(  # type: ignore[method-assign]
        return_value=(CASE_ID, "CR-2020", {CASE_ID}, SimpleNamespace(
            title="Suspected extortion", status="UNDER_INVESTIGATION",
            jurisdiction_id="RJ-JAIPUR",
        ))
    )
    gateway._get_all_case_nodes = AsyncMock(return_value=nodes)  # type: ignore[method-assign]
    gateway._retrieve_subgraph_multi = AsyncMock(return_value=(nodes, edges))  # type: ignore[method-assign]
    gateway._retrieve_subgraph = AsyncMock(return_value=(nodes, edges))  # type: ignore[method-assign]
    gateway._retrieve_case_documents = AsyncMock(return_value=docs)  # type: ignore[method-assign]

    response = await gateway.ask(
        question="What is Dinesh Malhotra's phone number?",
        case_id=CASE_ID,
    )
    answer = response.finding.direct_answer or response.finding.summary
    assert "+919812345670" in answer
    assert "[CDR-001]" in answer
    assert "evidence type" not in answer.lower()
    # The model was never asked to retype an identifier.
    assert not mock_router.chat.called
    # Presentation carries exactly the backing source.
    sources = (response.finding.presentation or {}).get("sources")
    assert sources and sources[0]["doc_id"] == "CDR-001"
    assert response.context.get("deterministic_attribute_answer") is True


async def test_gateway_attribute_followup_uses_the_previous_turn():
    """The headline regression: phone question about Anjali, then 'What about
    Dinesh?' must be answered with Dinesh's phone number — not with a
    clarification, not with a case summary."""
    settings = Settings(ai_allow_raw_pii=True, ai_pseudonymize=False)
    mock_router = MagicMock()
    mock_router.chat = AsyncMock(return_value={
        "available": False, "reason": "no_api_key_for_role_reasoning",
    })
    gateway = AIGateway(settings=settings, router=mock_router)

    nodes = _nodes()
    edges = _edges()
    docs = _documents()

    gateway._resolve_case_keys = AsyncMock(  # type: ignore[method-assign]
        return_value=(CASE_ID, "CR-2020", {CASE_ID}, SimpleNamespace(
            title="Suspected extortion", status="UNDER_INVESTIGATION",
            jurisdiction_id="RJ-JAIPUR",
        ))
    )
    gateway._get_all_case_nodes = AsyncMock(return_value=nodes)  # type: ignore[method-assign]
    gateway._retrieve_subgraph_multi = AsyncMock(return_value=(nodes, edges))  # type: ignore[method-assign]
    gateway._retrieve_subgraph = AsyncMock(return_value=(nodes, edges))  # type: ignore[method-assign]
    gateway._retrieve_case_documents = AsyncMock(return_value=docs)  # type: ignore[method-assign]

    history = [
        {"role": "user", "content": "What is Anjali Hussain's phone number?"},
        {"role": "assistant", "content": "No case record currently documents a phone number for Anjali Hussain."},
    ]
    response = await gateway.ask(
        question="What about Dinesh?",
        case_id=CASE_ID,
        history=history,
    )
    answer = response.finding.direct_answer or response.finding.summary
    assert "+919812345670" in answer
    assert response.context["followup_resolution"]["is_followup"] is True
    assert response.context["followup_resolution"]["rewritten"]
    assert "phone number" in response.context["followup_resolution"]["rewritten"].lower()
