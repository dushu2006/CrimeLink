"""Versioned, evidence-indexed reporting endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApprovalRecord, CaseDocument, InvestigationReport
from app.db.session import get_db_session
from app.domain.enums import ApprovalStatus, ApprovalType
from app.security.deps import AuditRecorder, JurisdictionScope, Principal, get_audit_recorder, get_principal, get_scope, require_roles
from app.services import cases as case_service
from app.services import industry

router = APIRouter(tags=["reporting"])


class ReportDraftRequest(BaseModel):
    investigator_observations: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


def _sections(case, documents, payload: ReportDraftRequest) -> dict[str, Any]:
    return {
        "case_information": {"case_id": case.id, "case_number": case.case_number, "title": case.title, "jurisdiction_id": case.jurisdiction_id, "classification": case.classification.value},
        "executive_summary": "Evidence-grounded draft. A supervisor must review this report before it is relied upon.",
        "investigation_scope": "Authoritative records currently linked to this case.",
        "evidence_inventory": [{"evidence_id": item.id, "filename": item.filename, "sha256": item.content_hash, "classification": item.classification.value} for item in documents],
        "entities": [], "relationships": [], "network_analysis": [], "timeline": [], "financial_analysis": [], "communication_analysis": [], "geospatial_analysis": [], "patterns": [],
        "supporting_evidence": [item.id for item in documents], "contradictions": [], "alternative_explanations": [], "unknown_missing_information": [],
        "investigator_observations": payload.investigator_observations, "open_questions": payload.open_questions,
        "recommended_investigative_actions": ["Review unresolved contradictions and verify missing source records."],
        "audit_information": {"human_approval_required": True, "ai_is_not_authoritative": True},
    }


@router.post("/cases/{case_id}/reports", status_code=201)
async def create_report(case_id: str, payload: ReportDraftRequest, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await case_service.require_case(session, scope, case_id)
    documents = list((await session.execute(select(CaseDocument).where(CaseDocument.case_id == case.id, CaseDocument.is_deleted.is_(False)).order_by(CaseDocument.created_at))).scalars().all())
    content = _sections(case, documents, payload)
    evidence_index = [{"evidence_id": item.id, "sha256": item.content_hash, "filename": item.filename} for item in documents]
    report = await industry.create_report(session, case=case, actor_id=principal.id, content=content, evidence_index=evidence_index)
    recorder.record("CONFIG_CHANGE", target_resource=f"report:{report.id}", case_id=case.id, details={"action": "report_draft_created", "version": report.version})
    return industry.report_row(report)


@router.get("/cases/{case_id}/reports")
async def list_reports(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await case_service.require_case(session, scope, case_id)
    rows = list((await session.execute(select(InvestigationReport).where(InvestigationReport.case_id == case.id).order_by(InvestigationReport.version.desc()))).scalars().all())
    return {"case_id": case.id, "items": [industry.report_row(row) for row in rows]}


@router.post("/reports/{report_id}/submit")
async def submit_report(report_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"))):
    report = await session.get(InvestigationReport, report_id)
    if report is None:
        from app.errors import NotFoundError
        raise NotFoundError("Report not found.")
    await case_service.require_case(session, scope, report.case_id)
    report.status = "PENDING_REVIEW"
    existing = (await session.execute(select(ApprovalRecord).where(ApprovalRecord.object_id == report.id, ApprovalRecord.approval_type == ApprovalType.REPORT))).scalar_one_or_none()
    if existing is None:
        await industry.create_approval(session, case_id=report.case_id, object_type="report", object_id=report.id, approval_type=ApprovalType.REPORT, requested_by=report.generated_by or principal.id, object_payload={"object_hash": report.object_hash}, reason="Supervisor review requested.")
    return industry.report_row(report)


@router.post("/reports/{report_id}/approve")
async def approve_report(report_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    report = await session.get(InvestigationReport, report_id)
    if report is None:
        from app.errors import NotFoundError
        raise NotFoundError("Report not found.")
    case = await case_service.require_case(session, scope, report.case_id)
    if report.generated_by == principal.id:
        from app.errors import PermissionDeniedError
        raise PermissionDeniedError("A report author cannot approve their own report.")
    report.status = "APPROVED"
    report.approved_by = principal.id
    from app.db.base import utcnow
    report.approved_at = utcnow()
    approval = (await session.execute(select(ApprovalRecord).where(ApprovalRecord.object_id == report.id, ApprovalRecord.approval_type == ApprovalType.REPORT))).scalar_one_or_none()
    if approval is not None:
        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = principal.id
        approval.decided_at = report.approved_at
    else:
        await industry.create_approval(session, case_id=case.id, object_type="report", object_id=report.id, approval_type=ApprovalType.REPORT, requested_by=report.generated_by or principal.id, object_payload={"object_hash": report.object_hash})
        approval = (await session.execute(select(ApprovalRecord).where(ApprovalRecord.object_id == report.id, ApprovalRecord.approval_type == ApprovalType.REPORT))).scalar_one()
        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = principal.id
        approval.decided_at = report.approved_at
    recorder.record("CONFIG_CHANGE", target_resource=f"report:{report.id}", case_id=case.id, details={"action": "report_approved", "object_hash": report.object_hash})
    return industry.report_row(report)
