"""Second-pass AI quality contracts: modes, support levels and citations."""

from __future__ import annotations

import pytest

from app.ai.evidence_contract import (
    citation_coverage,
    classify_evidence_support,
    enrich_finding_contract,
    evidence_inventory,
)
from app.ai.retrieval import rank_and_filter_context, understand_query
from app.ai.safety import AISafetyViolation, validate_finding
from app.ai.schemas import ClaimCitation, EvidenceRef, FindingResult


def test_fact_is_strong_only_when_independent_source_types_corroborate():
    assert classify_evidence_support("FACT", ["d1"], ["FIR"]) == "DIRECTLY_SUPPORTED"
    assert classify_evidence_support(
        "FACT", ["d1", "d2"], ["FIR", "CALL_RECORD"]
    ) == "STRONGLY_SUPPORTED"
    assert classify_evidence_support("INFERENCE", ["d1"], ["FIR"]) == "INFERRED"
    assert classify_evidence_support("FACT", [], ["FIR", "CCTV"]) == "UNSUPPORTED"


def test_legacy_finding_gets_three_explanation_layers_and_claim_citations():
    finding = FindingResult(
        summary="The records document contact between two entities.",
        confidence=0.7,
        evidence_level="FACT",
        evidence_refs=[EvidenceRef(doc_id="d1")],
    )

    enriched = enrich_finding_contract(
        finding,
        evidence_type_by_id={"d1": "CALL_RECORD"},
        answer_mode="RELATIONSHIP_ANALYSIS",
    )

    assert enriched.answer_mode == "RELATIONSHIP_ANALYSIS"
    assert enriched.direct_answer == finding.summary
    assert enriched.evidence_explanation
    assert enriched.investigator_interpretation
    assert enriched.claims[0].evidence_refs == ["d1"]
    assert enriched.claims[0].support_level == "DIRECTLY_SUPPORTED"
    assert enriched.does_not_establish
    assert citation_coverage(enriched, {"d1"}) == 1.0


def test_unknown_boundary_claim_can_be_uncited_but_stronger_claims_cannot():
    finding = FindingResult(
        summary="Insufficient evidence.",
        confidence=0.0,
        evidence_level="UNKNOWN",
        claims=[
            ClaimCitation(
                claim="The available records do not establish the answer.",
                evidence_level="UNKNOWN",
            )
        ],
    )
    assert validate_finding(finding, allowed_evidence_ids=set()).safe


def test_claim_and_relationship_citations_are_checked_against_case_package():
    finding = FindingResult(
        summary="Observed",
        confidence=0.5,
        evidence_level="INFERENCE",
        evidence_refs=[EvidenceRef(doc_id="d1")],
        claims=[
            ClaimCitation(
                claim="A relationship is suggested.",
                evidence_refs=["d404"],
                evidence_level="INFERENCE",
            )
        ],
        relationships=[{"source_person": "a", "target_person": "b", "evidence_refs": ["d1"]}],
    )
    with pytest.raises(AISafetyViolation, match="Claim references nonexistent evidence"):
        validate_finding(finding, allowed_evidence_ids={"d1"})

    missing_relationship_citation = finding.model_copy(
        update={"claims": [], "relationships": [{"source_person": "a", "target_person": "b"}]}
    )
    with pytest.raises(AISafetyViolation, match="has no supporting evidence"):
        validate_finding(missing_relationship_citation, allowed_evidence_ids={"d1"})


def test_query_modes_and_exact_identifiers_are_deterministic():
    relationship = understand_query("How is entity:alpha connected to account ACC-1042?")
    timeline = understand_query("What happened before 2026-08-12?")
    evidence = understand_query("Which CDR records support this?")
    pattern = understand_query("Are there repeated communication patterns?")

    assert relationship.answer_mode == "RELATIONSHIP_ANALYSIS"
    assert "ACC-1042" in relationship.exact_terms
    assert timeline.answer_mode == "TIMELINE_ANALYSIS"
    assert evidence.answer_mode == "EVIDENCE_ANALYSIS"
    assert pattern.answer_mode == "PATTERN_ANALYSIS"


def test_evidence_inventory_reports_missing_types_without_negative_claims():
    inventory = evidence_inventory(
        [
            {"doc_id": "d1", "document_type": "FIR"},
            {"doc_id": "d2", "document_type": "CALL_RECORD"},
        ],
        known_types=("FIR", "CALL_RECORD", "CCTV"),
    )
    assert inventory["available_types"] == ["CALL_RECORD", "FIR"]
    assert inventory["missing_types"] == ["CCTV"]
    assert inventory["document_ids"] == ["d1", "d2"]


def test_document_ranking_prefers_evidence_type_diversity_after_best_match():
    understanding = understand_query("What evidence supports the connection?")
    nodes = []
    edges = []
    documents = [
        {"doc_id": "fir-1", "filename": "fir.txt", "document_type": "FIR", "content": "evidence supports connection"},
        {"doc_id": "fir-2", "filename": "fir-2.txt", "document_type": "FIR", "content": "evidence supports connection"},
        {"doc_id": "cdr-1", "filename": "cdr.txt", "document_type": "CALL_RECORD", "content": "evidence connection"},
    ]

    ranked = rank_and_filter_context(
        nodes, edges, documents, understanding, max_doc_chars=10000, max_docs=3
    )

    assert ranked.documents[0]["document_type"] == "FIR"
    assert {doc["document_type"] for doc in ranked.documents[:2]} == {"FIR", "CALL_RECORD"}
