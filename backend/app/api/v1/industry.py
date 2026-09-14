"""Evidence-grounded investigation workflow API.

The endpoints in this module persist reviewable work and provenance.  They do
not infer guilt, innocence, arrest decisions, or legal conclusions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApprovalRecord,
    CaseDocument,
    ContradictionRecord,
    EvidenceCustodyEvent,
    HypothesisRecord,
    InvestigationReport,
    InvestigationTask,
    InvestigatorNote,
    ModelRegistryEntry,
)
from app.db.session import get_db_session
from app.domain.enums import ApprovalType, CustodyEventType, InformationClassification
from app.security.classification import require_classification
from app.security.deps import AuditRecorder, JurisdictionScope, Principal, get_audit_recorder, get_principal, get_scope, require_roles
from app.services import cases as case_service
from app.services import industry

router = APIRouter(tags=["investigation-workflow"])


def _parse_json_list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=10000)
    owner_id: str | None = None
    priority: str = "MEDIUM"
    status: str = "TODO"
    due_at: str | None = None
    linked_evidence: list[str] = Field(default_factory=list)
    linked_entities: list[str] = Field(default_factory=list)
    linked_findings: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=10000)
    owner_id: str | None = None
    priority: str | None = None
    status: str | None = None
    due_at: str | None = None
    comment: str | None = Field(default=None, max_length=5000)


class NoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    classification: InformationClassification = InformationClassification.CONFIDENTIAL
    linked_evidence: list[str] = Field(default_factory=list)
    linked_entities: list[str] = Field(default_factory=list)
    linked_findings: list[str] = Field(default_factory=list)


class HypothesisCreate(BaseModel):
    statement: str | None = Field(default=None, min_length=1, max_length=5000)
    hypothesis: str | None = Field(default=None, min_length=1, max_length=5000)
    supporting_evidence: list[Any] = Field(default_factory=list)
    contradicting_evidence: list[Any] = Field(default_factory=list)
    unknown_information: list[str] = Field(default_factory=list)
    status: str = "OPEN"
    assessment: str = Field(default="", max_length=5000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ContradictionCreate(BaseModel):
    subject_key: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    claims: list[dict[str, Any]] = Field(min_length=2)
    verification_steps: list[str] = Field(default_factory=list)


class ClaimCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object: str = Field(min_length=1, max_length=200)
    observed_at: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: str = "UNVERIFIED"


class ApprovalCreate(BaseModel):
    object_type: str = Field(min_length=1, max_length=48)
    object_id: str = Field(min_length=1, max_length=36)
    approval_type: ApprovalType
    object_payload: Any = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=5000)


class ApprovalDecision(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=5000)


class ModelRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=32)
    prompt_version: str | None = None
    deployment_version: str | None = None
    performance: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    cost: float | None = None
    evaluation_status: str = "UNTESTED"
    active: bool = False


async def _case(case_id: str, scope: JurisdictionScope, session: AsyncSession, principal: Principal):
    case = await case_service.require_case(session, scope, case_id)
    require_classification(principal, case.classification)
    return case


@router.get("/cases/{case_id}/custody")
async def list_custody(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    result = await session.execute(select(EvidenceCustodyEvent).where(EvidenceCustodyEvent.case_id == case.id).order_by(EvidenceCustodyEvent.created_at, EvidenceCustodyEvent.id))
    rows = list(result.scalars().all())
    return {"case_id": case.id, "items": [{"id": row.id, "evidence_id": row.evidence_id, "event_type": row.event_type.value, "actor_id": row.actor_id, "object_hash": row.object_hash, "location": row.location, "details": row.details, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]}


@router.post("/documents/{doc_id}/integrity")
async def verify_integrity(doc_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal), recorder: AuditRecorder = Depends(get_audit_recorder)):
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        from app.errors import NotFoundError
        raise NotFoundError("Evidence not found.")
    case = await _case(document.case_id, scope, session, principal)
    result = await industry.verify_integrity(session, __import__("app.container", fromlist=["get_container"]).get_container(), document, principal.id)
    recorder.record("DOC_VIEW", target_resource=f"evidence:{doc_id}", case_id=case.id, details={"integrity": result.get("state")})
    return result


@router.get("/cases/{case_id}/tasks")
async def list_tasks(case_id: str, status: str | None = Query(default=None), scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    rows = await industry.list_tasks(session, case.id)
    if status:
        rows = [row for row in rows if row.status.value == status.upper()]
    return {"case_id": case.id, "items": [industry.task_row(row) for row in rows], "count": len(rows)}


@router.post("/cases/{case_id}/tasks", status_code=201)
async def create_task(case_id: str, payload: TaskCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await _case(case_id, scope, session, principal)
    task = await industry.create_task(session, case=case, actor_id=principal.id, **payload.model_dump())
    recorder.record("CONFIG_CHANGE", target_resource=f"task:{task.id}", case_id=case.id, details={"action": "task_created"})
    return industry.task_row(task)


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, payload: TaskUpdate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    task = await session.get(InvestigationTask, task_id)
    if task is None:
        from app.errors import NotFoundError
        raise NotFoundError("Task not found.")
    case = await _case(task.case_id, scope, session, principal)
    updated = await industry.update_task(session, task, principal.id, payload.model_dump(exclude_none=True))
    recorder.record("CONFIG_CHANGE", target_resource=f"task:{task.id}", case_id=case.id, details={"action": "task_updated"})
    return industry.task_row(updated)


@router.get("/cases/{case_id}/notes")
async def list_notes(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    rows = await industry.list_notes(session, case.id, principal)
    return {"case_id": case.id, "items": [industry.note_row(row) for row in rows], "count": len(rows)}


@router.post("/cases/{case_id}/notes", status_code=201)
async def create_note(case_id: str, payload: NoteCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await _case(case_id, scope, session, principal)
    note = await industry.create_note(session, case=case, actor=principal, **payload.model_dump())
    recorder.record("CONFIG_CHANGE", target_resource=f"note:{note.id}", case_id=case.id, details={"action": "note_created", "version": note.version})
    return industry.note_row(note)


@router.get("/cases/{case_id}/hypotheses")
async def list_hypotheses(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    rows = await industry.list_hypotheses(session, case.id)
    return {"case_id": case.id, "items": [industry.hypothesis_row(row) for row in rows], "count": len(rows)}


@router.post("/cases/{case_id}/hypotheses", status_code=201)
async def create_hypothesis(case_id: str, payload: HypothesisCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await _case(case_id, scope, session, principal)
    item = await industry.create_hypothesis(session, case=case, actor_id=principal.id, values=payload.model_dump())
    recorder.record("INVESTIGATE", target_resource=f"hypothesis:{item.id}", case_id=case.id, details={"action": "hypothesis_created"})
    return industry.hypothesis_row(item)


@router.get("/cases/{case_id}/contradictions")
async def list_contradictions(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    rows = await industry.list_contradictions(session, case.id)
    return {"case_id": case.id, "items": [industry.contradiction_row(row) for row in rows], "count": len(rows)}


@router.post("/cases/{case_id}/contradictions", status_code=201)
async def create_contradiction(case_id: str, payload: ContradictionCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await _case(case_id, scope, session, principal)
    item = await industry.create_contradiction(session, case_id=case.id, **payload.model_dump())
    recorder.record("INVESTIGATE", target_resource=f"contradiction:{item.id}", case_id=case.id, details={"action": "contradiction_recorded"})
    return industry.contradiction_row(item)


@router.get("/cases/{case_id}/unknowns")
async def unknowns(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    contradictions = await industry.list_contradictions(session, case.id)
    hypotheses = await industry.list_hypotheses(session, case.id)
    items = []
    for item in contradictions:
        items.append({"state": item.status.value, "subject": item.subject_key, "question": item.predicate, "why": item.explanation, "verification_steps": item.verification_steps or []})
    for item in hypotheses:
        for gap in item.unknown_information or []:
            items.append({"state": "UNKNOWN", "subject": item.statement, "question": gap, "why": "The investigator recorded this information as unresolved.", "verification_steps": [f"Verify: {gap}"]})
    return {"case_id": case.id, "items": items, "states": ["KNOWN", "UNKNOWN", "MISSING", "CONTRADICTORY", "UNVERIFIED"]}


@router.get("/cases/{case_id}/next-steps")
async def next_steps(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    contradictions = await industry.list_contradictions(session, case.id)
    hypotheses = await industry.list_hypotheses(session, case.id)
    steps = []
    for item in contradictions:
        steps.append({"priority": "HIGH", "action": "review contradiction", "reason": item.explanation, "source": item.id})
    for item in hypotheses:
        for gap in item.unknown_information or []:
            steps.append({"priority": "MEDIUM", "action": "gather missing information", "reason": gap, "source": item.id})
    if not steps:
        steps.append({"priority": "LOW", "action": "review evidence coverage", "reason": "No unresolved workflow item is recorded; verify that the relevant source datasets are available.", "source": None})
    return {"case_id": case.id, "items": steps, "disclaimer": "Evidence-gathering suggestions only; never a recommendation to arrest, prosecute, or punish."}


@router.post("/cases/{case_id}/claims", status_code=201)
async def create_claim(case_id: str, payload: ClaimCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN")), recorder: AuditRecorder = Depends(get_audit_recorder)):
    case = await _case(case_id, scope, session, principal)
    from app.db.base import new_uuid, utcnow
    from app.db.models import ClaimRecord
    claim = ClaimRecord(id=new_uuid(), case_id=case.id, **payload.model_dump())
    session.add(claim)
    await session.flush()
    existing = list((await session.execute(select(ClaimRecord).where(ClaimRecord.case_id == case.id, ClaimRecord.subject == claim.subject, ClaimRecord.predicate == claim.predicate, ClaimRecord.object != claim.object))).scalars().all())
    contradiction = None
    if existing:
        contradiction = await industry.create_contradiction(session, case_id=case.id, subject_key=claim.subject, predicate=claim.predicate, claims=[{"claim_id": row.id, "object": row.object, "source_ref": (row.source_refs or [None])[0]} for row in [*existing, claim]], verification_steps=[f"Verify {claim.predicate} for {claim.subject} using an independent source."])
        claim.status = "CONTRADICTORY"
    recorder.record("INVESTIGATE", target_resource=f"claim:{claim.id}", case_id=case.id, details={"contradiction_id": contradiction.id if contradiction else None})
    return {"claim_id": claim.id, "subject": claim.subject, "predicate": claim.predicate, "object": claim.object, "status": claim.status.value if hasattr(claim.status, "value") else claim.status, "contradiction": industry.contradiction_row(contradiction) if contradiction else None}


@router.post("/cases/{case_id}/approvals", status_code=201)
async def create_approval(case_id: str, payload: ApprovalCreate, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("INVESTIGATOR", "SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"))):
    case = await _case(case_id, scope, session, principal)
    item = await industry.create_approval(session, case_id=case.id, requested_by=principal.id, **payload.model_dump())
    return industry.approval_row(item)


@router.patch("/approvals/{approval_id}")
async def decide_approval(approval_id: str, payload: ApprovalDecision, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(require_roles("SUPERVISOR", "ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"))):
    item = await session.get(ApprovalRecord, approval_id)
    if item is None:
        from app.errors import NotFoundError
        raise NotFoundError("Approval request not found.")
    if item.case_id:
        await _case(item.case_id, scope, session, principal)
    updated = await industry.decide_approval(session, item, actor=principal, status=payload.status, reason=payload.reason)
    return industry.approval_row(updated)


@router.get("/cases/{case_id}/approvals")
async def list_approvals(case_id: str, scope: JurisdictionScope = Depends(get_scope), session: AsyncSession = Depends(get_db_session), principal: Principal = Depends(get_principal)):
    case = await _case(case_id, scope, session, principal)
    rows = list((await session.execute(select(ApprovalRecord).where(ApprovalRecord.case_id == case.id).order_by(ApprovalRecord.created_at.desc()))).scalars().all())
    return {"case_id": case.id, "items": [industry.approval_row(row) for row in rows]}
