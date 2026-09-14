"""Industry-upgrade regression tests: custody, reviewable work and safe state."""

from __future__ import annotations

import pytest

from app.domain.enums import CaseStatus, InformationClassification
from app.errors import PermissionDeniedError, ValidationFailedError
from app.security.deps import Principal
from app.services import cases as case_service
from app.services import industry


@pytest.mark.asyncio
async def test_case_lifecycle_rejects_invalid_transition_and_allows_review_path(db, case, users):
    with pytest.raises(ValidationFailedError):
        await case_service.update_status(db, case, CaseStatus.SEALED, Principal(users["INV-0001"]))

    investigator = Principal(users["INV-0001"])
    await case_service.update_status(db, case, CaseStatus.ACTIVE_INVESTIGATION, investigator)
    await case_service.update_status(db, case, CaseStatus.SUBMITTED, investigator)
    with pytest.raises(PermissionDeniedError):
        await case_service.update_status(db, case, CaseStatus.CLOSED, investigator)

    supervisor = Principal(users["ADM-0001"])
    await case_service.update_status(db, case, CaseStatus.CLOSED, supervisor)
    await case_service.update_status(db, case, CaseStatus.SEALED, supervisor)
    assert case.status == CaseStatus.SEALED


@pytest.mark.asyncio
async def test_tasks_notes_hypotheses_are_persisted_without_mutating_evidence(db, case, users):
    investigator = Principal(users["INV-0001"])
    task = await industry.create_task(
        db,
        case=case,
        actor_id=investigator.id,
        title="Verify identity",
        description="Obtain an independent identity document.",
        priority="HIGH",
    )
    await industry.update_task(db, task, investigator.id, {"status": "IN_PROGRESS", "comment": "Requested."})
    assert industry.task_row(task)["status"] == "IN_PROGRESS"
    assert task.comments[0]["text"] == "Requested."

    note = await industry.create_note(
        db,
        case=case,
        actor=investigator,
        text="Phone ownership remains unverified.",
        classification=InformationClassification.CONFIDENTIAL,
    )
    assert industry.note_row(note)["version"] == 1

    hypothesis = await industry.create_hypothesis(
        db,
        case=case,
        actor_id=investigator.id,
        values={
            "hypothesis": "Person A may be an intermediary.",
            "supporting_evidence": ["evidence-1"],
            "unknown_information": ["Who controlled the account on the incident date?"],
        },
    )
    assert industry.hypothesis_row(hypothesis)["status"] == "OPEN"


@pytest.mark.asyncio
async def test_contradiction_requires_sources_and_preserves_both_claims(db, case):
    contradiction = await industry.create_contradiction(
        db,
        case_id=case.id,
        subject_key="PERSON:A",
        predicate="LOCATED_AT",
        claims=[
            {"object": "Location A", "source_ref": "cdr.csv#12", "observed_at": "2026-08-10T18:00:00Z"},
            {"object": "Location B", "source_ref": "cctv.csv#4", "observed_at": "2026-08-10T18:00:00Z"},
        ],
        verification_steps=["Review the raw CDR and CCTV timestamps with an analyst."],
    )
    row = industry.contradiction_row(contradiction)
    assert row["status"] == "CONTRADICTORY"
    assert len(row["claims"]) == 2
    assert "cdr.csv#12" in row["explanation"]


@pytest.mark.asyncio
async def test_controlled_approval_cannot_be_self_approved(db, case, users):
    investigator = Principal(users["INV-0001"])
    approval = await industry.create_approval(
        db,
        case_id=case.id,
        object_type="finding",
        object_id="finding-1",
        approval_type="FINDING",
        requested_by=investigator.id,
        object_payload={"evidence": ["doc-1"]},
    )
    with pytest.raises(PermissionDeniedError):
        await industry.decide_approval(db, approval, actor=investigator, status="APPROVED")

    admin = Principal(users["ADM-0001"])
    result = await industry.decide_approval(db, approval, actor=admin, status="APPROVED", reason="Reviewed sources.")
    assert industry.approval_row(result)["status"] == "APPROVED"


def test_case_scoped_workflow_and_report_endpoints(client, investigator_headers, admin_headers):
    created = client.post(
        "/api/v1/cases",
        headers=investigator_headers,
        json={"case_number": "FIR/INDUSTRY/001", "title": "Workflow test", "jurisdiction_id": "RJ-JAIPUR"},
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    task = client.post(f"/api/v1/cases/{case_id}/tasks", headers=investigator_headers, json={"title": "Request CCTV"})
    assert task.status_code == 201, task.text
    report = client.post(f"/api/v1/cases/{case_id}/reports", headers=investigator_headers, json={"open_questions": ["Who controlled the vehicle?"]})
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]
    assert report.json()["content"]["audit_information"]["human_approval_required"] is True
    submitted = client.post(f"/api/v1/reports/{report_id}/submit", headers=investigator_headers)
    assert submitted.status_code == 200, submitted.text
    approved = client.post(f"/api/v1/reports/{report_id}/approve", headers=admin_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
