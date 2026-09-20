"""Evidence corroboration: one record is one source, and support is not truth.

The claims an investigator must be able to trust here are counting claims:
"this appears in three records across two evidence types" has to mean exactly
that — not "this document repeated itself three times".
"""

from __future__ import annotations

from app.ai.claims import EntityVocabulary, claims_from_edges, extract_claims_from_documents
from app.ai.contradiction import contradicted_identities, detect_contradictions
from app.ai.corroboration import (
    STATUS_CONFLICTED,
    STATUS_MULTI_SOURCE,
    STATUS_MULTI_TYPE,
    STATUS_SINGLE_SOURCE,
    corroborate_claims,
    multi_source_corroborations,
    summarize_corroboration,
)
from app.ai.response_composer import deterministic_fallback

NODES = [
    {"provenance_key": "person:1", "label": "Person", "properties": {"name": "Anjali Hussain"}},
    {"provenance_key": "person:2", "label": "Person", "properties": {"name": "Dinesh Malhotra"}},
    {"provenance_key": "location:1", "label": "Location", "properties": {"address": "Central Market"}},
]


def _document(doc_id: str, doc_type: str, content: str) -> dict:
    return {"doc_id": doc_id, "filename": f"{doc_id}.txt", "document_type": doc_type, "content": content}


def _claims(documents):
    return extract_claims_from_documents(documents, vocabulary=EntityVocabulary.from_nodes(NODES))


def _find(corroborations, needle: str):
    return next(c for c in corroborations if needle.lower() in c.claim.lower())


# --------------------------------------------------------------------------- #
# 13. The same assertion in multiple documents
# --------------------------------------------------------------------------- #

def test_same_claim_in_two_documents_is_multi_source():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("DIARY-02", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
    ])
    corroborations = corroborate_claims(claims)
    entry = _find(corroborations, "Central Market")
    assert entry.support_count == 2
    assert entry.status == STATUS_MULTI_SOURCE
    assert sorted(entry.document_ids) == ["DIARY-01", "DIARY-02"]


# --------------------------------------------------------------------------- #
# 14. The same assertion across different evidence types
# --------------------------------------------------------------------------- #

def test_same_claim_across_evidence_types_is_multi_type_corroborated():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("SURV-02", "SURVEILLANCE", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("CDR-03", "CALL_RECORD", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
    ])
    entry = _find(corroborate_claims(claims), "Central Market")
    assert entry.support_count == 3
    assert entry.independent_source_types == 3
    assert entry.status == STATUS_MULTI_TYPE
    assert {source["evidence_type"] for source in entry.sources} == {
        "CASE_DIARY", "SURVEILLANCE", "CALL_RECORD",
    }


def test_graph_relationships_corroborated_across_evidence_types():
    """The CDR and the case diary both document the same phone association."""
    edges = [
        {
            "source_key": "person:1", "target_key": "person:2", "rel_type": "CALLED",
            "timestamp": "2025-05-20T10:00:00+00:00",
            "source_doc_ids": ["CDR-03", "DIARY-01"], "case_ids": ["case-c1"],
        }
    ]
    claims = claims_from_edges(
        edges,
        key_to_label={"person:1": "Anjali Hussain", "person:2": "Dinesh Malhotra"},
        evidence_types={"CDR-03": "CALL_RECORD", "DIARY-01": "CASE_DIARY"},
    )
    entry = _find(corroborate_claims(claims), "Anjali Hussain")
    assert entry.support_count == 2
    assert entry.independent_source_types == 2
    assert entry.status == STATUS_MULTI_TYPE


# --------------------------------------------------------------------------- #
# 15. Repetition in one document is not independent corroboration
# --------------------------------------------------------------------------- #

def test_repetition_within_one_document_is_a_single_source():
    repeated = "\n".join(
        "Anjali Hussain was at Central Market at 21:00 on 22 May 2025." for _ in range(5)
    )
    claims = _claims([_document("DIARY-01", "CASE_DIARY", repeated)])
    corroborations = corroborate_claims(claims)
    entry = _find(corroborations, "Central Market")
    assert entry.support_count == 1
    assert entry.status == STATUS_SINGLE_SOURCE


def test_only_multi_source_assertions_are_reported_as_corroborated():
    claims = _claims([
        _document("DIARY-01", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("SURV-02", "SURVEILLANCE", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("NOTE-03", "FIELD_REPORT", "Dinesh Malhotra was at Central Market at 09:00 on 01 June 2025."),
    ])
    corroborations = corroborate_claims(claims)
    multi = multi_source_corroborations(corroborations)
    assert len(multi) == 1
    assert "Anjali Hussain" in multi[0].claim
    summary = summarize_corroboration(corroborations)
    assert summary["assertions_examined"] == len(corroborations)
    assert summary["multi_source"] == 1


# --------------------------------------------------------------------------- #
# 16. A contested assertion is never labelled corroborated
# --------------------------------------------------------------------------- #

def test_conflicted_claim_is_not_labelled_corroborated():
    documents = [
        _document("DIARY-01", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("SURV-02", "SURVEILLANCE", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("STMT-03", "WITNESS_STATEMENT", "Anjali Hussain was at Riverside Depot at 21:00 on 22 May 2025."),
    ]
    claims = _claims(documents)
    contradictions = detect_contradictions(claims)
    corroborations = corroborate_claims(claims, contradicted_keys=contradicted_identities(contradictions))
    contested = [c for c in corroborations if c.contradicted]
    assert contested, "the contested assertion must be marked"
    assert all(c.status == STATUS_CONFLICTED for c in contested)
    assert any("Central Market" in c.claim for c in contested)
    assert not multi_source_corroborations(corroborations), (
        "a contested assertion must not be presented as corroborated support"
    )


# --------------------------------------------------------------------------- #
# Answer shape: documentary support, never a conclusion
# --------------------------------------------------------------------------- #

def test_corroboration_answer_explains_support_and_its_limits():
    from app.ai.evidence_boundary import build_evidence_boundary
    from app.ai.query_planner import plan_query

    documents = [
        _document("DIARY-01", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("SURV-02", "SURVEILLANCE", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025."),
        _document("NOTE-03", "FIELD_REPORT", "Dinesh Malhotra was at Central Market at 09:00 on 01 June 2025."),
    ]
    claims = _claims(documents)
    corroborations = corroborate_claims(claims)
    question = "Which facts are supported by multiple evidence sources?"
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
        corroboration=[c.as_dict() for c in corroborations],
        corroboration_summary=summarize_corroboration(corroborations),
    )
    payload = deterministic_fallback(boundary)
    summary = payload["summary"]
    assert "Central Market" in summary
    assert "[DIARY-01]" in summary and "[SURV-02]" in summary
    assert "not proof" in summary
    corroborated_claims = [c for c in payload["claims"] if c.get("corroboration")]
    assert corroborated_claims, "multi-record support must be visible on the claim"
    assert payload["evidence_refs"]
    for conclusion in ("guilty", "proves guilt", "establishes guilt"):
        assert conclusion not in summary.lower()


def test_evidence_type_count_is_deduplicated():
    """Three documents of one evidence type are three records, one type."""
    claims = _claims([
        _document(f"DIARY-0{index}", "CASE_DIARY", "Anjali Hussain was at Central Market at 21:00 on 22 May 2025.")
        for index in range(1, 4)
    ])
    entry = _find(corroborate_claims(claims), "Central Market")
    assert entry.support_count == 3
    assert entry.independent_source_types == 1
    assert entry.status == STATUS_MULTI_SOURCE
