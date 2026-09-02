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

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import api_router
from app.config import get_settings
from app.db.session import dispose_engines, init_db
from app.errors import register_exception_handlers
from app.logging import configure_logging, get_logger, new_trace_id, set_trace_id

log = get_logger("crimelink.api")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BACKEND_ROOT.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "dev")
    settings.ensure_directories()
    await init_db()

    from app.container import get_container

    container = get_container()
    # Touch these so a broken configuration fails at boot, not mid-investigation.
    _ = container.object_store
    _ = container.graph_store
    try:
        container.broker.health()
    except Exception as exc:  # noqa: BLE001
        log.warning("startup.broker_unavailable", error=str(exc))

    log.info(
        "crimelink.started",
        version=__version__,
        environment=settings.environment,
        profile=settings.profile,
        graph=settings.effective_graph_backend,
        store=settings.effective_object_store_backend,
        broker=settings.effective_broker_backend,
    )
    yield
    await dispose_engines()
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_and_metrics(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or new_trace_id()
        set_trace_id(trace_id)
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
        return response

    app.include_router(api_router, prefix="/api/v1")

    # Serve the built investigator console when it exists (production image and
    # the single-process demo profile both do this).
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
