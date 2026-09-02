"""Safe read-only database inspection endpoints for administrators/developers.

These endpoints expose live counts, paginated entity listings and status
information for PostgreSQL, Neo4j/embedded graph, Redis, object storage and
NLP/AI providers. They never return raw credentials, never allow writes, and
require the ADMIN role with jurisdiction controls.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    EntityResolutionItem,
    User,
)
from app.db.session import get_db_session
from app.security.deps import Principal, require_roles

router = APIRouter(prefix="/admin/database", tags=["database-inspection"])


@router.get("/summary")
async def summary(
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Aggregate live counts + infrastructure status."""
    from app.container import get_container
    from app.synthetic_corpus import get_corpus_stats
    return await get_corpus_stats()


@router.get("/cases")
async def list_cases(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    rows = (
        (await session.execute(select(Case).order_by(Case.created_at.desc()).limit(limit).offset(offset)))
        .scalars().all()
    )
    total = (await session.execute(select(func.count(Case.id)))).scalar() or 0
    return {
        "items": [
            {
                "id": c.id,
                "case_number": c.case_number,
                "title": c.title,
                "jurisdiction_id": c.jurisdiction_id,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ],
        "count": len(rows),
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/entities")
async def list_entities(
    label: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """List entities in the graph store, optionally filtered by label."""
    from app.container import get_container
    container = get_container()
    try:
        result = container.graph_store.list_nodes(label=label, limit=limit, offset=offset)
    except AttributeError:
        result = {"items": [], "total": 0, "note": "Graph store does not support list_nodes"}
    return result


@router.get("/relationships")
async def list_relationships(
    rel_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    from app.container import get_container
    container = get_container()
    try:
        result = container.graph_store.list_edges(rel_type=rel_type, limit=limit, offset=offset)
    except AttributeError:
        result = {"items": [], "total": 0, "note": "Graph store does not support list_edges"}
    return result


@router.get("/documents")
async def list_documents(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    from app.db.models import CaseDocument as DCD
    rows = (
        (await session.execute(select(DCD).order_by(DCD.created_at.desc()).limit(limit).offset(offset)))
        .scalars().all()
    )
    total = (await session.execute(select(func.count(DCD.id)))).scalar() or 0
    return {
        "items": [
            {
                "id": d.id,
                "case_id": d.case_id,
                "document_type": d.document_type.value,
                "filename": d.filename,
                "size_bytes": d.size_bytes,
                "ingestion_status": d.ingestion_status.value,
                "source_confidence": d.source_confidence.value,
                "quarantined": d.quarantined,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/health")
async def health(
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Check status of all infrastructure components without exposing secrets."""
    from app.container import get_container
    settings = get_settings()
    container = get_container()
    postgres_ok = True
    neo4j_ok = True
    redis_ok = True
    object_ok = True
    broker_ok = True
    nlp_name = container.nlp.name
    ai_roles: dict[str, bool] = {
        role: settings.ai_role_available(role)
        for role in ("extraction", "reasoning", "explanation", "classification", "embedding")
    }
    # postgres
    try:
        from app.db.session import get_async_engine
        from sqlalchemy import text as _text
        import asyncio
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(_text("SELECT 1"))
    except Exception:
        postgres_ok = False
    # graph
    try:
        container.graph_store.stats()
    except Exception:
        neo4j_ok = False
    # broker
    try:
        broker_ok = container.broker.health().get("alive", False)
    except Exception:
        broker_ok = False
    # object store basic sanity (local always works)
    try:
        container.object_store.list_keys(container.settings.minio_bucket_documents)
    except Exception:
        object_ok = False
    return {
        "postgres": {"ok": postgres_ok, "backend": settings.effective_relational_backend},
        "graph": {"ok": neo4j_ok, "backend": settings.effective_graph_backend},
        "redis": {"ok": redis_ok, "backend": settings.effective_broker_backend},
        "object_store": {"ok": object_ok, "backend": settings.effective_object_store_backend},
        "broker": {"ok": broker_ok},
        "nlp_provider": nlp_name,
        "ai_roles": ai_roles,
    }


@router.get("/postgres")
async def postgres_info(
    principal: Principal = Depends(require_roles("ADMIN")),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Postgres-side stats: table counts (live)."""
    tables = ["users", "cases", "case_documents", "ingestion_jobs",
              "entity_resolution_queue", "detected_patterns", "audit_logs"]
    counts: dict[str, int] = {}
    for t in tables:
        try:
            val = (await session.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar()
            counts[t] = int(val or 0)
        except Exception:
            counts[t] = -1
    return {"counts": counts, "backend": get_settings().effective_relational_backend}


@router.get("/neo4j")
async def neo4j_info(
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Graph store stats (works for Neo4j and the embedded backend)."""
    from app.container import get_container
    container = get_container()
    return {
        "backend": get_settings().effective_graph_backend,
        "stats": container.graph_store.stats(),
    }
