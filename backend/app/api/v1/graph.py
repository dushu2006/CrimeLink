"""Graph endpoints (PRD 10 / 11).

``GET /graph/nodes/{pk}/expand``
    Depth is **hard-capped at 2** for interactivity; returns Cytoscape-format
    JSON and is logged as ``GRAPH_EXPAND``.

``GET /graph/nodes/{pk}/influence``
    Returns the score *and* the explanation subgraph.  A score without an
    explanation is a test failure, not a partial answer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.domain.enums import EntityType
from app.errors import ValidationFailedError
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    audited,
    get_audit_recorder,
    get_principal,
    get_scope,
)
from app.services.graph_service import GraphService

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/nodes/{provenance_key}/expand")
@audited(
    "GRAPH_EXPAND",
    target=lambda result, **kw: kw.get("provenance_key"),
    details=lambda result, **kw: {
        "depth": kw.get("depth"),
        "rel_types": kw.get("rel_types"),
        "nodes_returned": len(result.get("nodes", [])),
    },
)
async def expand(
    provenance_key: str,
    rel_types: str | None = Query(
        None, description="Comma-separated relationship types to follow"
    ),
    depth: int = Query(1, ge=1, le=2, description="Hard-capped at 2 hops"),
    limit: int = Query(300, ge=1, le=1000),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    types = [t.strip().upper() for t in (rel_types or "").split(",") if t.strip()]
    return await GraphService().expand(
        session, scope, provenance_key, rel_types=types or None, depth=depth, limit=limit
    )


@router.get("/nodes/{provenance_key}/influence")
async def influence(
    provenance_key: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    payload = await GraphService().influence(session, scope, provenance_key)
    recorder.record(
        "GRAPH_EXPAND",
        target_resource=provenance_key,
        details={"kind": "influence", "rank": payload.get("rank_in_case")},
    )
    await recorder.flush()
    return payload


@router.get("/nodes/{provenance_key}")
async def node(
    provenance_key: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    from app.services.graph_service import _node_row

    service = GraphService()
    resolved = await service._assert_node_in_scope(session, scope, provenance_key)
    return _node_row(resolved)


@router.get("/cases/{case_id}")
async def case_graph(
    case_id: str,
    include_staging: bool = Query(
        False, description="Include unverified (anonymous-tip) nodes and links"
    ),
    limit: int = Query(2000, ge=1, le=10_000),
    labels: str | None = Query(
        None, description="Comma-separated entity types (PERSON, PHONE, …) to keep"
    ),
    rel_types: str | None = Query(
        None, description="Comma-separated relationship types (CALLED, …) to keep"
    ),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """The whole case graph for the canvas — every node carries its evidence.

    This is the **Master Graph**: the complete network of the case.  The
    optional ``labels`` / ``rel_types`` filters restrict the view (the same
    canonical data, just filtered) — they never change what is persisted.
    """
    label_list = [l.strip().upper() for l in (labels or "").split(",") if l.strip()]
    rel_list = [r.strip().upper() for r in (rel_types or "").split(",") if r.strip()]
    payload = await GraphService().case_graph(
        session,
        scope,
        case_id,
        include_staging=include_staging,
        limit=limit,
        labels=label_list or None,
        rel_types=rel_list or None,
    )
    recorder.record(
        "GRAPH_EXPAND",
        target_resource=case_id,
        details={
            "kind": "master_graph",
            "nodes": len(payload["nodes"]),
            "edges": len(payload["edges"]),
        },
    )
    await recorder.flush()
    return payload


@router.get("/cases/{case_id}/temporal")
async def temporal_graph(
    case_id: str,
    target: str | None = Query(
        None, description="Optional person provenance key to focus the window on"
    ),
    from_ts: str | None = Query(None, description="ISO-8601 start (inclusive)"),
    to_ts: str | None = Query(None, description="ISO-8601 end (inclusive)"),
    depth: int = Query(3, ge=1, le=4, description="BFS depth from the target"),
    limit: int = Query(400, ge=1, le=2000),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """The time-constrained **visual** graph (Temporal Graph).

    Returns graph-ready ``nodes`` / ``edges`` (plus the window, dated events
    and evidence), NOT a serialised path.  A window that matches nothing comes
    back with an explicit ``empty_reason``.
    """
    payload = await GraphService().temporal_graph(
        session,
        scope,
        case_id,
        target=target,
        from_ts=from_ts,
        to_ts=to_ts,
        depth=depth,
        limit=limit,
    )
    recorder.record(
        "GRAPH_EXPAND",
        target_resource=case_id,
        details={
            "kind": "temporal_graph",
            "target": target,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "nodes": len(payload["nodes"]),
            "edges": len(payload["edges"]),
            "empty_reason": payload.get("empty_reason"),
        },
    )
    await recorder.flush()
    return payload


@router.get("/cases/{case_id}/centrality")
async def centrality(
    case_id: str,
    metric: str = Query(
        "betweenness", pattern="^(betweenness|pagerank|degree|eigenvector)$"
    ),
    limit: int = Query(25, ge=1, le=200),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Ranked influence scores (PRD 11.1) with the weights that produced them."""
    rows = await GraphService().ranked_influencers(
        session, scope, case_id, limit=limit, metric=metric
    )
    return {"case_id": case_id, "metric": metric, "items": rows, "count": len(rows)}


@router.get("/cases/{case_id}/influencers")
async def influencers(
    case_id: str,
    limit: int = Query(10, ge=1, le=100),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    rows = await GraphService().ranked_influencers(session, scope, case_id, limit=limit)
    return {"case_id": case_id, "items": rows, "count": len(rows)}


class PathRequest(BaseModel):
    source_key: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    max_depth: int | None = Field(default=None, ge=1, le=4)
    slack_seconds: int = Field(default=0, ge=0, le=86_400)


@router.post("/cases/{case_id}/paths")
async def temporal_paths(
    case_id: str,
    payload: PathRequest,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Chronologically coherent connections between two entities (PRD 11.4)."""
    paths = await GraphService().temporal_paths(
        session,
        scope,
        case_id,
        payload.source_key,
        payload.target_key,
        max_depth=payload.max_depth,
    )
    return {"case_id": case_id, "paths": paths, "count": len(paths)}


@router.get("/cases/{case_id}/staging")
async def staging_nodes(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Anonymous-tip entities waiting for an investigator to promote them."""
    rows = await GraphService().staging_nodes(session, scope, case_id)
    return {"case_id": case_id, "items": rows, "count": len(rows)}


class PromoteRequest(BaseModel):
    provenance_keys: list[str] = Field(min_length=1, max_length=200)


@router.post("/cases/{case_id}/staging/promote")
@audited(
    "CONFIG_CHANGE",
    target=lambda result, **kw: f"case:{kw.get('case_id')}:staging",
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {"promoted": result.get("promoted")},
)
async def promote_staging(
    case_id: str,
    payload: PromoteRequest,
    principal: Principal = Depends(get_principal),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    principal.require("INVESTIGATOR", "ADMIN")
    return await GraphService().promote_staging(
        session, scope, case_id, payload.provenance_keys
    )


@router.get("/stats")
async def stats(
    principal: Principal = Depends(get_principal),
) -> dict:
    return GraphService().stats()


@router.get("/entity-types")
async def entity_types(principal: Principal = Depends(get_principal)) -> dict:
    return {"items": [t.value for t in EntityType]}


def _validate_type(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return EntityType(value).value
    except ValueError as exc:
        raise ValidationFailedError(f"Unknown entity type '{value}'.") from exc
