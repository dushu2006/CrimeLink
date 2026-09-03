"""Jurisdiction-scoped explorers for documents, entities and relationships.

The equivalents under ``/database/*`` are deliberately ADMIN-only operational
views of the whole deployment.  Investigators need the same resources filtered
to the cases they may actually see, so these endpoints apply the ordinary
jurisdiction scope instead of an admin role check.

Every row returned here carries whatever provenance actually exists for it, and
nothing is synthesised when it does not.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container
from app.db.models import Case, CaseDocument, SourceReference
from app.db.session import get_db_session
from app.errors import NotFoundError
from app.security.deps import (
    JurisdictionScope,
    Principal,
    get_principal,
    get_scope,
)
from app.services import cases as case_service
from app.services.graph_service import GraphService, _edge_row, _node_row

router = APIRouter(prefix="/explore", tags=["explore"])


async def _visible_case_ids(session: AsyncSession, scope: JurisdictionScope) -> set[str]:
    # scope.case_filter() also honours active cross-jurisdiction grants, which
    # a plain jurisdiction match would silently drop -- a granted case would
    # then be openable by id but invisible in every listing.
    rows = (await session.execute(select(Case.id).where(scope.case_filter()))).scalars()
    return set(rows)


@router.get("/documents")
async def documents(
    case_id: str | None = Query(None),
    status: str | None = Query(None),
    quarantined: bool | None = Query(None),
    q: str | None = Query(None, description="Filename contains"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Documents across every case the caller may see."""
    allowed = await _visible_case_ids(session, scope)
    if not allowed:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    query = select(CaseDocument).where(CaseDocument.case_id.in_(allowed))
    if case_id:
        query = query.where(CaseDocument.case_id == case_id)
    if status:
        query = query.where(CaseDocument.ingestion_status == status)
    if quarantined is not None:
        query = query.where(CaseDocument.quarantined.is_(quarantined))
    if q:
        query = query.where(CaseDocument.filename.ilike(f"%{q}%"))

    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                query.order_by(CaseDocument.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )

    # Reference counts come from the provenance table, never from a guess.
    counts: dict[str, int] = {}
    if rows:
        counts = {
            row[0]: int(row[1])
            for row in (
                await session.execute(
                    select(SourceReference.doc_id, func.count(SourceReference.id))
                    .where(SourceReference.doc_id.in_([d.id for d in rows]))
                    .group_by(SourceReference.doc_id)
                )
            ).all()
        }

    case_numbers = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(Case.id, Case.case_number).where(
                    Case.id.in_({d.case_id for d in rows})
                )
            )
        ).all()
    }

    return {
        "items": [
            {
                "id": d.id,
                "case_id": d.case_id,
                "case_number": case_numbers.get(d.case_id),
                "filename": d.filename,
                "document_type": d.document_type.value,
                "ingestion_status": d.ingestion_status.value,
                "source_confidence": d.source_confidence.value,
                "quarantined": d.quarantined,
                "failure_reason": d.failure_reason,
                "size_bytes": d.size_bytes,
                "language": d.language,
                "reference_count": counts.get(d.id, 0),
                "origin": (d.source_metadata or {}).get("document_origin"),
                "relative_path": (d.source_metadata or {}).get("relative_path"),
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/documents/{doc_id}")
async def document_detail(
    doc_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        raise NotFoundError("Document not found.")
    case = await case_service.require_case(session, scope, document.case_id)

    reference_total = (
        await session.execute(
            select(func.count(SourceReference.id)).where(SourceReference.doc_id == doc_id)
        )
    ).scalar_one()

    # Entities extracted from this document, straight out of the graph.
    entities: list[dict] = []
    try:
        snapshot = get_container().graph_store.snapshot(document.case_id, include_staging=True)
        for node in snapshot.nodes.values():
            if doc_id in (node.properties.get("source_doc_ids") or []):
                entities.append(_node_row(node))
    except Exception:  # noqa: BLE001 - the document view must still render
        entities = []

    return {
        "id": document.id,
        "case": {"id": case.id, "case_number": case.case_number, "title": case.title},
        "filename": document.filename,
        "document_type": document.document_type.value,
        "ingestion_status": document.ingestion_status.value,
        "ingestion_stage": document.ingestion_stage,
        "source_confidence": document.source_confidence.value,
        "quarantined": document.quarantined,
        "failure_reason": document.failure_reason,
        "content_hash": document.content_hash,
        "size_bytes": document.size_bytes,
        "language": document.language,
        "origin": (document.source_metadata or {}).get("document_origin"),
        "relative_path": (document.source_metadata or {}).get("relative_path"),
        "reference_count": int(reference_total),
        "entities": entities[:200],
        "entity_count": len(entities),
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


@router.get("/entities")
async def entities(
    label: str | None = Query(None),
    q: str | None = Query(None, description="Name contains"),
    case_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Entities from the graph, restricted to visible cases."""
    allowed = await _visible_case_ids(session, scope)
    if not allowed:
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "labels": {}}
    targets = {case_id} & allowed if case_id else allowed

    seen: dict[str, dict] = {}
    labels: dict[str, int] = {}
    store = get_container().graph_store
    for cid in targets:
        try:
            snapshot = store.snapshot(cid, include_staging=False)
        except Exception:  # noqa: BLE001 - a missing case graph is not fatal
            continue
        for node in snapshot.nodes.values():
            if node.label == "Case" or node.provenance_key in seen:
                continue
            row = _node_row(node)
            if label and row["label"] != label:
                continue
            if q and q.lower() not in str(row["name"]).lower():
                continue
            seen[node.provenance_key] = row
            labels[row["label"]] = labels.get(row["label"], 0) + 1

    items = sorted(seen.values(), key=lambda r: (r["label"], str(r["name"])))

    # A full node row lists every case and document the entity touches, which
    # for a busy phone is thousands of ids the table never draws.  Send the
    # displayed columns plus the counts behind them.
    def _summary(row: dict) -> dict:
        return {
            "provenance_key": row["provenance_key"],
            "label": row["label"],
            "name": row["name"],
            "confidence": row.get("confidence"),
            "staging": row.get("staging", False),
            "case_count": len(row.get("case_ids") or []),
            "document_count": len(row.get("source_doc_ids") or []),
            "evidence": row.get("evidence"),
        }

    return {
        "items": [_summary(row) for row in items[offset : offset + limit]],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "labels": dict(sorted(labels.items())),
    }


@router.get("/entities/{provenance_key}")
async def entity_detail(
    provenance_key: str,
    rel_limit: int = Query(100, ge=1, le=500),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """One entity, its relationships, and the documents that evidence it."""
    service = GraphService()
    node = await service._assert_node_in_scope(session, scope, provenance_key)
    row = _node_row(node)

    neighbours: list[dict] = []
    seen_edges: set[str] = set()
    store = get_container().graph_store
    for case_id in row["case_ids"]:
        try:
            snapshot = store.snapshot(case_id, include_staging=False)
        except Exception:  # noqa: BLE001
            continue
        # Only the endpoint's identity is drawn, and a full node row carries
        # every case and document it belongs to.  A hub account has thousands
        # of edges, so projecting here is the difference between a 45 KB
        # response and a 45 MB one.
        names = {
            key: {
                "provenance_key": n.provenance_key,
                "label": n.label,
                "name": n.name,
            }
            for key, n in snapshot.nodes.items()
        }
        for edge in snapshot.edges:
            if provenance_key not in (edge.source_key, edge.target_key):
                continue
            key = _edge_row(edge)["key"]
            if key in seen_edges:
                continue
            seen_edges.add(key)
            other_key = (
                edge.target_key if edge.source_key == provenance_key else edge.source_key
            )
            neighbours.append(
                {
                    **_edge_row(edge),
                    "direction": "out" if edge.source_key == provenance_key else "in",
                    "other": names.get(other_key),
                }
            )

    doc_ids = row["source_doc_ids"]
    documents: list[dict] = []
    if doc_ids:
        rows = list(
            (
                await session.execute(
                    select(CaseDocument).where(CaseDocument.id.in_(doc_ids))
                )
            ).scalars()
        )
        documents = [
            {
                "id": d.id,
                "filename": d.filename,
                "document_type": d.document_type.value,
                "case_id": d.case_id,
                "ingestion_status": d.ingestion_status.value,
            }
            for d in rows
        ]

    # The provenance rows behind this entity's evidencing documents.
    references: list[dict] = []
    if doc_ids:
        from app.api.v1.sources import _reference_row

        ref_rows = list(
            (
                await session.execute(
                    select(SourceReference)
                    .where(SourceReference.doc_id.in_(doc_ids))
                    .limit(50)
                )
            ).scalars()
        )
        references = [_reference_row(r) for r in ref_rows]

    # Strongest evidence first, so a truncated list is still the useful one.
    neighbours.sort(key=lambda r: -float(r.get("confidence") or 0))

    return {
        "entity": row,
        "relationships": neighbours[:rel_limit],
        "relationship_count": len(neighbours),
        "relationships_truncated": len(neighbours) > rel_limit,
        "documents": documents,
        "references": references,
    }


@router.get("/relationships")
async def relationships(
    rel_type: str | None = Query(None),
    case_id: str | None = Query(None),
    entity: str | None = Query(None, description="Only edges touching this provenance key."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Relationships with their endpoints resolved and evidence attached."""
    allowed = await _visible_case_ids(session, scope)
    if not allowed:
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "types": {}}
    targets = {case_id} & allowed if case_id else allowed

    items: list[dict] = []
    types: dict[str, int] = {}
    seen: set[str] = set()
    store = get_container().graph_store
    for cid in targets:
        try:
            snapshot = store.snapshot(cid, include_staging=False)
        except Exception:  # noqa: BLE001
            continue
        # The list only renders an endpoint's name and type, and a full node
        # row carries every case and document it appears in -- tens of
        # kilobytes per page.  Project the endpoints down to what is drawn.
        names = {
            key: {
                "provenance_key": node.provenance_key,
                "label": node.label,
                "name": node.name,
            }
            for key, node in snapshot.nodes.items()
        }
        for edge in snapshot.edges:
            row = _edge_row(edge)
            if row["key"] in seen:
                continue
            seen.add(row["key"])
            if entity and entity not in (edge.source_key, edge.target_key):
                continue
            types[row["rel_type"]] = types.get(row["rel_type"], 0) + 1
            if rel_type and row["rel_type"] != rel_type:
                continue
            items.append(
                {
                    **row,
                    "case_id": cid,
                    "source_entity": names.get(edge.source_key),
                    "target_entity": names.get(edge.target_key),
                }
            )

    items.sort(key=lambda r: (-float(r.get("confidence") or 0), r["rel_type"]))
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "types": dict(sorted(types.items())),
    }
