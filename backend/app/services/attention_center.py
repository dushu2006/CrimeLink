"""Attention Center — real data only, no fabrication.

Categories:
- Critical: patterns/findings requiring review (NEW status, high confidence)
- Investigation: new relevant connections/findings (recent patterns, findings, case links)
- Data quality: unresolved entities, quarantine records, low-confidence nodes
- Evidence: new documents, evidence with gaps

Every item links directly to real objects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    EntityResolutionItem,
    InvestigationFinding,
)
from app.security.deps import JurisdictionScope
from app.services.cases import visible_case_ids


async def get_attention_center(
    session: AsyncSession,
    scope: JurisdictionScope,
) -> dict[str, Any]:
    allowed_ids = await visible_case_ids(session, scope)
    allowed_list = sorted(allowed_ids) if allowed_ids else []

    if not allowed_list:
        return {
            "categories": {
                "critical": [],
                "investigation": [],
                "data_quality": [],
                "evidence": [],
            },
            "counts": {"critical": 0, "investigation": 0, "data_quality": 0, "evidence": 0},
            "total": 0,
        }

    critical: list[dict[str, Any]] = []
    investigation: list[dict[str, Any]] = []
    data_quality: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)

    # --- Critical: NEW patterns, high confidence patterns, NEW findings ---
    pat_critical_stmt = (
        select(DetectedPattern)
        .where(DetectedPattern.case_id.in_(allowed_list))
        .where(
            or_(
                DetectedPattern.confidence >= 0.85,
                DetectedPattern.status == "NEW",
            )
        )
        .order_by(DetectedPattern.confidence.desc())
        .limit(20)
    )
    pat_critical = (await session.execute(pat_critical_stmt)).scalars().all()
    for p in pat_critical:
        critical.append({
            "id": p.id,
            "type": "pattern",
            "case_id": p.case_id,
            "severity": "critical" if p.confidence >= 0.9 else "high",
            "title": f"{p.pattern_type.value if hasattr(p.pattern_type, 'value') else str(p.pattern_type)} — {p.confidence:.2f} confidence",
            "description": p.explanation[:150] if p.explanation else "",
            "timestamp": p.detected_at.isoformat() if getattr(p, "detected_at", None) else None,
            "link": {"kind": "pattern", "case_id": p.case_id, "pattern_id": p.id},
        })

    findings_critical_stmt = (
        select(InvestigationFinding)
        .where(InvestigationFinding.case_id.in_(allowed_list))
        .where(InvestigationFinding.status == "NEW")
        .order_by(InvestigationFinding.created_at.desc())
        .limit(20)
    )
    findings_critical = (await session.execute(findings_critical_stmt)).scalars().all()
    for f in findings_critical:
        critical.append({
            "id": f.id,
            "type": "finding",
            "case_id": f.case_id,
            "severity": "critical",
            "title": f.title[:80] if f.title else "New finding requires review",
            "description": f"Status: {f.status.value if hasattr(f.status, 'value') else str(f.status)}",
            "timestamp": f.created_at.isoformat() if f.created_at else None,
            "link": {"kind": "finding", "case_id": f.case_id, "finding_id": f.id},
        })

    # --- Investigation: recent patterns, recent findings, case connections ---
    recent_pat_stmt = (
        select(DetectedPattern)
        .where(DetectedPattern.case_id.in_(allowed_list))
        .order_by(DetectedPattern.detected_at.desc())
        .limit(20)
    )
    recent_pats = (await session.execute(recent_pat_stmt)).scalars().all()
    for p in recent_pats:
        # Skip if already in critical to avoid duplication? Keep but mark as investigation too if recent
        investigation.append({
            "id": p.id,
            "type": "pattern",
            "case_id": p.case_id,
            "severity": "info",
            "title": f"Pattern detected: {p.pattern_type.value if hasattr(p.pattern_type, 'value') else str(p.pattern_type)}",
            "description": p.explanation[:120] if p.explanation else "",
            "timestamp": p.detected_at.isoformat() if getattr(p, "detected_at", None) else None,
            "link": {"kind": "pattern", "case_id": p.case_id, "pattern_id": p.id},
        })

    recent_findings_stmt = (
        select(InvestigationFinding)
        .where(InvestigationFinding.case_id.in_(allowed_list))
        .order_by(InvestigationFinding.created_at.desc())
        .limit(20)
    )
    recent_findings = (await session.execute(recent_findings_stmt)).scalars().all()
    for f in recent_findings:
        investigation.append({
            "id": f.id,
            "type": "finding",
            "case_id": f.case_id,
            "severity": "info",
            "title": f.title[:80] if f.title else "Investigation finding",
            "description": f"Status: {f.status.value if hasattr(f.status, 'value') else str(f.status)}",
            "timestamp": f.created_at.isoformat() if f.created_at else None,
            "link": {"kind": "finding", "case_id": f.case_id, "finding_id": f.id},
        })

    # --- Data quality: unresolved entities, low confidence patterns ---
    unresolved_stmt = (
        select(EntityResolutionItem)
        .where(EntityResolutionItem.case_id.in_(allowed_list))
        .where(EntityResolutionItem.status == "PENDING")
        .order_by(EntityResolutionItem.created_at.desc())
        .limit(20)
    )
    unresolved = (await session.execute(unresolved_stmt)).scalars().all()
    for item in unresolved:
        data_quality.append({
            "id": item.id,
            "type": "unresolved_entity",
            "case_id": item.case_id,
            "severity": "warning",
            "title": f"Unresolved entity: {getattr(item, 'canonical_id', item.source_node_key)}",
            "description": f"Potential duplicate with {item.target_node_key} (similarity: {item.similarity_score:.2f})",
            "timestamp": item.created_at.isoformat() if item.created_at else None,
            "link": {"kind": "resolution", "case_id": item.case_id, "resolution_id": item.id},
        })

    # Low confidence nodes in graph — check snapshot for low confidence
    try:
        container = get_container()
        for case_id in allowed_list[:5]:  # limit to avoid heavy scan
            snapshot = container.graph_store.snapshot(case_id, include_staging=False)
            low_conf_nodes = [
                n for n in snapshot.nodes.values()
                if float(n.properties.get("confidence", 1.0) or 1.0) < 0.5
            ][:5]
            for n in low_conf_nodes:
                data_quality.append({
                    "id": n.provenance_key,
                    "type": "low_confidence_entity",
                    "case_id": case_id,
                    "severity": "warning",
                    "title": f"Low confidence entity: {n.name}",
                    "description": f"Confidence {n.properties.get('confidence', 0):.2f} — {n.label}",
                    "timestamp": None,
                    "link": {"kind": "entity", "case_id": case_id, "entity_key": n.provenance_key},
                })
    except Exception:
        pass

    # --- Evidence: recent documents, quarantine ---
    recent_docs_stmt = (
        select(CaseDocument)
        .where(CaseDocument.case_id.in_(allowed_list))
        .where(CaseDocument.is_deleted.is_(False))
        .order_by(CaseDocument.created_at.desc())
        .limit(20)
    )
    recent_docs = (await session.execute(recent_docs_stmt)).scalars().all()
    for d in recent_docs:
        evidence.append({
            "id": d.id,
            "type": "document",
            "case_id": d.case_id,
            "severity": "info",
            "title": f"New evidence: {d.filename}",
            "description": f"Type: {d.document_type.value if hasattr(d.document_type, 'value') else str(d.document_type)} — Case {d.case_id[:8]}",
            "timestamp": d.created_at.isoformat() if d.created_at else None,
            "link": {"kind": "document", "case_id": d.case_id, "document_id": d.id},
        })

    # Sort each category by timestamp desc
    def _sort_key(x):
        return x.get("timestamp") or ""

    critical.sort(key=_sort_key, reverse=True)
    investigation.sort(key=_sort_key, reverse=True)
    data_quality.sort(key=_sort_key, reverse=True)
    evidence.sort(key=_sort_key, reverse=True)

    return {
        "categories": {
            "critical": critical[:20],
            "investigation": investigation[:20],
            "data_quality": data_quality[:20],
            "evidence": evidence[:20],
        },
        "counts": {
            "critical": len(critical),
            "investigation": len(investigation),
            "data_quality": len(data_quality),
            "evidence": len(evidence),
        },
        "total": len(critical) + len(investigation) + len(data_quality) + len(evidence),
    }
