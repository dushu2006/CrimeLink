"""Case Dashboard — full page aggregation with real stats only.

No fabrication: all numbers come from DB and GraphStore.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, func
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
from app.services.cases import require_case
from app.services.graph_service import GraphService


async def get_case_dashboard(
    session: AsyncSession,
    scope: JurisdictionScope,
    case_id: str,
) -> dict[str, Any]:
    await require_case(session, scope, case_id)
    case = await session.get(Case, case_id)
    if not case:
        raise ValueError("Case not found")

    container = get_container()
    graph_service = GraphService(container=container)

    # Graph snapshot for stats
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)
    nodes = list(snapshot.nodes.values())
    edges = snapshot.edges

    # Filter out document artifact nodes for entity count
    from app.domain.enums import is_document_artifact_node

    actor_nodes = [n for n in nodes if not is_document_artifact_node(n)]
    entities_by_label: dict[str, int] = {}
    for n in actor_nodes:
        lbl = n.label
        entities_by_label[lbl] = entities_by_label.get(lbl, 0) + 1

    relationships_by_type: dict[str, int] = {}
    for e in edges:
        relationships_by_type[e.rel_type] = relationships_by_type.get(e.rel_type, 0) + 1

    # Documents
    doc_stmt = select(func.count(CaseDocument.id)).where(
        CaseDocument.case_id == case_id, CaseDocument.is_deleted.is_(False)
    )
    doc_count = (await session.execute(doc_stmt)).scalar() or 0

    # Evidence count — unique source_doc_ids in nodes + edges
    source_doc_ids = set()
    for n in nodes:
        for sid in n.properties.get("source_doc_ids") or []:
            source_doc_ids.add(sid)
        if n.properties.get("source_doc_id"):
            source_doc_ids.add(n.properties.get("source_doc_id"))
    for e in edges:
        for sid in e.properties.get("source_doc_ids") or []:
            source_doc_ids.add(sid)
        if e.properties.get("source_doc_id"):
            source_doc_ids.add(e.properties.get("source_doc_id"))
    evidence_count = len(source_doc_ids)

    # Patterns
    pat_stmt = select(func.count(DetectedPattern.id)).where(DetectedPattern.case_id == case_id)
    pattern_count = (await session.execute(pat_stmt)).scalar() or 0

    pat_list_stmt = select(DetectedPattern).where(DetectedPattern.case_id == case_id).limit(20)
    patterns = (await session.execute(pat_list_stmt)).scalars().all()

    # Unresolved entities
    res_stmt = select(func.count(EntityResolutionItem.id)).where(
        EntityResolutionItem.case_id == case_id,
        EntityResolutionItem.status == "PENDING",
    )
    unresolved_count = (await session.execute(res_stmt)).scalar() or 0

    res_list_stmt = (
        select(EntityResolutionItem)
        .where(EntityResolutionItem.case_id == case_id, EntityResolutionItem.status == "PENDING")
        .limit(10)
    )
    unresolved_items = (await session.execute(res_list_stmt)).scalars().all()

    # Findings
    findings_stmt = (
        select(InvestigationFinding)
        .where(InvestigationFinding.case_id == case_id)
        .order_by(InvestigationFinding.created_at.desc())
        .limit(10)
    )
    findings = (await session.execute(findings_stmt)).scalars().all()

    # High priority: patterns with high confidence, NEW status, or findings with status NEW
    high_priority = []
    for p in patterns:
        if p.confidence >= 0.8 or str(p.status.value if hasattr(p.status, "value") else p.status).upper() == "NEW":
            high_priority.append({
                "id": p.id,
                "type": "pattern",
                "pattern_type": p.pattern_type.value if hasattr(p.pattern_type, "value") else str(p.pattern_type),
                "explanation": p.explanation,
                "confidence": p.confidence,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            })
    for f in findings[:5]:
        if str(f.status.value if hasattr(f.status, "value") else f.status).upper() in ("NEW", "OPEN"):
            high_priority.append({
                "id": f.id,
                "type": "finding",
                "question": f.question[:120] if f.question else "",
                "status": f.status.value if hasattr(f.status, "value") else str(f.status),
                "created_at": f.created_at.isoformat() if f.created_at else None,
            })

    # Gaps: unresolved entities + patterns with contradictions
    gaps = []
    for item in unresolved_items:
        gaps.append({
            "id": item.id,
            "type": "unresolved_entity",
            "canonical_id": item.canonical_id,
            "member_keys": item.member_keys[:3] if item.member_keys else [],
            "reason": "Pending entity resolution",
        })
    for p in patterns:
        if p.explanation and "contradiction" in p.explanation.lower():
            gaps.append({
                "id": p.id,
                "type": "pattern_contradiction",
                "pattern_type": p.pattern_type.value if hasattr(p.pattern_type, "value") else str(p.pattern_type),
                "explanation": p.explanation[:150],
            })

    # Recent activity — from findings + patterns + documents
    recent_activity: list[dict[str, Any]] = []

    # Recent docs
    recent_docs_stmt = (
        select(CaseDocument)
        .where(CaseDocument.case_id == case_id, CaseDocument.is_deleted.is_(False))
        .order_by(CaseDocument.created_at.desc())
        .limit(5)
    )
    recent_docs = (await session.execute(recent_docs_stmt)).scalars().all()
    for d in recent_docs:
        recent_activity.append({
            "type": "document",
            "id": d.id,
            "title": d.filename,
            "timestamp": d.created_at.isoformat() if d.created_at else None,
            "description": f"Document uploaded: {d.filename}",
        })

    for p in sorted(patterns, key=lambda x: x.created_at or x.id, reverse=True)[:5]:
        recent_activity.append({
            "type": "pattern",
            "id": p.id,
            "title": f"{p.pattern_type.value if hasattr(p.pattern_type, 'value') else str(p.pattern_type)} pattern",
            "timestamp": p.created_at.isoformat() if p.created_at else None,
            "description": p.explanation[:100] if p.explanation else "",
        })

    for f in findings[:5]:
        recent_activity.append({
            "type": "finding",
            "id": f.id,
            "title": f.question[:60] if f.question else "Finding",
            "timestamp": f.created_at.isoformat() if f.created_at else None,
            "description": f"Finding {f.status.value if hasattr(f.status, 'value') else str(f.status)}",
        })

    # Sort recent activity by timestamp desc, handling None
    def _ts_key(x):
        return x.get("timestamp") or ""

    recent_activity.sort(key=_ts_key, reverse=True)

    # Build header
    header = {
        "id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "status": case.status.value if hasattr(case.status, "value") else str(case.status),
        "jurisdiction_id": case.jurisdiction_id,
        "dataset_id": case.dataset_id,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if getattr(case, "updated_at", None) else None,
        "closed_at": case.closed_at.isoformat() if getattr(case, "closed_at", None) and case.closed_at else None,
        "description": case.title,  # Use title as description if no dedicated field
    }

    stats = {
        "entities": len(actor_nodes),
        "entities_by_label": entities_by_label,
        "relationships": len(edges),
        "relationships_by_type": relationships_by_type,
        "evidence": evidence_count,
        "documents": doc_count,
        "patterns": pattern_count,
        "unresolved": unresolved_count,
    }

    intelligence = {
        "high_priority": high_priority[:10],
        "gaps": gaps[:10],
        "unresolved": [
            {
                "id": item.id,
                "canonical_id": item.canonical_id,
                "member_count": len(item.member_keys or []),
            }
            for item in unresolved_items[:5]
        ],
        "patterns": [
            {
                "id": p.id,
                "pattern_type": p.pattern_type.value if hasattr(p.pattern_type, "value") else str(p.pattern_type),
                "confidence": p.confidence,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "entity_count": len(p.entity_keys or []),
            }
            for p in patterns[:10]
        ],
        "recent_activity": recent_activity[:15],
    }

    return {
        "header": header,
        "stats": stats,
        "intelligence": intelligence,
    }
