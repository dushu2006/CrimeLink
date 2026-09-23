"""Vercel Services entrypoint for the FastAPI backend.

Vercel's current Python/FastAPI runtime resolves a service entrypoint file at
the service root (the official Vercel FastAPI service examples configure
`entrypoint: "main:app"` with `main.py` sitting in the service root). This
module only re-exports the existing ASGI application instance defined in
``app.main``; all application behaviour, routing, and configuration stay in
``app/main.py``. Local and Docker development keep running
`uvicorn app.main:create_app` via ``run.py`` and are unaffected.
"""

from app.main import app

__all__ = ["app"]
