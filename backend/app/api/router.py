"""API v1 router aggregation.

Every route here is authenticated by default except the explicitly public ones
(login, refresh, setup, health, version).  No route in this surface uses the ``DELETE``
method — not disabled, absent — and a test asserts that.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    access,
    admin,
    ai,
    auth,
    cases,
    database,
    documents,
    explore,
    export,
    graph,
    health,
    jobs,
    objects,
    patterns,
    resolution,
    search,
    sources,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(objects.router)
api_router.include_router(cases.router)
api_router.include_router(documents.router)
api_router.include_router(jobs.router)
api_router.include_router(export.router)
api_router.include_router(explore.router)
api_router.include_router(search.router)
api_router.include_router(sources.router)
api_router.include_router(graph.router)
api_router.include_router(resolution.router)
api_router.include_router(patterns.router)
api_router.include_router(access.router)
api_router.include_router(admin.router)
api_router.include_router(database.router)
api_router.include_router(ai.router)

__all__ = ["api_router"]
