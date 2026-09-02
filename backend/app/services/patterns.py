"""Review queue 2 — pattern findings (PRD 11.3 / 10 / 14).

A detected pattern is an accusation-adjacent output.  It is therefore modelled as
a durable review item with its own lifecycle (``NEW → REVIEWED / DISMISSED /
ESCALATED``) and is **never** shown to an investigator as a confirmed finding.

The dismissal-rate report exists as the structural answer to alert fatigue: if a
pattern type is dismissed more than 70% of the time over 30 days, an
administrator is told the thresholds need recalibration instead of the queue
quietly training investigators to ignore it.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.base import utcnow
from app.db.models import DetectedPattern
from app.domain.enums import PatternStatus, PatternType
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.security.deps import JurisdictionScope, Principal

_REVIEWABLE = (PatternStatus.NEW, PatternStatus.REVIEWED, PatternStatus.ESCALATED)


def _row(pattern: DetectedPattern) -> dict:
    container = get_container()
    entities = []
    for key in pattern.entity_keys or []:
        node = container.graph_store.get_node(key)
        if node is None:
            entities.append({"provenance_key": key, "name": key[:8], "label": "?"})
        else:
            entities.append(
                {
                    "provenance_key": key,
                    "name": node.name,
                    "label": node.label,
                    "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
                }
            )
    return {
        "id": pattern.id,
        "case_id": pattern.case_id,
        "pattern_type": pattern.pattern_type.value,
        "confidence": pattern.confidence,
        "status": pattern.status.value,
        "explanation": pattern.explanation,
        "details": pattern.details or {},
        "entity_keys": list(pattern.entity_keys or []),
        "entities": entities,
        "evidence_doc_ids": list(pattern.evidence_doc_ids or []),
        "detected_at": pattern.detected_at.isoformat() if pattern.detected_at else None,
        "reviewed_at": pattern.reviewed_at.isoformat() if pattern.reviewed_at else None,
        "review_note": pattern.review_note,
    }


async def list_patterns(
    session: AsyncSession,
    scope: JurisdictionScope,
    *,
    case_id: str | None = None,
    status: PatternStatus | None = None,
    pattern_type: PatternType | None = None,
    limit: int = 100,
) -> list[dict]:
    from app.db.models import Case
    from app.services.cases import require_case

    stmt = select(DetectedPattern)
    if case_id:
        await require_case(session, scope, case_id)
        stmt = stmt.where(DetectedPattern.case_id == case_id)
    else:
        allowed = set(
            (await session.execute(select(Case.id).where(scope.case_filter()))).scalars().all()
        )
        stmt = stmt.where(DetectedPattern.case_id.in_(sorted(allowed)) if allowed else False)
    if status:
        stmt = stmt.where(DetectedPattern.status == status)
    if pattern_type:
        stmt = stmt.where(DetectedPattern.pattern_type == pattern_type)
    rows = (
        await session.execute(stmt.order_by(DetectedPattern.detected_at.desc()).limit(limit))
    ).scalars().all()
    return [_row(r) for r in rows]


async def review(
    session: AsyncSession,
    scope: JurisdictionScope,
    pattern_id: str,
    *,
    principal: Principal,
    decision: PatternStatus,
    note: str | None = None,
) -> dict:
    from app.services.cases import require_case

    pattern = await session.get(DetectedPattern, pattern_id)
    if pattern is None:
        raise NotFoundError("Pattern finding not found.")
    await require_case(session, scope, pattern.case_id)
    if pattern.status not in _REVIEWABLE:
        raise ConflictError("This finding can no longer be reviewed.")
    if decision not in (PatternStatus.REVIEWED, PatternStatus.DISMISSED, PatternStatus.ESCALATED):
        raise ValidationFailedError("Unsupported review decision.")
    if decision in (PatternStatus.DISMISSED, PatternStatus.ESCALATED) and (
        not note or len(note.strip()) < 5
    ):
        raise ValidationFailedError(
            "A written rationale is mandatory when dismissing or escalating a finding."
        )
    pattern.status = decision
    pattern.reviewed_by = principal.id
    pattern.review_note = (note or "").strip() or None
    pattern.reviewed_at = utcnow()
    await session.flush()
    return _row(pattern)


async def dismissal_report(
    session: AsyncSession,
    scope: JurisdictionScope,
    *,
    case_id: str | None = None,
    days: int = 30,
) -> dict:
    """Aggregate dismissal rates per pattern type (alert-fatigue control)."""
    from datetime import timedelta

    from app.db.models import Case

    cutoff = utcnow() - timedelta(days=days)
    stmt = select(DetectedPattern).where(DetectedPattern.detected_at >= cutoff)
    if case_id:
        from app.services.cases import require_case

        await require_case(session, scope, case_id)
        stmt = stmt.where(DetectedPattern.case_id == case_id)
    else:
        allowed = set(
            (await session.execute(select(Case.id).where(scope.case_filter()))).scalars().all()
        )
        stmt = stmt.where(DetectedPattern.case_id.in_(sorted(allowed)) if allowed else False)
    rows = (await session.execute(stmt)).scalars().all()

    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "dismissed": 0})
    for row in rows:
        key = row.pattern_type.value
        counts[key]["total"] += 1
        if row.status == PatternStatus.DISMISSED:
            counts[key]["dismissed"] += 1

    settings = get_container().settings
    threshold = float(settings.pattern_dismissal_rate_alert)
    report = []
    for key, value in sorted(counts.items()):
        rate = value["dismissed"] / value["total"] if value["total"] else 0.0
        report.append(
            {
                "pattern_type": key,
                "total": value["total"],
                "dismissed": value["dismissed"],
                "dismissal_rate": round(rate, 3),
                "needs_recalibration": value["total"] >= 3 and rate >= threshold,
            }
        )
    return {
        "window_days": days,
        "threshold": threshold,
        "patterns": report,
    }
