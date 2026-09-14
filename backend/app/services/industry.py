"""Durable investigation workflow services.

These services are intentionally deterministic and database-first.  They do not
make legal decisions; they create reviewable work, preserve provenance, and
surface what is unknown or contradictory.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_uuid, utcnow
from app.db.models import (
    ApprovalRecord,
    Case,
    CaseDocument,
    ClaimRecord,
    ContradictionRecord,
    EvidenceCustodyEvent,
    HypothesisRecord,
    InvestigationReport,
    InvestigationTask,
    InvestigatorNote,
    ModelRegistryEntry,
)
from app.domain.enums import (
    ApprovalStatus,
    ApprovalType,
    CustodyEventType,
    HypothesisStatus,
    InformationClassification,
    TaskPriority,
    TaskStatus,
    UncertaintyState,
)
from app.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError
from app.security.classification import require_classification


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _hash_payload(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(data).hexdigest()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ValidationFailedError("Dates must be ISO-8601 values.") from exc


async def custody_events(session: AsyncSession, evidence_id: str) -> list[EvidenceCustodyEvent]:
    rows = await session.execute(
        select(EvidenceCustodyEvent)
        .where(EvidenceCustodyEvent.evidence_id == evidence_id)
        .order_by(EvidenceCustodyEvent.created_at, EvidenceCustodyEvent.id)
    )
    return list(rows.scalars().all())


async def record_custody(
    session: AsyncSession,
    *,
    document: CaseDocument,
    event_type: CustodyEventType,
    actor_id: str | None,
    details: dict | None = None,
    location: str | None = None,
) -> EvidenceCustodyEvent:
    event = EvidenceCustodyEvent(
        id=new_uuid(),
        evidence_id=document.id,
        case_id=document.case_id,
        event_type=event_type,
        actor_id=actor_id,
        object_hash=document.content_hash,
        location=location or document.storage_key,
        details=details or {},
    )
    session.add(event)
    await session.flush()
    return event


async def verify_integrity(session: AsyncSession, container, document: CaseDocument, actor_id: str | None = None) -> dict:
    """Verify bytes and append HASH_VERIFIED or a tamper alert event."""
    from app.domain.provenance import content_hash

    try:
        raw = container.object_store.get(container.settings.minio_bucket_documents, document.storage_key)
    except Exception as exc:  # noqa: BLE001
        return {"document_id": document.id, "state": "MISSING", "match": False, "error": str(exc)}
    actual = content_hash(raw)
    match = actual == document.content_hash
    event = await record_custody(
        session,
        document=document,
        event_type=CustodyEventType.HASH_VERIFIED,
        actor_id=actor_id,
        details={"computed_hash": actual, "match": match},
    )
    return {
        "document_id": document.id,
        "recorded_hash": document.content_hash,
        "computed_hash": actual,
        "state": "VERIFIED" if match else "TAMPERED",
        "match": match,
        "custody_event_id": event.id,
        "verified_at": utcnow().isoformat(),
    }


def task_row(task: InvestigationTask) -> dict:
    return {
        "id": task.id,
        "case_id": task.case_id,
        "title": task.title,
        "description": task.description,
        "owner_id": task.owner_id,
        "creator_id": task.creator_id,
        "priority": _enum_value(task.priority),
        "status": _enum_value(task.status),
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "linked_evidence": task.linked_evidence or [],
        "linked_entities": task.linked_entities or [],
        "linked_findings": task.linked_findings or [],
        "comments": task.comments or [],
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


async def create_task(session: AsyncSession, *, case: Case, actor_id: str, **values: Any) -> InvestigationTask:
    task = InvestigationTask(
        id=new_uuid(),
        case_id=case.id,
        creator_id=actor_id,
        title=str(values["title"]).strip(),
        description=str(values.get("description", "")).strip(),
        owner_id=values.get("owner_id"),
        priority=TaskPriority(values.get("priority", TaskPriority.MEDIUM)),
        status=TaskStatus(values.get("status", TaskStatus.TODO)),
        due_at=_parse_datetime(values.get("due_at")),
        linked_evidence=list(values.get("linked_evidence", [])),
        linked_entities=list(values.get("linked_entities", [])),
        linked_findings=list(values.get("linked_findings", [])),
    )
    if not task.title:
        raise ValidationFailedError("Task title is required.")
    session.add(task)
    await session.flush()
    return task


async def list_tasks(session: AsyncSession, case_id: str) -> list[InvestigationTask]:
    result = await session.execute(
        select(InvestigationTask).where(InvestigationTask.case_id == case_id).order_by(InvestigationTask.created_at.desc())
    )
    return list(result.scalars().all())


async def update_task(session: AsyncSession, task: InvestigationTask, actor_id: str, values: dict) -> InvestigationTask:
    if "status" in values:
        next_status = TaskStatus(values["status"])
        task.status = next_status
        if next_status == TaskStatus.COMPLETED:
            task.completed_at = utcnow()
    for key in ("title", "description", "owner_id"):
        if key in values and values[key] is not None:
            setattr(task, key, values[key])
    if "priority" in values:
        task.priority = TaskPriority(values["priority"])
    if "due_at" in values:
        task.due_at = _parse_datetime(values["due_at"])
    if "comment" in values and values["comment"]:
        task.comments = [*(task.comments or []), {"author_id": actor_id, "text": str(values["comment"]), "at": utcnow().isoformat()}]
    await session.flush()
    return task


def note_row(note: InvestigatorNote) -> dict:
    return {
        "id": note.id, "case_id": note.case_id, "author_id": note.author_id,
        "text": note.text, "classification": _enum_value(note.classification),
        "linked_evidence": note.linked_evidence or [], "linked_entities": note.linked_entities or [],
        "linked_findings": note.linked_findings or [], "version": note.version,
        "supersedes_id": note.supersedes_id, "created_at": note.created_at.isoformat() if note.created_at else None,
    }


async def create_note(session: AsyncSession, *, case: Case, actor, text: str, classification: str, **links) -> InvestigatorNote:
    level = InformationClassification(classification)
    require_classification(actor, level)
    if not text.strip():
        raise ValidationFailedError("Note text is required.")
    note = InvestigatorNote(
        id=new_uuid(), case_id=case.id, author_id=actor.id, text=text.strip(),
        classification=level, linked_evidence=list(links.get("linked_evidence", [])),
        linked_entities=list(links.get("linked_entities", [])), linked_findings=list(links.get("linked_findings", [])),
    )
    session.add(note)
    await session.flush()
    return note


async def list_notes(session: AsyncSession, case_id: str, actor) -> list[InvestigatorNote]:
    result = await session.execute(select(InvestigatorNote).where(InvestigatorNote.case_id == case_id).order_by(InvestigatorNote.created_at.desc()))
    rows = list(result.scalars().all())
    return [row for row in rows if _can_read(actor, row.classification)]


def _can_read(actor, classification) -> bool:
    from app.security.classification import can_access
    return can_access(actor, classification)


def hypothesis_row(item: HypothesisRecord) -> dict:
    return {
        "id": item.id, "case_id": item.case_id, "hypothesis": item.statement,
        "statement": item.statement, "supporting_evidence": item.supporting_evidence or [],
        "contradicting_evidence": item.contradicting_evidence or [], "unknown_information": item.unknown_information or [],
        "investigator_id": item.investigator_id, "status": _enum_value(item.status),
        "assessment": item.assessment, "confidence": item.confidence,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


async def create_hypothesis(session: AsyncSession, *, case: Case, actor_id: str, values: dict) -> HypothesisRecord:
    statement = str(values.get("statement") or values.get("hypothesis") or "").strip()
    if not statement:
        raise ValidationFailedError("Hypothesis statement is required.")
    confidence = values.get("confidence")
    if confidence is not None and not 0 <= float(confidence) <= 1:
        raise ValidationFailedError("Hypothesis assessment must be between 0 and 1.")
    item = HypothesisRecord(
        id=new_uuid(), case_id=case.id, statement=statement,
        supporting_evidence=list(values.get("supporting_evidence", [])),
        contradicting_evidence=list(values.get("contradicting_evidence", [])),
        unknown_information=list(values.get("unknown_information", [])), investigator_id=actor_id,
        status=HypothesisStatus(values.get("status", HypothesisStatus.OPEN)),
        assessment=str(values.get("assessment", "")).strip(), confidence=float(confidence) if confidence is not None else None,
    )
    session.add(item)
    await session.flush()
    return item


async def list_hypotheses(session: AsyncSession, case_id: str) -> list[HypothesisRecord]:
    result = await session.execute(select(HypothesisRecord).where(HypothesisRecord.case_id == case_id).order_by(HypothesisRecord.updated_at.desc()))
    return list(result.scalars().all())


async def create_contradiction(session: AsyncSession, *, case_id: str, subject_key: str, predicate: str, claims: list[dict], verification_steps: list[str]) -> ContradictionRecord:
    if len(claims) < 2:
        raise ValidationFailedError("A contradiction requires at least two claims.")
    sources = {str(claim.get("source_ref")) for claim in claims if claim.get("source_ref")}
    explanation = f"{len(claims)} claims for {subject_key} disagree on {predicate}; sources: {', '.join(sorted(sources)) or 'not recorded'}."
    item = ContradictionRecord(
        id=new_uuid(), case_id=case_id, subject_key=subject_key, predicate=predicate,
        claims=claims, explanation=explanation, verification_steps=verification_steps or [f"Verify {predicate} against an independent source."],
        status=UncertaintyState.CONTRADICTORY,
    )
    session.add(item)
    await session.flush()
    return item


def contradiction_row(item: ContradictionRecord) -> dict:
    return {
        "id": item.id, "case_id": item.case_id, "subject": item.subject_key, "subject_key": item.subject_key,
        "predicate": item.predicate, "claims": item.claims or [], "explanation": item.explanation,
        "verification_steps": item.verification_steps or [], "status": _enum_value(item.status),
        "detected_by": item.detected_by, "reviewed_by": item.reviewed_by,
        "review_note": item.review_note, "created_at": item.created_at.isoformat() if item.created_at else None,
    }


async def list_contradictions(session: AsyncSession, case_id: str) -> list[ContradictionRecord]:
    result = await session.execute(select(ContradictionRecord).where(ContradictionRecord.case_id == case_id).order_by(ContradictionRecord.created_at.desc()))
    return list(result.scalars().all())


async def create_approval(session: AsyncSession, *, case_id: str | None, object_type: str, object_id: str, approval_type: ApprovalType, requested_by: str, object_payload: Any, reason: str | None = None) -> ApprovalRecord:
    item = ApprovalRecord(
        id=new_uuid(), case_id=case_id, object_type=object_type, object_id=object_id,
        approval_type=approval_type, status=ApprovalStatus.PENDING, requested_by=requested_by,
        object_hash=_hash_payload(object_payload), reason=reason,
    )
    session.add(item)
    await session.flush()
    return item


async def decide_approval(session: AsyncSession, item: ApprovalRecord, *, actor, status: str, reason: str | None = None) -> ApprovalRecord:
    if actor.id == item.requested_by:
        raise PermissionDeniedError("A requester cannot approve their own controlled action.")
    if actor.role.value not in {"SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN", "ADMIN"}:
        raise PermissionDeniedError("Supervisor approval is required for this action.")
    item.status = ApprovalStatus(status)
    item.decided_by = actor.id
    item.decided_at = utcnow()
    item.reason = reason or item.reason
    await session.flush()
    return item


def approval_row(item: ApprovalRecord) -> dict:
    return {"id": item.id, "case_id": item.case_id, "object_type": item.object_type, "object_id": item.object_id, "approval_type": _enum_value(item.approval_type), "status": _enum_value(item.status), "requested_by": item.requested_by, "decided_by": item.decided_by, "object_hash": item.object_hash, "reason": item.reason, "created_at": item.created_at.isoformat() if item.created_at else None, "decided_at": item.decided_at.isoformat() if item.decided_at else None}


async def create_report(session: AsyncSession, *, case: Case, actor_id: str, content: dict, evidence_index: list[dict]) -> InvestigationReport:
    """Create a versioned draft from authoritative records; AI cannot approve it."""
    result = await session.execute(select(InvestigationReport).where(InvestigationReport.case_id == case.id))
    previous = list(result.scalars().all())
    version = max((item.version for item in previous), default=0) + 1
    report = InvestigationReport(
        id=new_uuid(), case_id=case.id, version=version, content=content,
        evidence_index=evidence_index, object_hash=_hash_payload({"content": content, "evidence_index": evidence_index}),
        status="DRAFT", generated_by=actor_id,
    )
    session.add(report)
    await session.flush()
    return report


def report_row(report: InvestigationReport) -> dict:
    return {"id": report.id, "case_id": report.case_id, "version": report.version, "content": report.content or {}, "evidence_index": report.evidence_index or [], "object_hash": report.object_hash, "status": report.status, "generated_by": report.generated_by, "approved_by": report.approved_by, "approved_at": report.approved_at.isoformat() if report.approved_at else None, "created_at": report.created_at.isoformat() if report.created_at else None}


async def register_model(session: AsyncSession, values: dict) -> ModelRegistryEntry:
    item = ModelRegistryEntry(id=new_uuid(), name=values["name"], provider=values["provider"], version=values["version"], role=values["role"], prompt_version=values.get("prompt_version"), deployment_version=values.get("deployment_version"), performance=values.get("performance", {}), latency_ms=values.get("latency_ms"), cost=values.get("cost"), evaluation_status=values.get("evaluation_status", "UNTESTED"), active=bool(values.get("active", False)))
    session.add(item)
    await session.flush()
    return item


def model_row(item: ModelRegistryEntry) -> dict:
    return {"id": item.id, "name": item.name, "provider": item.provider, "version": item.version, "role": item.role, "prompt_version": item.prompt_version, "deployment_version": item.deployment_version, "performance": item.performance or {}, "latency_ms": item.latency_ms, "cost": item.cost, "evaluation_status": item.evaluation_status, "active": item.active, "created_at": item.created_at.isoformat() if item.created_at else None}
