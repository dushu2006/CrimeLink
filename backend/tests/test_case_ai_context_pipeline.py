import pytest
from app.ai.case_context import (
    CaseAIContext,
    CaseContextStats,
    format_readable_date,
    generate_case_suggested_questions,
)
from app.ai.schemas import FindingResult, ClaimCitation
from app.ai.evidence_contract import enrich_finding_contract
from app.ai.gateway import get_ai_gateway


def test_format_readable_date():
    assert format_readable_date(None) == "Unknown"
    assert format_readable_date("2026-01-12T14:30:00Z") == "Jan 12, 2026"
    assert format_readable_date("2026-03-08") == "Mar 08, 2026"


def test_case_context_stats_detailed_dict():
    stats = CaseContextStats(
        case_id="case-uuid-1",
        evidence_count=12,
        entity_count=14,
        person_count=8,
        relationship_count=14,
        evidence_types_count=4,
        evidence_types=["CDR", "FINANCIAL", "SURVEILLANCE", "DOCUMENT"],
        entity_counts_by_type={"people": 8, "accounts": 3, "phones": 2, "vehicles": 1},
    )

    # Canonical 5-key backwards compatibility
    canonical = stats.as_dict()
    assert len(canonical) == 5
    assert set(canonical.keys()) == {
        "case_id",
        "evidence_count",
        "entity_count",
        "person_count",
        "relationship_count",
    }

    # Detailed dictionary
    detailed = stats.as_detailed_dict()
    assert detailed["evidence_types_count"] == 4
    assert detailed["evidence_types"] == ["CDR", "FINANCIAL", "SURVEILLANCE", "DOCUMENT"]
    assert detailed["entity_counts_by_type"] == {"people": 8, "accounts": 3, "phones": 2, "vehicles": 1}


def test_generate_case_suggested_questions():
    entities = [
        {"label": "PERSON", "properties": {"name": "Ravi Kumar", "is_criminal": True}},
        {"label": "PERSON", "properties": {"name": "Mohan Sharma"}},
        {"label": "ACCOUNT", "properties": {"account_number": "ACC-9988"}},
        {"label": "PHONE", "properties": {"phone_number": "+91-9876543210"}},
    ]
    evidence_types = ["CDR", "FINANCIAL", "SURVEILLANCE"]

    questions = generate_case_suggested_questions(
        case_number="CR-2020",
        case_title="Tender manipulation and bribery at a ward office",
        entities=entities,
        evidence_types=evidence_types,
    )

    assert len(questions) >= 5
    # Should include target person connection to the tender theme
    assert any("Ravi Kumar" in q and "tender process" in q for q in questions)
    # Should include financial relationships question
    assert any("financial relationships" in q.lower() for q in questions)
    # Should include timeline before and after question
    assert any("before and after" in q.lower() for q in questions)
    # Should include multiple evidence sources corroboration
    assert any("multiple evidence sources" in q.lower() for q in questions)
    # Should include contradictions question
    assert any("contradictions" in q.lower() for q in questions)


def test_case_ai_context_summary_dict():
    stats = CaseContextStats(
        case_id="case-uuid-1",
        evidence_count=12,
        entity_count=14,
        person_count=8,
        relationship_count=14,
        evidence_types_count=4,
        evidence_types=["CDR", "FINANCIAL"],
        entity_counts_by_type={"people": 8, "accounts": 3, "phones": 2, "vehicles": 1},
    )
    context = CaseAIContext(
        case_id="case-uuid-1",
        case_number="CR-2020",
        case_title="Tender manipulation and bribery",
        status="OPEN",
        jurisdiction="METRO-CENTRAL",
        stats=stats,
        timeline_summary={"first_recorded": "Jan 12, 2026", "latest_recorded": "Mar 08, 2026", "event_count": 28},
        verified_evidence=[{"doc_id": "doc1", "document_type": "CDR"}],
        entities=[{"name": "Ravi Kumar", "label": "PERSON"}],
        relationships=[{"source": "Ravi Kumar", "target": "Mohan Sharma", "rel_type": "CALLED"}],
        events=[{"timestamp": "2026-01-12"}],
        suggested_questions=["Who are the key people in this case?"],
    )

    summary = context.as_summary_dict()
    assert summary["case_number"] == "CR-2020"
    assert summary["case_title"] == "Tender manipulation and bribery"
    assert summary["timeline"]["first_recorded"] == "Jan 12, 2026"
    assert summary["timeline"]["latest_recorded"] == "Mar 08, 2026"
    assert summary["stats"]["entity_counts_by_type"]["people"] == 8
    assert summary["suggested_questions"] == ["Who are the key people in this case?"]


def test_claim_support_classification_and_corroboration():
    finding = FindingResult(
        summary="Ravi Kumar called Mohan Sharma 7 times before the tender submission. Financial transfers followed.",
        confidence=0.85,
        direct_answer="Ravi Kumar called Mohan Sharma 7 times before the tender submission.",
        claims=[
            ClaimCitation(
                claim="Ravi contacted Mohan seven times before the incident.",
                evidence_refs=["CDR-02"],
                evidence_level="FACT",
                support_level="DIRECTLY_SUPPORTED",
            ),
            ClaimCitation(
                claim="Timing and frequency of calls may indicate coordination.",
                evidence_refs=["CDR-02"],
                evidence_level="INFERENCE",
                support_level="INFERRED",
            ),
            ClaimCitation(
                claim="Financial transfer of INR 50,000 between accounts.",
                evidence_refs=["CDR-02", "FIN-04"],
                evidence_level="FACT",
                support_level="DIRECTLY_SUPPORTED",
            ),
        ],
    )

    enriched = enrich_finding_contract(
        finding=finding,
        evidence_type_by_id={"CDR-02": "CDR", "FIN-04": "FINANCIAL", "SURV-03": "SURVEILLANCE"},
    )

    assert enriched.evidence_coverage["total_claims"] == 3
    assert enriched.evidence_coverage["supported_claims"] >= 2
    assert "sources_used" in enriched.why_this_answer
    assert any(s["doc_id"] == "CDR-02" for s in enriched.why_this_answer["sources_used"])
    assert any(s["doc_id"] == "FIN-04" for s in enriched.why_this_answer["sources_used"])
    assert len(enriched.followup_questions) >= 1
    # Check that claim with multiple evidence types has corroboration
    assert any("Corroborated across 2 evidence types" in (c.corroboration or "") for c in enriched.claims)


def test_history_coreference_resolution():
    all_case_nodes = [
        {
            "provenance_key": "person:ravi_kumar",
            "label": "Person",
            "properties": {"name": "Ravi Kumar"},
        },
        {
            "provenance_key": "person:mohan_sharma",
            "label": "Person",
            "properties": {"name": "Mohan Sharma"},
        },
    ]
    history = [
        {"role": "user", "content": "Tell me about Ravi Kumar and his phone records."},
        {"role": "assistant", "content": "Ravi Kumar is associated with phone +91-9876543210 and CDR-02."},
    ]
    query = "What financial transactions did he make?"
    resolved = get_ai_gateway()._resolve_entities_from_history(query, history, all_case_nodes)
    assert "person:ravi_kumar" in resolved
