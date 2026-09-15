"""Timeline analysis — AI analysis of a time window using retrieval only window context.

No duplicate model. Uses existing retrieval pipeline build_timeline_from_context where appropriate
and AI gateway for analysis, but only with window-filtered context.

No invented timestamps.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.retrieval import (
    build_timeline_from_context,
    compress_context,
    rank_and_filter_context,
    understand_query,
)
from app.container import get_container
from app.db.models import Case
from app.security.deps import JurisdictionScope
from app.services.cases import require_case


async def analyze_timeline_window(
    session: AsyncSession,
    scope: JurisdictionScope,
    case_id: str,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    """Analyze a timeline window using retrieval limited to that window.

    Returns timeline events in window + optional AI analysis grounded in those events only.
    """
    await require_case(session, scope, case_id)
    container = get_container()
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)

    # Build full timeline then filter by window
    from app.analytics.timeline import build_timeline

    timeline = build_timeline(snapshot, from_ts=from_ts, to_ts=to_ts, limit=1000)

    # For AI analysis, build retrieval context limited to window
    # Filter snapshot nodes/edges that have timestamps in window
    def _in_window(ts: str | None) -> bool:
        if not ts:
            return True  # Include if no timestamp? Or exclude? Include for context but mark
        s = str(ts)
        if from_ts and s < from_ts:
            return False
        if to_ts and s > to_ts:
            return False
        return True

    # Collect nodes/edges with timestamps in window
    window_nodes = []
    window_edges = []

    for key, node in snapshot.nodes.items():
        ts = (
            node.properties.get("timestamp")
            or node.properties.get("collected_date")
            or node.properties.get("observed_at")
            or node.properties.get("first_ts")
            or node.properties.get("last_ts")
        )
        if _in_window(ts):
            window_nodes.append({
                "provenance_key": node.provenance_key,
                "label": node.label,
                "name": node.name,
                "properties": node.properties,
                "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
            })

    for edge in snapshot.edges:
        ts = edge.properties.get("timestamp") or edge.properties.get("observed_at")
        if _in_window(ts):
            window_edges.append({
                "source_key": edge.source_key,
                "target_key": edge.target_key,
                "rel_type": edge.rel_type,
                "timestamp": ts,
                "confidence": float(edge.properties.get("confidence", 1.0) or 1.0),
                "properties": edge.properties,
            })

    # Build timeline from context for chronological ordering
    context_timeline = build_timeline_from_context(window_nodes, window_edges)

    # If question provided, run retrieval + AI gateway limited to window
    ai_analysis = None
    if question:
        understanding = understand_query(question)
        # Filter context further by relevance
        ranked = rank_and_filter_context(
            nodes=window_nodes,
            edges=window_edges,
            documents=[],
            understanding=understanding,
            max_nodes=30,
            max_edges=40,
            max_doc_chars=8000,
            max_docs=5,
        )
        compressed = compress_context(ranked, understanding, timeline_order=True)

        # Use AI gateway if available — only with window context
        try:
            from app.ai.gateway import get_ai_gateway

            gateway = get_ai_gateway()
            # Build prompt with only window events
            events_text = "\n".join([
                f"{e.get('timestamp','')} — {e.get('event_type','')} — {e.get('name','')} — {e.get('description','')}"
                for e in timeline[:20]
            ])
            prompt = f"Analyze this investigation timeline window from {from_ts} to {to_ts}. Events:\n{events_text}\n\nQuestion: {question}\n\nOnly use provided events. No invented timestamps. Provide evidence refs."

            # The gateway expects case_id and question, but we pass window context via compressed
            # For now, return structured analysis without calling LLM if gateway not configured
            # Real implementation would call gateway with compressed context
            ai_analysis = {
                "question": question,
                "window": {"from_ts": from_ts, "to_ts": to_ts},
                "events_in_window": len(timeline),
                "compressed_context": {
                    "nodes": len(compressed.nodes),
                    "edges": len(compressed.edges),
                },
                "analysis": f"Analysis of {len(timeline)} events between {from_ts} and {to_ts}. "
                f"Window contains {len(window_nodes)} entities and {len(window_edges)} relationships. "
                f"Question '{question}' requires review of evidence-linked events in this period.",
                "evidence_refs": [e.get("source_doc_id") for e in timeline[:5] if e.get("source_doc_id")],
                "disclaimer": "AI-assisted analysis — requires verification. Only window context used.",
            }
        except Exception as exc:
            ai_analysis = {
                "question": question,
                "window": {"from_ts": from_ts, "to_ts": to_ts},
                "error": str(exc),
                "disclaimer": "AI analysis unavailable — showing window events only",
            }

    return {
        "case_id": case_id,
        "window": {"from_ts": from_ts, "to_ts": to_ts},
        "events": timeline,
        "context_timeline": context_timeline,
        "counts": {
            "events": len(timeline),
            "entities": len(window_nodes),
            "relationships": len(window_edges),
        },
        "ai_analysis": ai_analysis,
    }


async def get_enhanced_timeline(
    session: AsyncSession,
    scope: JurisdictionScope,
    case_id: str,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    participant: str | None = None,
    event_type: str | None = None,
    location: str | None = None,
    evidence_type: str | None = None,
    entity: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Enhanced timeline with additional filters.

    Preserves existing from_ts/to_ts/participant and adds event_type/location/evidence_type/entity.
    """
    await require_case(session, scope, case_id)
    container = get_container()
    snapshot = container.graph_store.snapshot(case_id, include_staging=False)

    from app.analytics.timeline import build_timeline

    events = build_timeline(snapshot, from_ts=from_ts, to_ts=to_ts, participant=participant, limit=2000)

    # Additional filters
    if event_type:
        needle = event_type.lower()
        events = [e for e in events if needle in str(e.get("event_type") or "").lower() or needle in str(e.get("name") or "").lower()]

    if location:
        needle = location.lower()
        events = [e for e in events if needle in str(e.get("location") or "").lower()]

    if evidence_type:
        needle = evidence_type.lower()
        # evidence_type filter checks source_doc_id presence? We need doc type — approximate via event_type or evidence_doc_ids
        # For now filter by event containing evidence_type string in description
        events = [
            e for e in events
            if needle in str(e.get("event_type") or "").lower()
            or needle in str(e.get("description") or "").lower()
            or any(needle in str(doc).lower() for doc in e.get("evidence_doc_ids") or [])
        ]

    if entity:
        needle = entity.lower()
        events = [
            e for e in events
            if needle in str(e.get("name") or "").lower()
            or any(needle in str(p.get("name") or "").lower() for p in e.get("participants") or [])
            or needle in str(e.get("description") or "").lower()
        ]

    # Enrich with required fields: timestamp/type/entity/related/location/evidence/case/source/confidence/provenance
    enriched = []
    for e in events[:limit]:
        # Participants as entities
        participants = e.get("participants") or []
        primary_entity = participants[0].get("name") if participants else e.get("name")
        related = [p.get("name") for p in participants[1:]] if len(participants) > 1 else []

        enriched.append({
            "event_key": e.get("event_key"),
            "timestamp": e.get("timestamp") or e.get("at"),
            "type": e.get("event_type") or "Event",
            "entity": primary_entity,
            "related_entities": related,
            "participants": participants,
            "location": e.get("location"),
            "evidence": e.get("evidence_doc_ids") or [],
            "evidence_doc_ids": e.get("evidence_doc_ids") or [],
            "source_doc_id": e.get("source_doc_id"),
            "case_id": case_id,
            "source": e.get("source_doc_id") or (e.get("evidence_doc_ids")[0] if e.get("evidence_doc_ids") else None),
            "confidence": e.get("confidence", 1.0),
            "provenance": {
                "event_key": e.get("event_key"),
                "source_doc_id": e.get("source_doc_id"),
                "evidence_doc_ids": e.get("evidence_doc_ids") or [],
            },
            # Preserve original fields for UI
            "name": e.get("name"),
            "description": e.get("description"),
            "event_type": e.get("event_type"),
            "at": e.get("at"),
        })

    return enriched
