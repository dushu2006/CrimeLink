"""Operational endpoints (PRD 13).

``/health/live``  — the process is running.
``/health/ready`` — the process can reach its dependencies.
``/metrics``      — Prometheus exposition of the core operational metrics.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.config import get_settings
from app.container import get_container
from app.logging import get_logger

log = get_logger("crimelink.health")
router = APIRouter(tags=["operations"])


@router.get("/health")
async def health() -> dict:
    return await readiness()


@router.get("/health/live")
async def liveness() -> dict:
    return {"status": "alive", "service": "crimelink-api"}


@router.get("/health/ready")
async def readiness() -> dict:
    settings = get_settings()
    container = get_container()
    checks: dict[str, dict[str, object]] = {}

    def _record(name: str, ok: bool, **extra: object) -> None:
        checks[name] = {"status": "ok" if ok else "error", **extra}

    try:
        from sqlalchemy import text

        from app.db.session import get_async_engine

        async with get_async_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        _record("database", True, backend=settings.effective_relational_backend)
    except Exception as exc:  # noqa: BLE001
        _record("database", False, error=type(exc).__name__)

    try:
        stats = container.graph_store.stats()
        _record("graph", True, backend=stats.get("backend"), nodes=stats.get("nodes"))
    except Exception as exc:  # noqa: BLE001
        _record("graph", False, error=type(exc).__name__)

    try:
        _record("object_store", True, backend=container.object_store.backend_name)
    except Exception as exc:  # noqa: BLE001
        _record("object_store", False, error=type(exc).__name__)

    try:
        health = container.broker.health()
        _record("broker", bool(health.get("alive", True)), **health)
    except Exception as exc:  # noqa: BLE001
        _record("broker", False, error=type(exc).__name__)

    ready = all(
        check.get("status") == "ok" for check in checks.values()
    )
    return {
        "status": "ready" if ready else "degraded",
        "environment": settings.environment,
        "profile": settings.profile,
        "checks": checks,
    }


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition."""
    from app.services.metrics import render_metrics

    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/version")
async def version() -> dict:
    from app import __version__

    settings = get_settings()
    return {
        "service": "crimelink-api",
        "version": __version__,
        "environment": settings.environment,
        "profile": settings.profile,
        "graph_backend": settings.effective_graph_backend,
        "object_store_backend": settings.effective_object_store_backend,
        "broker_backend": settings.effective_broker_backend,
        "relational_backend": settings.effective_relational_backend,
    }
