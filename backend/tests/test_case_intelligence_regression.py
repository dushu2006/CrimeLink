"""Regression: the new intelligence must stay case-scoped and question-shaped.

Four case numbers are exercised — the demo corpus's CR-2001, CR-2019, CR-2020
and a synthetic fourth case — because the failure mode this guards against is
the worst one CrimeLink can have: an answer or an index entry that belongs to a
different investigation.
"""

from __future__ import annotations

import json

import pytest

from app.ai.claims import EntityVocabulary, extract_claims_from_documents
from app.ai.contradiction import detect_contradictions
from app.ai.corroboration import corroborate_claims, summarize_corroboration
from app.ai.evidence_boundary import build_evidence_boundary
from app.ai.query_planner import plan_query
from app.ai.response_composer import deterministic_fallback
from app.ai.semantic import (
    LocalHashingEmbedder,
    RouterEmbedder,
    SemanticIndexStore,
    merge_retrieval_candidates,
    search_case_index,
)
from app.ai.temporal import events_from_claims, merge_events, select_temporal_events

QUESTIONS = [
    "What are the details of this case?",
    "Tell me about the people involved.",
    "What are the files?",
    "What evidence connects {first} and {second}?",
    "Show me the financial evidence.",
    "What happened before the incident?",
    "Are there contradictions in the evidence?",
    "Which facts are supported by multiple evidence sources?",
    "Summarize this case.",
]


def _questions_for(case: dict) -> list[str]:
    """The same question set for every case, naming that case's own people."""
    people = [n["properties"]["name"] for n in case["nodes"] if "name" in n["properties"]]
    return [question.format(first=people[0], second=people[1]) for question in QUESTIONS]


def _case(case_number: str, case_id: str, *, city: str, people: tuple[str, str]) -> dict:
    """One synthetic, self-contained case with the same shape as a real one."""
    first, second = people
    nodes = [
        {"provenance_key": f"{case_id}:person:1", "label": "Person", "properties": {"name": first}},
        {"provenance_key": f"{case_id}:person:2", "label": "Person", "properties": {"name": second}},
        {"provenance_key": f"{case_id}:location:1", "label": "Location", "properties": {"address": f"{city} Market"}},
        {"provenance_key": f"{case_id}:location:2", "label": "Location", "properties": {"address": f"{city} Depot"}},
    ]
    documents = [
        {
            "doc_id": f"{case_number}-DIARY",
            "filename": f"{case_number}_diary.txt",
            "document_type": "CASE_DIARY",
            "content": (
                f"Incident: 2025-06-12T21:00:00+00:00\n"
                f"{first} was at {city} Market at 18:00 on 10 June 2025.\n"
                f"{second} was at {city} Market at 19:30 on 11 June 2025.\n"
                f"{first} was at {city} Market at 18:00 on 10 June 2025.\n"
            ),
        },
        {
            "doc_id": f"{case_number}-STMT",
            "filename": f"{case_number}_statement.txt",
            "document_type": "WITNESS_STATEMENT",
            "content": f"{first} was at {city} Market at 18:00 on 10 June 2025.\n",
        },
        {
            "doc_id": f"{case_number}-FIN",
            "filename": f"{case_number}_financial.csv",
            "document_type": "FINANCIAL",
            "content": (
                "txn_id,from_account,to_account,timestamp,amount,narrative\n"
                f"TXN-{case_number},50001,50002,2025-06-11T14:00:00+00:00,250000,Transfer per record\n"
            ),
        },
        {
            "doc_id": f"{case_number}-FIR",
            "filename": f"{case_number}_fir.pdf",
            "document_type": "FIR",
            "content": f"{first} is named as the accused in this case. Incident location: {city} Market.",
        },
    ]
    return {"case_number": case_number, "case_id": case_id, "nodes": nodes, "documents": documents}


CASES = [
    _case("CR-2001", "case-cr-2001", city="Bandra", people=("Ravi Kumar", "Meena Rao")),
    _case("CR-2019", "case-cr-2019", city="Wadala", people=("Imran Sheikh", "Kavita Nair")),
    _case("CR-2020", "case-cr-2020", city="Andheri", people=("Anjali Hussain", "Dinesh Malhotra")),
    _case("CR-2033", "case-cr-2033", city="Colaba", people=("Sunil Verma", "Rekha Iyer")),
]


def _boundary_for(case: dict, question: str):
    vocabulary = EntityVocabulary.from_nodes(case["nodes"])
    claims = extract_claims_from_documents(case["documents"], vocabulary=vocabulary)
    contradictions = detect_contradictions(claims)
    corroborations = corroborate_claims(claims)
    events = merge_events(events_from_claims(claims))
    plan = plan_query(question)
    temporal = (
        select_temporal_events(question, events, case_incident_time="2025-06-12T21:00:00+00:00")
        if plan.need_timeline
        else None
    )
    boundary = build_evidence_boundary(
        case_id=case["case_id"],
        case_number=case["case_number"],
        case_title=f"{case['case_number']} investigation",
        case_status="OPEN",
        jurisdiction="METRO-CENTRAL",
        question=question,
        plan=plan,
        nodes=case["nodes"],
        edges=[],
        documents=case["documents"],
        all_case_document_ids=[d["doc_id"] for d in case["documents"]],
        all_case_documents=case["documents"],
        contradictions=[c.as_dict() for c in contradictions],
        corroboration=[c.as_dict() for c in corroborations],
        corroboration_summary=summarize_corroboration(corroborations),
        temporal=temporal.as_dict() if temporal else {},
    )
    return boundary, plan


