"""Global CrimeLink search — exact identifiers first, then metadata, then graph lookup.

Priority:
1. exact identifiers (case ID, doc ID, entity key, pattern ID)
2. metadata filtering (case number/title, document filename)
3. graph/entity lookup (entity name, phone, vehicle, location)
4. semantic retrieval placeholder (future)

No LLM calls. Clean interface for future semantic search.

Each result includes enough context to understand why it matched.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, or_, func, cast
from sqlalchemy import String as SA_String
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.models import Case, CaseDocument, DetectedPattern
from app.security.deps import JurisdictionScope
from app.services.graph_service import GraphService


def _is_exact_id(q: str) -> bool:
    """Heuristic for exact ID search — UUID-like or known prefixes."""
    q = q.strip()
    if len(q) >= 8 and "-" in q:
        return True
    if q.upper().startswith(("CASE_", "CCTV-", "CDR-", "DOC-", "PAT-")):
        return True
    return False


async def global_search(
    session: AsyncSession,
    scope: JurisdictionScope,
    query: str,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Aggregated global search across entities, cases, evidence, documents, patterns, locations."""
    q = query.strip()
    if not q:
        return {
            "query": query,
            "categories": {
                "entities": [],
                "cases": [],
                "evidence": [],
                "documents": [],
                "patterns": [],
                "locations": [],
            },
            "total": 0,
        }

    container = get_container()
    graph_service = GraphService(container=container)
    from app.services.cases import visible_case_ids, case_summaries

    allowed_ids = await visible_case_ids(session, scope)
    allowed_list = sorted(allowed_ids) if allowed_ids else []

    # Results buckets
    entities: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    patterns: list[dict[str, Any]] = []
    locations: list[dict[str, Any]] = []

    # 1. Exact identifier search — case ID / case number
    # Cases by exact ID or case_number containing query
    if allowed_list:
        # Exact case ID
        exact_case = await session.get(Case, q)
        if exact_case and exact_case.id in allowed_ids:
            cases.append({
                "id": exact_case.id,
                "case_number": exact_case.case_number,
                "title": exact_case.title,
                "status": exact_case.status.value if hasattr(exact_case.status, "value") else str(exact_case.status),
                "match_reason": "exact_id",
                "match_context": f"Case ID exact match",
            })
        # Case number / title contains
        case_stmt = (
            select(Case)
            .where(Case.id.in_(allowed_list))
            .where(
                or_(
                    Case.case_number.ilike(f"%{q}%"),
                    Case.title.ilike(f"%{q}%"),
                )
            )
            .limit(limit)
        )
        case_rows = (await session.execute(case_stmt)).scalars().all()
        for c in case_rows:
            if any(r["id"] == c.id for r in cases):
                continue
            cases.append({
                "id": c.id,
                "case_number": c.case_number,
                "title": c.title,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                "match_reason": "metadata",
                "match_context": f"Case {c.case_number} — {c.title}",
            })

    # 2. Graph entity search — persons, phones, vehicles, etc.
    try:
        # General entity search
        entity_results = await graph_service.search(session, scope, q, limit=limit)
        for node in entity_results:
            label = str(node.get("label", "")).upper()
            entry = {
                "provenance_key": node.get("provenance_key"),
                "name": node.get("name") or node.get("display_name") or node.get("provenance_key", "")[:12],
                "label": node.get("label"),
                "confidence": node.get("confidence"),
                "case_ids": node.get("case_ids", []),
                "source_doc_ids": node.get("source_doc_ids", []),
                "match_reason": "graph_lookup",
                "match_context": f"{node.get('label')} — {node.get('name')} — {len(node.get('case_ids', []))} cases · {node.get('properties', {}).get('degree', '')}",
            }
            if label == "LOCATION":
                locations.append(entry)
            else:
                entities.append(entry)

        # Location specific search if not already
        if len(locations) < 5:
            loc_results = await graph_service.search(session, scope, q, entity_type="Location", limit=limit)
            for node in loc_results:
                if any(r["provenance_key"] == node.get("provenance_key") for r in locations):
                    continue
                locations.append({
                    "provenance_key": node.get("provenance_key"),
                    "name": node.get("name"),
                    "label": node.get("label"),
                    "case_ids": node.get("case_ids", []),
                    "match_reason": "location",
                    "match_context": f"Location — {node.get('name')}",
                })
    except Exception:
        # Search must not fail entirely if graph is unavailable
        pass

    # 3. Document search — filename, document_type
    if allowed_list:
        doc_stmt = (
            select(CaseDocument)
            .where(CaseDocument.case_id.in_(allowed_list))
            .where(CaseDocument.is_deleted.is_(False))
            .where(
                or_(
                    CaseDocument.filename.ilike(f"%{q}%"),
                    CaseDocument.id.ilike(f"%{q}%"),
                    cast(CaseDocument.document_type, SA_String).ilike(f"%{q}%"),
                )
            )
            .limit(limit)
        )
        doc_rows = (await session.execute(doc_stmt)).scalars().all()
        for d in doc_rows:
            documents.append({
                "id": d.id,
                "case_id": d.case_id,
                "filename": d.filename,
                "document_type": d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type),
                "content_hash": d.content_hash[:12] + "…" if d.content_hash else None,
                "match_reason": "metadata",
                "match_context": f"Document {d.filename} — {d.document_type}",
            })
            # Evidence category — documents that are evidence
            evidence.append({
                "id": d.id,
                "case_id": d.case_id,
                "filename": d.filename,
                "document_type": d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type),
                "evidence_type": "document",
                "match_reason": "evidence",
                "match_context": f"Evidence linked to {d.filename}",
            })

    # 4. Pattern search — explanation contains query, or entity_keys contain query
    if allowed_list:
        pat_stmt = (
            select(DetectedPattern)
            .where(DetectedPattern.case_id.in_(allowed_list))
            .where(
                or_(
                    DetectedPattern.explanation.ilike(f"%{q}%"),
                    DetectedPattern.id.ilike(f"%{q}%"),
                )
            )
            .limit(limit)
        )
        pat_rows = (await session.execute(pat_stmt)).scalars().all()
        for p in pat_rows:
            patterns.append({
                "id": p.id,
                "case_id": p.case_id,
                "pattern_type": p.pattern_type.value if hasattr(p.pattern_type, "value") else str(p.pattern_type),
                "explanation": p.explanation[:200] + "…" if len(p.explanation or "") > 200 else p.explanation,
                "confidence": p.confidence,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "entity_keys": list(p.entity_keys or [])[:5],
                "match_reason": "pattern",
                "match_context": f"Pattern {p.pattern_type} — {p.explanation[:80]}…",
            })

    # Also check entity_keys in patterns for query (person name search should surface patterns)
    if allowed_list and len(patterns) < limit:
        # Brute force: if query looks like a person name, find patterns whose entities contain that name
        # Use graph search results to get provenance_keys, then find patterns referencing them
        entity_keys = [e["provenance_key"] for e in entities[:10]]
        if entity_keys:
            pat_stmt2 = (
                select(DetectedPattern)
                .where(DetectedPattern.case_id.in_(allowed_list))
                .limit(200)
            )
            all_pats = (await session.execute(pat_stmt2)).scalars().all()
            for p in all_pats:
                if any(ek in (p.entity_keys or []) for ek in entity_keys):
                    if any(r["id"] == p.id for r in patterns):
                        continue
                    patterns.append({
                        "id": p.id,
                        "case_id": p.case_id,
                        "pattern_type": p.pattern_type.value if hasattr(p.pattern_type, "value") else str(p.pattern_type),
                        "explanation": p.explanation[:200] + "…" if len(p.explanation or "") > 200 else p.explanation,
                        "confidence": p.confidence,
                        "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                        "entity_keys": list(p.entity_keys or [])[:5],
                        "match_reason": "entity_in_pattern",
                        "match_context": f"Pattern involving {q} — {p.explanation[:80]}…",
                    })
                    if len(patterns) >= limit:
                        break

    total = len(entities) + len(cases) + len(evidence) + len(documents) + len(patterns) + len(locations)

    return {
        "query": q,
        "categories": {
            "entities": entities[:limit],
            "cases": cases[:limit],
            "evidence": evidence[:limit],
            "documents": documents[:limit],
            "patterns": patterns[:limit],
            "locations": locations[:limit],
        },
        "counts": {
            "entities": len(entities),
            "cases": len(cases),
            "evidence": len(evidence),
            "documents": len(documents),
            "patterns": len(patterns),
            "locations": len(locations),
        },
        "total": total,
    }
