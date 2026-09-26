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
from app.datasets import registry
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
    # Scoped to the active dataset like every other view. An inspector that
    # shows rows no page can reach is how "the old data is still there"
    # reports start; the dataset each row belongs to is returned so the scope
    # is visible rather than implied.
    visible = await registry.visibility_filter(session, Case)
    rows = (
        (
            await session.execute(
                select(Case)
                .where(visible)
                .order_by(Case.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars().all()
    )
    total = (
        await session.execute(select(func.count(Case.id)).where(visible))
    ).scalar() or 0
    return {
        "items": [
            {
                "id": c.id,
                "case_number": c.case_number,
                "title": c.title,
                "jurisdiction_id": c.jurisdiction_id,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                "dataset_id": c.dataset_id,
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
    visible = await registry.visibility_filter(session, DCD)
    rows = (
        (
            await session.execute(
                select(DCD)
                .where(visible)
                .order_by(DCD.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars().all()
    )
    total = (
        await session.execute(select(func.count(DCD.id)).where(visible))
    ).scalar() or 0
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


def _redact_url(url: str) -> str:
    """Mask the credential component of a connection URL (host/path kept)."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - malformed URL
        return "<unparseable url>"
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
        return urlunsplit(parts._replace(netloc=netloc))
    return url


def _classify_error(exc: BaseException, target: str) -> str:
    """Turn an infrastructure failure into a sanitized, actionable message.

    The three classes an operator acts on differently:

    * ``config``       — the service answered but the named resource (database,
      bucket) does not exist / is not configured;
    * ``auth``         — the service answered but rejected the credentials;
    * ``connectivity`` — the service could not be reached at all (DNS, refused,
      timeout) — the classic failure when a Compose hostname leaks into a
      serverless environment.

    Secrets never appear: *target* must already be credential-free.
    """
    import socket

    message = str(exc)
    lowered = message.lower()
    if "22N51" in message:
        return (
            f"config: {target} reported Neo4j 22N51 — the configured database does not "
            "exist on the server; set NEO4J_DATABASE to the database that does"
        )
    if isinstance(exc, socket.gaierror) or "getaddrinfo" in lowered or "name or service not known" in lowered or "cannot resolve" in lowered or "nodename nor servname" in lowered:
        return f"connectivity: {target} — the configured host does not resolve from this runtime (Compose hostnames do not exist on serverless platforms)"
    if isinstance(exc, ConnectionRefusedError) or "refused" in lowered:
        return f"connectivity: {target} — connection refused"
    if "timed out" in lowered or "timeout" in lowered or isinstance(exc, TimeoutError):
        return f"connectivity: {target} — timed out"
    if "password authentication" in lowered or "authentication failed" in lowered or "autherror" in lowered.replace(" ", "") or "access denied" in lowered or "invalidaccesskeyid" in lowered.replace(" ", "") or "signatuedoesnotmatch" in lowered.replace(" ", "") or "unauthorized" in lowered:
        return f"auth: {target} — credentials were rejected; check the configured user/password or key/secret"
    detail = message.strip().splitlines()[0] if message.strip() else type(exc).__name__
    return f"{target}: {detail[:200]}"


@router.get("/health")
async def health(
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Check status of all infrastructure components without exposing secrets.

    Every dependency is *actually contacted* (no component reports ``ok``
    without a successful round-trip), each probe runs with a bounded timeout
    so a black-holed endpoint can never hang the admin console, and failures
    are classified as **config** / **auth** / **connectivity** with a sanitized
    message that tells the operator what to change.  URLs in the response are
    credential-free.
    """
    import asyncio

    from app.container import get_container
    from app.errors import ServiceUnavailableError

    settings = get_settings()
    container = get_container()

    # ---------------------------------------------------------------- postgres
    async def _postgres_probe() -> str:
        from app.db.session import get_async_engine
        from sqlalchemy import text as _text

        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(_text("SELECT 1"))
        return "SELECT 1 round-trip ok"

    postgres = {"ok": True, "backend": settings.effective_relational_backend}
    try:
        detail = await asyncio.wait_for(_postgres_probe(), timeout=3.0)
        postgres["detail"], postgres["error"] = detail, None
    except asyncio.TimeoutError:
        postgres["ok"] = False
        postgres["detail"] = "SELECT 1"
        postgres["error"] = f"connectivity: postgres — health probe timed out after 3s"
    except Exception as exc:
        postgres["ok"] = False
        postgres["detail"] = "SELECT 1"
        postgres["error"] = _classify_error(exc, "postgres")

    # -------------------------------------------------------------------- graph
    graph = {"ok": True, "backend": settings.effective_graph_backend}
    try:
        stats = await asyncio.wait_for(
            asyncio.to_thread(container.graph_store.stats), timeout=10.0
        )
        graph["detail"] = f"{stats.get('nodes', 0)} nodes / {stats.get('edges', 0)} edges"
        graph["error"] = None
    except asyncio.TimeoutError:
        graph["ok"] = False
        graph["detail"] = "node/edge count"
        graph["error"] = "connectivity: graph — health probe timed out after 10s"
    except ServiceUnavailableError as exc:
        graph["ok"] = False
        graph["detail"] = "node/edge count"
        graph["error"] = str(exc)
    except Exception as exc:
        graph["ok"] = False
        graph["detail"] = "node/edge count"
        graph["error"] = _classify_error(exc, f"graph ({settings.neo4j_database} @ {_redact_url(settings.neo4j_uri)})")

    # --------------------------------------------------------------------- redis
    redis = {"ok": True, "backend": settings.effective_broker_backend}
    if settings.effective_broker_backend == "inline":
        # Honest, not a fake OK: with the inline broker Redis is *not part of
        # this deployment* (jobs run in-process, rate limiting is in-process
        # with a documented fail-open fallback), so there is nothing to check.
        redis["detail"] = "not used — inline job broker, in-process rate limiting"
        redis["error"] = None
    else:
        def _redis_probe() -> str:
            import redis as redis_lib  # type: ignore

            client = redis_lib.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            try:
                client.ping()
            finally:
                client.close()
            return f"PING ok @ {_redact_url(settings.redis_url)}"

        try:
            detail = await asyncio.wait_for(asyncio.to_thread(_redis_probe), timeout=4.0)
            redis["detail"], redis["error"] = detail, None
        except asyncio.TimeoutError:
            redis["ok"] = False
            redis["detail"] = _redact_url(settings.redis_url)
            redis["error"] = "connectivity: redis — health probe timed out after 4s"
        except Exception as exc:
            redis["ok"] = False
            redis["detail"] = _redact_url(settings.redis_url)
            redis["error"] = _classify_error(exc, "redis")

    # -------------------------------------------------------------- object store
    object_store = {"ok": True, "backend": settings.effective_object_store_backend}
    try:
        def _object_probe():
            # The property initialises the store (MinIO ensure_buckets may dial
            # the endpoint), so it runs in the same worker thread as the probe.
            store = container.object_store
            return store.health_check()

        ok, detail, error = await asyncio.wait_for(
            asyncio.to_thread(_object_probe), timeout=20.0
        )
        object_store["ok"] = ok
        object_store["detail"] = detail
        object_store["error"] = error
    except asyncio.TimeoutError:
        object_store["ok"] = False
        object_store["detail"] = ""
        object_store["error"] = "connectivity: object store — health probe timed out after 20s"
    except Exception as exc:
        object_store["ok"] = False
        object_store["detail"] = ""
        object_store["error"] = _classify_error(exc, "object store")

    # -------------------------------------------------------------------- broker
    broker = {"ok": True, "backend": settings.effective_broker_backend}
    if settings.effective_broker_backend == "inline":
        broker["detail"] = "in-process executor — jobs run inside the API function (no external workers required)"
        broker["error"] = None
    else:
        try:
            info = await asyncio.wait_for(asyncio.to_thread(container.broker.health), timeout=8.0)
            if info.get("alive"):
                broker["detail"] = f"celery — {info.get('workers', 0)} worker(s) responding"
                broker["error"] = None
            else:
                broker["ok"] = False
                broker["detail"] = "celery"
                broker["error"] = (
                    info.get("error")
                    or "connectivity: no Celery workers responded to the inspection ping — "
                    "deploy workers, or set CRIMELINK_BROKER_BACKEND=inline for a serverless deployment"
                )
        except asyncio.TimeoutError:
            broker["ok"] = False
            broker["detail"] = "celery"
            broker["error"] = "connectivity: broker — health probe timed out after 8s"
        except Exception as exc:
            broker["ok"] = False
            broker["detail"] = "celery"
            broker["error"] = _classify_error(exc, "broker")

    return {
        "postgres": postgres,
        "graph": graph,
        "redis": redis,
        "object_store": object_store,
        "broker": broker,
        "nlp_provider": container.nlp.name,
        "ai_roles": {
            role: settings.ai_role_available(role)
            for role in ("extraction", "reasoning", "explanation", "classification", "embedding")
        },
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
