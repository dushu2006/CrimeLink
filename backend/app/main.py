"""CrimeLink API application entry point.

Responsibilities of this module, and nothing else:

* configure structured logging;
* mint a ``trace_id`` for every request and return it in responses, so a user
  complaint ("this search hung") maps to one ID that spans API → broker → every
  pipeline stage (PRD 13);
* install the uniform error contract;
* initialise persistence and wire the adapters;
* serve the built investigator console when one is present.

All business behaviour lives behind the API routers and services.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app import runtime
from app.api.router import api_router
from app.config import get_settings
from app.db.session import dispose_engines, init_db
from app.errors import register_exception_handlers
from app.logging import configure_logging, get_logger, new_trace_id, set_trace_id

log = get_logger("crimelink.api")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BACKEND_ROOT.parent / "frontend" / "dist"


def _ensure_writable_directories(settings) -> None:
    """Create the workspace directories, surviving a read-only bundle root.

    On serverless platforms only ``/tmp`` is writable; a deployment whose
    ``CRIMELINK_DATA_DIR`` still points inside the (read-only) function bundle
    would otherwise die during startup — which on Vercel poisons the whole
    instance (``FUNCTION_INVOCATION_FAILED`` for every later request). When
    directory creation fails on a serverless runtime, the transient workspace
    roots are re-pointed under ``/tmp`` with a loud warning and startup
    continues. Everywhere else the failure stays fatal, exactly as before.
    """
    try:
        settings.ensure_directories()
        return
    except OSError as exc:
        if not runtime.running_on_serverless():
            raise
        log.warning(
            "startup.data_dir_unwritable",
            error=str(exc),
            data_dir=str(settings.data_dir),
            detail="re-pointing transient workspace directories under /tmp",
        )
        fallback = Path("/tmp") / "crimelink-data"
        object.__setattr__(settings, "data_dir", fallback)
        object.__setattr__(settings, "object_store_dir", Path("/tmp") / "crimelink-objects")
        object.__setattr__(
            settings, "graph_snapshot_path", fallback / "graph.json"
        )
        settings.ensure_directories()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "dev")
    _ensure_writable_directories(settings)

    # Serverless runtimes (Vercel) only define their request handler after a
    # successful lifespan: a startup crash does not fail one boot, it turns
    # EVERY later invocation on that instance into 500
    # FUNCTION_INVOCATION_FAILED. A transient database/proxy blip during a cold
    # start must therefore degrade the instance, not kill it — requests then
    # answer with the normal error contract (and the next cold start retries
    # the full initialization). Outside serverless the historical fail-fast
    # behaviour is kept verbatim: a broken configuration should crash a
    # container at boot, not mid-investigation.
    tolerant = runtime.running_on_serverless()
    degraded: list[str] = []

    def _guard(step: str, exc: BaseException) -> None:
        if not tolerant:
            raise exc
        degraded.append(f"{step}: {type(exc).__name__}: {exc}")
        log.error("startup.step_failed_degraded", step=step, error=str(exc))

    # Alembic owns the schema.  The application and the deploy hook run the very
    # same upgrade (`python -m app.db.upgrade`, for example from a deployment
    # pre-deploy hook), so a container can never boot against a schema its code
    # does not expect —
    # and a pre-Alembic database is adopted on first start.  Once the database is
    # at head this is a version-table read; it runs off the event loop because
    # Alembic is synchronous.
    try:
        from app.db.upgrade import upgrade_database

        await asyncio.to_thread(upgrade_database, settings)
    except Exception as exc:  # noqa: BLE001
        _guard("schema_upgrade", exc)

    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        _guard("init_db", exc)

    try:
        from app.container import get_container

        container = get_container()
        # Touch these so a broken configuration is visible at boot. On a
        # serverless runtime a broken adapter degrades only the features that
        # need it (the documented failure table: "Neo4j unavailable — case
        # metadata still available"), instead of taking the whole API down.
        try:
            _ = container.object_store
        except Exception as exc:  # noqa: BLE001
            _guard("object_store", exc)
        try:
            _ = container.graph_store
        except Exception as exc:  # noqa: BLE001
            _guard("graph_store", exc)
        try:
            container.broker.health()
        except Exception as exc:  # noqa: BLE001
            log.warning("startup.broker_unavailable", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _guard("container", exc)

    # Metadata-only self-repair: guarantee the documented contract that the
    # three demo roles can sign in and that existing investigative data is
    # resolvable through an active dataset. Never fatal, never touches data
    # rows, idempotent — a healthy database pays two small queries.
    try:
        from app.datasets.repair import ensure_demo_users, repair_active_dataset
        from app.db.session import async_session

        async with async_session() as session:
            repair = await repair_active_dataset(session)
            created = await ensure_demo_users(session)
        if repair.get("status") != "ok" or created:
            log.warning(
                "startup.metadata_repair",
                repair=repair,
                users_created=created,
            )
    except Exception as exc:  # noqa: BLE001
        _guard("metadata_repair", exc)

    log.info(
        "crimelink.started",
        version=__version__,
        environment=settings.environment,
        profile=settings.profile,
        graph=settings.effective_graph_backend,
        store=settings.effective_object_store_backend,
        broker=settings.effective_broker_backend,
        serverless=tolerant,
        degraded=degraded or None,
    )
    yield
    try:
        from app.container import get_container as _get_container

        graph_store = _get_container().graph_store
        if hasattr(graph_store, "close"):
            graph_store.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("shutdown.graph_close_failed", error=str(exc))
    try:
        await dispose_engines()
        from app.api.v1.jobs import dispose_auth_engine

        await dispose_auth_engine()
    except Exception as exc:  # noqa: BLE001
        log.warning("shutdown.dispose_failed", error=str(exc))
    log.info("crimelink.stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CrimeLink API",
        version=__version__,
        description=(
            "AI-assisted criminal network analysis for Indian law enforcement. "
            "Every node and relationship carries a pointer to the document and text "
            "location that produced it; every identity merge and every pattern finding "
            "waits for a human decision; every action is written to a tamper-evident, "
            "hash-chained audit trail."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    register_exception_handlers(app)

    if settings.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Dataset-Id", "X-Trace-Id"],
    )

    @app.middleware("http")
    async def trace_and_metrics(request: Request, call_next):
        content_length = request.headers.get("content-length")
        try:
            oversized = bool(content_length and int(content_length) > settings.max_request_bytes)
        except ValueError:
            oversized = True
        if oversized:
            return Response(status_code=413, content="Request body too large")
        trace_id = request.headers.get("x-trace-id") or new_trace_id()
        set_trace_id(trace_id)
        # Also expose it on the request so a handler can quote it back in a
        # response body.  A user reporting "the AI failed" can then give us the
        # id that is already on the log line.
        request.state.trace_id = trace_id
        started = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
        finally:
            duration = time.perf_counter() - started
            from app.services.metrics import API_LATENCY, API_REQUESTS

            path = request.url.path
            if response is not None:
                API_LATENCY.labels(method=request.method, path=path).observe(duration)
                API_REQUESTS.labels(
                    method=request.method, path=path, status=response.status_code
                ).inc()
        if response is not None:
            response.headers["X-Trace-Id"] = trace_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
            if settings.environment == "production":
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.include_router(api_router, prefix="/api/v1")

    # Serve the built investigator console when it exists (production image and
    # the single-process embedded profile both do this).
    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            candidate = FRONTEND_DIST / full_path
            if full_path.startswith("api/") or full_path.startswith("assets/"):
                return Response(status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
