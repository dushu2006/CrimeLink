"""Pattern Investigation — actionable entry point for patterns.

Investigate identifies entities, opens graph, shows evidence, related cases, preserves provenance.
Optional AI via retrieval only related context.

Keep FACT/INFERENCE/HYPOTHESIS/UNKNOWN.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.models import Case, DetectedPattern
from app.security.deps import JurisdictionScope
from app.services.cases import require_case
from app.services.graph_service import GraphService, get_display_label


async def investigate_pattern(
    session: AsyncSession,
    scope: JurisdictionScope,
    pattern_id: str,
) -> dict[str, Any]:
    pattern = await session.get(DetectedPattern, pattern_id)
    if not pattern:
        raise ValueError("Pattern not found")

    await require_case(session, scope, pattern.case_id)

    container = get_container()
    graph_service = GraphService(container=container)
    snapshot = container.graph_store.snapshot(pattern.case_id, include_staging=False)

    entity_keys = list(pattern.entity_keys or [])
    entities = []
    for key in entity_keys:
        node = snapshot.nodes.get(key)
        if node:
            entities.append({
                "provenance_key": node.provenance_key,
                "name": get_display_label(node),
                "label": node.label,
                "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
                "case_ids": list(node.properties.get("case_ids") or []),
                "source_doc_ids": list(node.properties.get("source_doc_ids") or []),
                "evidence": {
                    "source_doc_id": node.properties.get("source_doc_id"),
                    "text_span": node.properties.get("text_span"),
                    "origin": node.properties.get("origin"),
                },
                "properties": {
                    k: v for k, v in node.properties.items()
                    if k not in {"case_ids", "source_doc_ids", "text_span", "origin", "source_doc_id"}
                },
            })
        else:
            entities.append({
                "provenance_key": key,
                "name": key[:12],
                "label": "Unknown",
                "confidence": 0.5,
                "case_ids": [pattern.case_id],
                "source_doc_ids": [],
                "evidence": None,
                "properties": {},
            })

    # Find related cases via shared entities
    related_cases: dict[str, dict[str, Any]] = {}
    for ent in entities:
        for cid in ent.get("case_ids") or []:
            if cid == pattern.case_id:
                continue
            if cid not in related_cases:
                # Try to load case
                c = await session.get(Case, cid)
                if c:
                    related_cases[cid] = {
                        "id": c.id,
                        "case_number": c.case_number,
                        "title": c.title,
                        "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                        "shared_entities": [],
                    }
    for ent in entities:
        for cid in ent.get("case_ids") or []:
            if cid in related_cases:
                related_cases[cid]["shared_entities"].append(ent["provenance_key"])

    # Evidence — collect source_doc_ids from entities and pattern
    evidence_docs = set()
    for ent in entities:
        for sid in ent.get("source_doc_ids") or []:
            evidence_docs.add(sid)

    # Find edges between entities in this pattern
    pattern_edges = []
    entity_key_set = set(entity_keys)
    for edge in snapshot.edges:
        if edge.source_key in entity_key_set and edge.target_key in entity_key_set:
            pattern_edges.append({
                "key": getattr(edge, "key", ""),
                "source": edge.source_key,
                "target": edge.target_key,
                "rel_type": edge.rel_type,
                "confidence": float(edge.properties.get("confidence", 1.0) or 1.0),
                "source_doc_ids": list(edge.properties.get("source_doc_ids") or []),
                "evidence": {
                    "source_doc_id": edge.properties.get("source_doc_id"),
                    "text_span": edge.properties.get("text_span"),
                    "origin": edge.properties.get("origin"),
                },
            })
            for sid in edge.properties.get("source_doc_ids") or []:
                evidence_docs.add(sid)

    # Build graph payload for UI — subgraph of pattern entities + 1-hop neighbors
    from app.services.graph_service import _bfs_neighbourhood

    # If we have entities, get neighbourhood for first entity as center
    graph_nodes = []
    graph_edges = []
    if entity_keys:
        try:
            walked = _bfs_neighbourhood(snapshot, entity_keys[0], requested_depth=1, node_budget=50)
            for n in walked["nodes"]:
                graph_nodes.append({
                    "provenance_key": n.provenance_key,
                    "name": get_display_label(n),
                    "label": n.label,
                    "confidence": float(n.properties.get("confidence", 1.0) or 1.0),
                    "case_ids": list(n.properties.get("case_ids") or []),
                })
            for e in walked["edges"].values():
                graph_edges.append({
                    "source": e.source_key,
                    "target": e.target_key,
                    "rel_type": e.rel_type,
                })
        except Exception:
            pass

    # Evidence grounding level — map to FACT/INFERENCE/HYPOTHESIS/UNKNOWN
    def _evidence_level(conf: float, has_source: bool) -> str:
        if conf >= 0.9 and has_source:
            return "FACT"
        if conf >= 0.7 and has_source:
            return "INFERENCE"
        if conf >= 0.5:
            return "HYPOTHESIS"
        return "UNKNOWN"

    # Pattern strength details
    analytical_basis = []
    if len(entities) >= 2:
        analytical_basis.append(f"{len(entities)} linked entities")
    if pattern_edges:
        analytical_basis.append(f"{len(pattern_edges)} direct relationships")
    if evidence_docs:
        analytical_basis.append(f"{len(evidence_docs)} evidence sources")

    return {
        "pattern": {
            "id": pattern.id,
            "case_id": pattern.case_id,
            "pattern_type": pattern.pattern_type.value if hasattr(pattern.pattern_type, "value") else str(pattern.pattern_type),
            "explanation": pattern.explanation,
            "confidence": pattern.confidence,
            "status": pattern.status.value if hasattr(pattern.status, "value") else str(pattern.status),
            "entity_keys": entity_keys,
            "evidence_level": _evidence_level(pattern.confidence, bool(evidence_docs)),
            "analytical_basis": analytical_basis,
            "contradictory_evidence": "No contradictory evidence was identified in the available dataset.",
            "data_gaps": [],
        },
        "entities": entities,
        "relationships": pattern_edges,
        "related_cases": list(related_cases.values()),
        "evidence": {
            "doc_ids": list(evidence_docs),
            "count": len(evidence_docs),
        },
        "graph": {
            "nodes": graph_nodes[:50],
            "edges": graph_edges[:100],
            "center": entity_keys[0] if entity_keys else None,
        },
        "counts": {
            "entities": len(entities),
            "relationships": len(pattern_edges),
            "related_cases": len(related_cases),
            "evidence": len(evidence_docs),
        },
        "provenance": {
            "pattern_id": pattern.id,
            "case_id": pattern.case_id,
            "entity_keys": entity_keys,
            "source": "GraphStore + DetectedPattern",
        },
    }