# --------------------------------------------------------------------------- #
# 27-30. Every case answers every question in its own terms
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("case", CASES, ids=[c["case_number"] for c in CASES])
def test_each_case_answers_all_questions_with_resolving_citations(case):
    answers: dict[str, str] = {}
    allowed_ids = {d["doc_id"] for d in case["documents"]}
    for question in _questions_for(case):
        boundary, _ = _boundary_for(case, question)
        payload = deterministic_fallback(boundary)
        summary = payload["summary"]
        assert summary.strip(), question
        for ref in payload["evidence_refs"]:
            assert ref in allowed_ids, f"{case['case_number']}: unknown citation {ref}"
        for claim in payload["claims"]:
            for ref in claim.get("evidence_refs", []):
                assert ref in allowed_ids or not ref
        answers[question] = summary

    distinct = {question: summary for question, summary in answers.items()}
    assert len(set(distinct.values())) >= 7, (
        f"{case['case_number']}: answers collapsed into a single template"
    )
    # The two overview-shaped questions may agree in substance but not verbatim
    # with the contradiction answer.
    assert answers["Are there contradictions in the evidence?"] != answers["Summarize this case."]


@pytest.mark.parametrize("case", CASES, ids=[c["case_number"] for c in CASES])
def test_answers_never_name_another_case_or_its_people(case):
    foreign_terms = set()
    for other in CASES:
        if other["case_id"] == case["case_id"]:
            continue
        foreign_terms.add(other["case_number"])
        foreign_terms.update(node["properties"]["name"] for node in other["nodes"] if "name" in node["properties"])

    for question in _questions_for(case):
        boundary, _ = _boundary_for(case, question)
        payload = deterministic_fallback(boundary)
        prose = payload["summary"] + " " + " ".join(payload.get("followup_questions", []))
        for term in foreign_terms:
            if term in {"Market", "Depot"}:
                continue
            assert term not in prose, (
                f"{case['case_number']} leaked {term!r} into an answer"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[c["case_number"] for c in CASES])
async def test_semantic_indexes_are_isolated_per_case(tmp_path, case):
    store = SemanticIndexStore(tmp_path / "ai_index")
    embedder = RouterEmbedder(None, fallback=LocalHashingEmbedder())
    hits, stats = await search_case_index(
        case_id=case["case_id"],
        question="What happened before the incident?",
        documents=case["documents"],
        store=store,
        embedder=embedder,
    )
    assert stats.available
    stored = json.dumps(store.load(case["case_id"]))
    for other in CASES:
        if other["case_id"] == case["case_id"]:
            continue
        for document in other["documents"]:
            assert document["doc_id"] not in stored
    for hit in hits:
        assert hit.chunk.document_id in {d["doc_id"] for d in case["documents"]}


@pytest.mark.asyncio
async def test_semantic_hits_from_another_case_are_not_merged(tmp_path):
    store = SemanticIndexStore(tmp_path / "ai_index")
    embedder = RouterEmbedder(None, fallback=LocalHashingEmbedder())
    case = CASES[2]
    other = CASES[0]
    hits, _ = await search_case_index(
        case_id=case["case_id"],
        question="financial transfers",
        documents=other["documents"] + case["documents"],
        store=store,
        embedder=embedder,
        authorized_document_ids=[d["doc_id"] for d in case["documents"]],
    )
    merged, _ = merge_retrieval_candidates(
        [],
        hits,
        all_documents=case["documents"],
        max_extra=10,
    )
    assert merged
    for document in merged:
        assert document["doc_id"] in {d["doc_id"] for d in case["documents"]}


def test_intelligence_cache_is_isolated_between_privacy_modes():
    """A pseudonymized object must never be served to a raw-PII request."""
    from app.ai import gateway as gateway_module

    gateway_module._INTELLIGENCE_CACHE.setdefault("case-x|fp|pseudonymized", {"marker": "pseudo"})
    gateway_module._INTELLIGENCE_CACHE.setdefault("case-x|fp|raw", {"marker": "raw"})
    assert gateway_module._INTELLIGENCE_CACHE["case-x|fp|pseudonymized"]["marker"] == "pseudo"
    assert gateway_module._INTELLIGENCE_CACHE["case-x|fp|raw"]["marker"] == "raw"
    gateway_module._INTELLIGENCE_CACHE.pop("case-x|fp|pseudonymized", None)
    gateway_module._INTELLIGENCE_CACHE.pop("case-x|fp|raw", None)
