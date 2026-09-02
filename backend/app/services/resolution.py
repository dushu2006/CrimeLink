"""Review queue 1 — entity resolution (PRD 9.2 / 10 / 14).

Wrongly linking two different people in a criminal graph is a serious error, so
the system never merges on a fuzzy match by itself.  It proposes; an
INVESTIGATOR decides, and must write *why* (``resolution_note`` is mandatory on
any decided row).

Merges are **reversible**: the absorbed node keeps the exact edge list it had
before the merge, so a wrongful merge can be undone rather than apologised for.
Rejections are **tombstoned** in the graph so the pair is never re-proposed on a
later re-ingest — without that, queue volume grows without bound and the queue
becomes ignorable.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.base import utcnow
from app.db.models import EntityResolutionItem
from app.domain.enums import ResolutionStatus
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.security.deps import JurisdictionScope, Principal

SLA_HOURS = 48


async def list_queue(
    session: AsyncSession,
    scope: JurisdictionScope,
    *,
    case_id: str | None = None,
    status: ResolutionStatus | None = None,
    limit: int = 100,
) -> list[dict]:
    from app.services.cases import require_case

    stmt = select(EntityResolutionItem)
    if case_id:
        await require_case(session, scope, case_id)
        stmt = stmt.where(EntityResolutionItem.case_id == case_id)
    else:
        from app.db.models import Case

        allowed = set(
            (await session.execute(select(Case.id).where(scope.case_filter()))).scalars().all()
        )
        stmt = stmt.where(EntityResolutionItem.case_id.in_(sorted(allowed)) if allowed else False)
    if status:
        stmt = stmt.where(EntityResolutionItem.status == status)
    items = (await session.execute(stmt.order_by(EntityResolutionItem.created_at.desc()).limit(limit))).scalars().all()
    return [_item_row(item, _age_hours(item)) for item in items]


def _age_hours(item: EntityResolutionItem) -> float:
    start = item.created_at or utcnow()
    return round((utcnow() - start).total_seconds() / 3600.0, 2)


def _item_row(item: EntityResolutionItem, age_hours: float) -> dict:
    container = get_container()
    source = container.graph_store.get_node(item.source_node_key)
    target = container.graph_store.get_node(item.target_node_key)
    return {
        "id": item.id,
        "case_id": item.case_id,
        "status": item.status.value,
        "similarity_score": item.similarity_score,
        "match_basis": item.match_basis.value,
        "evidence_doc_ids": list(item.evidence_doc_ids or []),
        "resolution_note": item.resolution_note,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "age_hours": age_hours,
        "sla_hours": SLA_HOURS,
        "sla_breached": item.status == ResolutionStatus.PENDING and age_hours > SLA_HOURS,
        "source": _node_side(source),
        "target": _node_side(target),
    }


def _node_side(node) -> dict:
    if node is None:
        return {"provenance_key": None, "name": "(node unavailable)", "label": "?"}
    return {
        "provenance_key": node.provenance_key,
        "name": node.name,
        "label": node.label,
        "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
        "aliases": list(node.properties.get("aliases") or []),
        "source_doc_ids": list(node.properties.get("source_doc_ids") or []),
        "case_ids": list(node.properties.get("case_ids") or []),
    }


async def _load(
    session: AsyncSession, scope: JurisdictionScope, queue_id: str
) -> EntityResolutionItem:
    from app.services.cases import require_case

    item = await session.get(EntityResolutionItem, queue_id)
    if item is None:
        raise NotFoundError("Review item not found.")
    await require_case(session, scope, item.case_id)
    return item


async def merge(
    session: AsyncSession,
    scope: JurisdictionScope,
    queue_id: str,
    *,
    principal: Principal,
    note: str,
) -> dict:
    """Approve a proposed merge.  The decision and its rationale are audited."""
    item = await _load(session, scope, queue_id)
    if item.status != ResolutionStatus.PENDING:
        raise ConflictError("This review item has already been resolved.")
    if not note or len(note.strip()) < 5:
        raise ValidationFailedError(
            "A written rationale is mandatory when merging two people."
        )
    container = get_container()
    result = container.graph_store.merge_persons(
        item.source_node_key, item.target_node_key, principal.id, queue_id=item.id
    )
    item.status = ResolutionStatus.MERGED
    item.resolved_by = principal.id
    item.resolution_note = note.strip()
    item.resolved_at = utcnow()
    await session.flush()
    return {
        "queue_id": item.id,
        "status": item.status.value,
        "kept_key": result.kept_key,
        "absorbed_key": result.absorbed_key,
        "rerouted_edges": result.rerouted_edges,
        "reversible": True,
    }


async def reject(
    session: AsyncSession,
    scope: JurisdictionScope,
    queue_id: str,
    *,
    principal: Principal,
    note: str,
) -> dict:
    """Reject a proposed merge and tombstone the pair."""
    item = await _load(session, scope, queue_id)
    if item.status != ResolutionStatus.PENDING:
        raise ConflictError("This review item has already been resolved.")
    if not note or len(note.strip()) < 5:
        raise ValidationFailedError(
            "A written rationale is mandatory when rejecting a match."
        )
    container = get_container()
    container.graph_store.tombstone_reject(
        item.source_node_key, item.target_node_key, principal.id
    )
    item.status = ResolutionStatus.REJECTED
    item.resolved_by = principal.id
    item.resolution_note = note.strip()
    item.resolved_at = utcnow()
    await session.flush()
    return {"queue_id": item.id, "status": item.status.value, "re_proposable": False}


async def unmerge(
    session: AsyncSession,
    scope: JurisdictionScope,
    queue_id: str,
    *,
    principal: Principal,
    note: str,
) -> dict:
    """Reverse a previously approved merge (wrongful merges are the worst failure mode)."""
    item = await _load(session, scope, queue_id)
    if item.status != ResolutionStatus.MERGED:
        raise ConflictError("Only a completed merge can be reversed.")
    if not note or len(note.strip()) < 5:
        raise ValidationFailedError("A written rationale is mandatory when reversing a merge.")
    container = get_container()
    container.graph_store.unmerge_persons(
        item.source_node_key, item.target_node_key, principal.id
    )
    item.status = ResolutionStatus.PENDING
    item.resolved_by = None
    item.resolution_note = note.strip()
    item.resolved_at = utcnow()
    await session.flush()
    return {"queue_id": item.id, "status": item.status.value, "reversed": True}


def sla_summary(items: list[dict]) -> dict:
    pending = [i for i in items if i["status"] == "PENDING"]
    breached = [i for i in pending if i["sla_breached"]]
    oldest = max((i["age_hours"] for i in pending), default=0.0)
    return {
        "pending": len(pending),
        "breached": len(breached),
        "oldest_pending_hours": oldest,
        "sla_hours": SLA_HOURS,
    }
