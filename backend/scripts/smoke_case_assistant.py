"""Live smoke test for the Case Evidence Assistant redesign.

Seeds a realistic case (people, phones, accounts, vehicles, documents,
timeline) into an isolated scratch space, then asks the target
questions and prints the resulting answer, intent, and follow-ups.

Run::

    python scripts/smoke_case_assistant.py

The provider is stubbed so the script works without an API key; the
deterministic path is exercised when no key is configured.  Environment
is sandboxed to a temp dir so no real data is touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolated workspace so the smoke test never touches real data.
_TMP = tempfile.mkdtemp(prefix="crimelink-smoke-")
os.environ.setdefault("CRIMELINK_DATA_DIR", _TMP)
os.environ.setdefault("CRIMELINK_DB_URL", f"sqlite:///{_TMP}/smoke.db")
os.environ.setdefault("CRIMELINK_AI_ALLOW_RAW_PII", "true")
os.environ.pop("CRIMELINK_AI_API_KEY", None)
os.environ.pop("CRIMELINK_AI_REASONING_API_KEY", None)

from app.ai.query_planner import plan_query, INTENT_ENTITY_LOOKUP  # noqa: E402
from app.ai.response_composer import attribute_answer, deterministic_fallback  # noqa: E402
from app.ai.evidence_boundary import build_evidence_boundary  # noqa: E402
from app.ai.response_composer import build_prompt  # noqa: E402


QUESTIONS = [
    "What are the details of this case?",
    "Tell me about the people involved.",
    "What are the files?",
    "What evidence connects Anjali Hussain and Dinesh Malhotra?",
    "What happened in this case?",
    "Show me the financial evidence.",
    "What happened before the incident?",
    "What is the incident date?",
    "Who is the suspect?",
    "What is the phone number of Raja Kumar?",
    "What is the FIR number?",
    "Are there contradictions in the evidence?",
    "Summarize this case.",
    "What about Raja Kumar?",
    "What is 18% of 450?",
    "Explain chain of custody in Indian courts",
]


def _case_fixture() -> dict:
    """A small but realistic case used by the smoke test."""

    case_id = "case-smoke-001"
    nodes = [
        {"provenance_key": "person:1", "label": "Person",
         "properties": {"name": "Anjali Hussain", "role": "Witness",
                        "case_ids": [case_id], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "person:2", "label": "Person",
         "properties": {"name": "Dinesh Malhotra", "role": "Accused",
                        "case_ids": [case_id], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "person:3", "label": "Person",
         "properties": {"name": "Rekha Sharma", "role": "Informant",
                        "case_ids": [case_id], "source_doc_ids": ["WIT-002"]}},
        {"provenance_key": "person:4", "label": "Person",
         "properties": {"name": "Raja Kumar", "role": "Suspect",
                        "case_ids": [case_id], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "phone:1", "label": "Phone",
         "properties": {"number": "+919812345670", "case_ids": [case_id],
                        "source_doc_ids": ["CDR-001"]}},
        {"provenance_key": "phone:2", "label": "Phone",
         "properties": {"number": "+919812345671", "case_ids": [case_id],
                        "source_doc_ids": ["CDR-001"]}},
        {"provenance_key": "phone:3", "label": "Phone",
         "properties": {"number": "+919876540321", "case_ids": [case_id],
                        "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "account:1", "label": "BankAccount",
         "properties": {"account_number": "50100234567890", "bank_name": "SBI",
                        "case_ids": [case_id], "source_doc_ids": ["BANK-003"]}},
        {"provenance_key": "account:2", "label": "BankAccount",
         "properties": {"account_number": "50100234567891", "bank_name": "HDFC",
                        "case_ids": [case_id], "source_doc_ids": ["BANK-003"]}},
        {"provenance_key": "vehicle:1", "label": "Vehicle",
         "properties": {"plate": "RJ14AB1234", "make_model": "Swift",
                        "case_ids": [case_id], "source_doc_ids": ["FIR-001"]}},
        {"provenance_key": "location:1", "label": "Location",
         "properties": {"address": "Warehouse, Mansarovar",
                        "case_ids": [case_id], "source_doc_ids": ["CCTV-004"]}},
    ]
    edges = [
        {"source_key": "person:2", "target_key": "person:1", "rel_type": "CALLED",
         "confidence": 0.9, "timestamp": "2020-03-11T20:14:00Z",
         "source_doc_ids": ["CDR-001"], "call_count": 14, "case_ids": [case_id]},
        {"source_key": "phone:1", "target_key": "phone:2", "rel_type": "CALLED",
         "confidence": 0.9, "timestamp": "2020-03-11T20:14:00Z",
         "source_doc_ids": ["CDR-001"], "call_count": 14, "case_ids": [case_id]},
        {"source_key": "person:2", "target_key": "phone:1", "rel_type": "USES_PHONE",
         "confidence": 0.95, "source_doc_ids": ["CDR-001"], "case_ids": [case_id]},
        {"source_key": "person:1", "target_key": "phone:2", "rel_type": "USES_PHONE",
         "confidence": 0.95, "source_doc_ids": ["CDR-001"], "case_ids": [case_id]},
        {"source_key": "person:4", "target_key": "phone:3", "rel_type": "USES_PHONE",
         "confidence": 0.92, "source_doc_ids": ["FIR-001"], "case_ids": [case_id]},
        {"source_key": "person:2", "target_key": "account:1", "rel_type": "OWNS_ACCOUNT",
         "confidence": 0.9, "source_doc_ids": ["BANK-003"], "case_ids": [case_id]},
        {"source_key": "person:1", "target_key": "account:2", "rel_type": "OWNS_ACCOUNT",
         "confidence": 0.9, "source_doc_ids": ["BANK-003"], "case_ids": [case_id]},
        {"source_key": "account:1", "target_key": "account:2", "rel_type": "TRANSFER_TO",
         "confidence": 0.85, "amount": 450000, "timestamp": "2020-03-10T11:00:00Z",
         "source_doc_ids": ["BANK-003"], "case_ids": [case_id]},
        {"source_key": "person:2", "target_key": "vehicle:1", "rel_type": "OWNS_VEHICLE",
         "confidence": 0.9, "source_doc_ids": ["FIR-001"], "case_ids": [case_id]},
        {"source_key": "person:2", "target_key": "location:1", "rel_type": "LOCATED_AT",
         "confidence": 0.7, "timestamp": "2020-03-12T02:30:00Z",
         "source_doc_ids": ["CCTV-004"], "case_ids": [case_id]},
        {"source_key": "person:3", "target_key": "person:1", "rel_type": "ASSOCIATE_OF",
         "confidence": 0.6, "source_doc_ids": ["WIT-002"], "case_ids": [case_id]},
    ]
    documents = [
        {"doc_id": "FIR-001", "filename": "FIR_CR2020_001.txt", "document_type": "FIR",
         "content": "First information report naming Dinesh Malhotra as accused and "
                    "Anjali Hussain as witness. Incident occurred on 2020-03-12.",
         "case_ids": [case_id]},
        {"doc_id": "CDR-001", "filename": "CDR_MARCH_2020.csv", "document_type": "CALL_RECORD",
         "content": "Call detail records showing 14 calls between the two numbers on 2020-03-11.",
         "case_ids": [case_id]},
        {"doc_id": "BANK-003", "filename": "BANK_STATEMENT_2020.csv", "document_type": "BANK_STATEMENT",
         "content": "Bank statement showing a transfer of Rs 4,50,000 on 2020-03-10.",
         "case_ids": [case_id]},
        {"doc_id": "CCTV-004", "filename": "CCTV_MANSAROVAR.txt", "document_type": "CCTV",
         "content": "CCTV log placing the vehicle RJ14AB1234 near the warehouse at 02:30.",
         "case_ids": [case_id]},
        {"doc_id": "WIT-002", "filename": "WITNESS_STATEMENT_SHARMA.txt", "document_type": "WITNESS_STATEMENT",
         "content": "Witness statement from Rekha Sharma describing the accused's movements.",
         "case_ids": [case_id]},
    ]
    return {"case_id": case_id, "nodes": nodes, "edges": edges, "documents": documents}


def _timeline(nodes, edges):
    from app.ai.retrieval import build_timeline_from_context
    return build_timeline_from_context(nodes, edges)


def main() -> int:
    fixture = _case_fixture()
    case_id = fixture["case_id"]
    nodes, edges, documents = fixture["nodes"], fixture["edges"], fixture["documents"]
    timeline = _timeline(nodes, edges)

    case_stats = {
        "case_id": case_id,
        "evidence_count": len(documents),
        "entity_count": len(nodes),
        "person_count": sum(1 for n in nodes if n["label"] == "Person"),
        "relationship_count": len(edges),
        "evidence_types_count": len({d["document_type"] for d in documents}),
        "evidence_types": sorted({d["document_type"] for d in documents}),
        "entity_counts_by_type": {"people": 4, "phones": 3, "accounts": 2, "vehicles": 1, "locations": 1},
    }

    known_persons = [
        "Anjali Hussain", "Dinesh Malhotra", "Rekha Sharma", "Raja Kumar",
    ]

    for question in QUESTIONS:
        plan = plan_query(question, known_person_names=known_persons)
        boundary = build_evidence_boundary(
            case_id=case_id,
            case_number="CR-2020",
            case_title="Suspected extortion and money laundering",
            case_status="UNDER_INVESTIGATION",
            jurisdiction="RJ-JAIPUR",
            question=question,
            plan=plan,
            nodes=nodes,
            edges=edges,
            documents=documents,
            all_case_document_ids=[d["doc_id"] for d in documents],
            timeline=timeline,
            available_evidence_types=case_stats["evidence_types"],
            missing_evidence_types=["CHARGESHEET"],
            case_stats=case_stats,
        )
        system_prompt, user_prompt = build_prompt(boundary)
        if plan.intent == INTENT_ENTITY_LOOKUP:
            fb = attribute_answer(boundary, plan)
        else:
            assert "You are an investigative analysis assistant" in system_prompt
            fb = deterministic_fallback(boundary)

        print("=" * 78)
        print(f"Q: {question}")
        print(f"   intent={plan.intent} style={plan.response_style} detail={plan.detail} attr={plan.requested_attribute}")
        print(f"   entities_resolved={plan.entities or plan.person_names}")
        print(f"   prompt_chars={len(user_prompt)}  claims={len(fb.get('claims', []))}")
        print("-" * 78)
        print(str(fb.get("summary"))[:700])
        if fb.get("followup_questions"):
            print("-- follow-ups:", fb["followup_questions"][:3])
        print()

    print(f"[smoke-case] done, scratch={_TMP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
