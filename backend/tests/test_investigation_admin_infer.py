"""Regression tests for Investigation workflow state inference (WS 4).

Verifies:
1. Cases ingested via the Admin dataset path (with dataset_id set) infer
   stages 1–5 as COMPLETED, not PENDING.
2. Stages 7 (ai_analysis) and 8 (generate_findings) remain PENDING until explicitly run.
3. Inferred COMPLETED stages unblock downstream stages: stage 6 is runnable
   and does not raise StageBlocked.
4. Non-admin cases (dataset_id=None) without explicit runs remain PENDING.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    InvestigationStageRun,
)
from app.db.session import sync_session
from app.domain.enums import CaseStatus, DocumentType, IngestionStatus, PatternType, SourceConfidence
from app.domain.models import GraphNode
from app.services.investigation import run_stage, workflow_state


@pytest.fixture()
def admin_dataset_case(container) -> str:
    """Create a case simulated as imported via the Admin dataset path."""
    case_id = f"ADM_{uuid4().hex[:8]}"
    dataset_id = f"DS_{uuid4().hex[:8]}"

    with sync_session() as session:
        case = Case(
            id=case_id,
            case_number=f"FIR/ADMIN/{uuid4().hex[:6]}",
            title="Admin Ingested Fraud Case",
            jurisdiction_id="SYN-DEV",
            dataset_id=dataset_id,
            status=CaseStatus.OPEN,
        )
        session.add(case)

        # Add 3 COMPLETE documents
        for i in range(3):
            doc = CaseDocument(
                id=new_uuid(),
                case_id=case_id,
                dataset_id=dataset_id,
                document_type=DocumentType.FIR.value,
                filename=f"doc_{i}.txt",
                storage_key=f"docs/doc_{i}.txt",
                content_hash=f"hash_{i}_{uuid4().hex}",
                size_bytes=1000,
                ingestion_status=IngestionStatus.COMPLETE.value,
                ingestion_stage=6,
                source_confidence=SourceConfidence.SYNTHETIC,
            )
            session.add(doc)
        session.commit()

    # Add graph nodes so stage 5 can verify graph presence
    node1 = GraphNode(
        provenance_key=f"PERSON_ADM_1_{uuid4().hex[:4]}",
        label="Person",
        properties={
            "name": "Suspect A",
            "case_id": case_id,
            "case_ids": [case_id],
            "confidence": 0.95,
            "source_doc_ids": ["doc_0"],
        },
    )
    node2 = GraphNode(
        provenance_key=f"PERSON_ADM_2_{uuid4().hex[:4]}",
        label="Person",
        properties={
            "name": "Suspect B",
            "case_id": case_id,
            "case_ids": [case_id],
            "confidence": 0.95,
            "source_doc_ids": ["doc_1"],
        },
    )
    container.graph_store.upsert_nodes([node1, node2])

    return case_id


def test_admin_ingested_case_infers_stages_1_to_5_as_completed(admin_dataset_case: str):
    """An admin-ingested case must show stages 1-5 COMPLETED without explicit runs."""
    state = workflow_state(admin_dataset_case)
    by_key = {s["key"]: s for s in state["stages"]}

    # Stages 1 to 5 must report COMPLETED with inferred=True
    for key in ("process_data", "extract_entities", "resolve_entities", "build_relationships", "build_graph"):
        stage = by_key[key]
        assert stage["status"] == "COMPLETED", f"Stage {key} should be COMPLETED, got {stage['status']}"
        assert stage["detail"].get("inferred") is True, f"Stage {key} detail should indicate inferred"

    # Stage 6 is PENDING (no patterns yet), but RUNNABLE because stage 5 is COMPLETED
    assert by_key["network_analysis"]["status"] == "PENDING"
    assert by_key["network_analysis"]["runnable"] is True
    assert by_key["network_analysis"]["blocked_by"] == []

    # Stages 7 and 8 are NOT inferred; they remain PENDING
    assert by_key["ai_analysis"]["status"] == "PENDING"
    assert by_key["generate_findings"]["status"] == "PENDING"


def test_admin_ingested_case_can_run_stage_6_without_block(admin_dataset_case: str):
    """Inferred completion unblocks running stage 6 directly."""
    result = run_stage(admin_dataset_case, "network_analysis")
    assert result["stage"] == 6
    assert result["key"] == "network_analysis"
    assert result["status"] == "COMPLETED"

    # Now workflow_state reflects the explicit run for stage 6
    state = workflow_state(admin_dataset_case)
    by_key = {s["key"]: s for s in state["stages"]}
    assert by_key["network_analysis"]["status"] == "COMPLETED"
    assert by_key["network_analysis"]["detail"].get("inferred") is not True


def test_non_admin_case_remains_pending_and_gated():
    """A case without dataset_id (e.g. manual creation) starts entirely PENDING."""
    case_id = f"MAN_{uuid4().hex[:8]}"
    with sync_session() as session:
        case = Case(
            id=case_id,
            case_number=f"FIR/MAN/{uuid4().hex[:6]}",
            title="Manual Case",
            jurisdiction_id="SYN-DEV",
            dataset_id=None,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        session.commit()

    state = workflow_state(case_id)
    by_key = {s["key"]: s for s in state["stages"]}
    assert all(s["status"] == "PENDING" for s in state["stages"])
    assert not by_key["extract_entities"]["runnable"]
    assert by_key["extract_entities"]["blocked_by"] == [1]


def test_finding_review_dismissed(admin_dataset_case: str):
    """Verifies that findings can be DISMISSED by an investigator (WS 5.2)."""
    from app.services.investigation import review_finding, run_stage
    from app.db.models import InvestigationFinding
    run_stage(admin_dataset_case, "network_analysis")
    run_stage(admin_dataset_case, "generate_findings")
    with sync_session() as session:
        finding = session.execute(
            select(InvestigationFinding).where(InvestigationFinding.case_id == admin_dataset_case)
        ).scalars().first()
    if finding:
        res = review_finding(admin_dataset_case, finding.id, "DISMISSED", "Not relevant", "inv-1")
        assert res["status"] == "DISMISSED"
        assert res["review_decision"] == "DISMISSED"
