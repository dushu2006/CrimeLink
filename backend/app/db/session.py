"""Engine / session management for both the async API and the sync workers.

The API (FastAPI) uses the async engine; Celery workers use the sync engine.
Both point at the same database and the same models, and both are created from
``Settings`` so switching profiles is purely declarative.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger("crimelink.db")

_async_engine: AsyncEngine | None = None
_async_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_sync_engine: Any | None = None
_sync_sessionmaker: sessionmaker[Session] | None = None
_forced_url: str | None = None


def async_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if _forced_url:
        return _forced_url
    if settings.effective_relational_backend == "postgres":
        return settings.postgres_dsn
    return settings.sqlite_url


def sync_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if _forced_url:
        return _forced_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
    if settings.effective_relational_backend == "postgres":
        return settings.postgres_dsn_sync
    return settings.sqlite_url_sync


def configure_for_tests(url: str) -> None:
    """Point every future engine at *url* (used by the test suite)."""
    global _forced_url, _async_engine, _async_sessionmaker, _sync_engine, _sync_sessionmaker
    _forced_url = url
    _async_engine = None
    _async_sessionmaker = None
    _sync_engine = None
    _sync_sessionmaker = None


def get_async_engine(settings: Settings | None = None) -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        settings = settings or get_settings()
        url = async_url(settings)
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(
                pool_size=settings.postgres_pool_size,
                max_overflow=settings.postgres_max_overflow,
            )
        _async_engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite_pragmas(_async_engine, sync=False)
        log.info("db.async_engine_ready", url=_redact(url))
    return _async_engine


def get_sync_engine(settings: Settings | None = None) -> Any:
    global _sync_engine
    if _sync_engine is None:
        settings = settings or get_settings()
        url = sync_url(settings)
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _sync_engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite_pragmas(_sync_engine, sync=True)
    return _sync_engine


def _configure_sqlite_pragmas(engine: Any, *, sync: bool) -> None:
    """WAL + foreign keys + a busy timeout so concurrent workers behave.

    Both the async and the sync engine expose a ``sync_engine`` whose ``connect``
    event hands us the raw DBAPI connection, so one listener serves both.
    """

    def _apply(dbapi_connection: Any, connection_record: Any = None) -> None:
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            # A full-corpus import holds the single SQLite writer for long
            # stretches while the API keeps serving.  Ten seconds was short
            # enough that logins failed with "database is locked" during an
            # ingest, so wait long enough to outlast a bulk write batch.
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.close()
        except Exception:  # pragma: no cover - pragmas are advisory
            pass

    target = engine if sync else engine.sync_engine
    event.listens_for(target, "connect")(_apply)


def _redact(url: str) -> str:
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        rest = "***@" + rest.split("@", 1)[1]
    return f"{scheme}://{rest}"


def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _async_sessionmaker
    if _async_sessionmaker is None:
        _async_sessionmaker = async_sessionmaker(
            get_async_engine(), expire_on_commit=False, autoflush=False
        )
    return _async_sessionmaker


def get_sync_sessionmaker() -> sessionmaker[Session]:
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        _sync_sessionmaker = sessionmaker(
            get_sync_engine(), expire_on_commit=False, autoflush=False
        )
    return _sync_sessionmaker


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """Async session scope with commit/rollback handling."""
    session = get_async_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with async_session() as session:
        yield session


@contextmanager
def sync_session() -> Iterator[Session]:
    """Sync session scope used by Celery workers and admin scripts."""
    session = get_sync_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def init_db() -> None:
    """Create all tables and apply engine-specific bootstrap."""
    from app.db.models import Base  # local import avoids a cycle at module load

    settings = get_settings()
    settings.ensure_directories()
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if settings.effective_relational_backend == "postgres":
        await _bootstrap_postgres(engine)
    log.info("db.ready", backend=settings.effective_relational_backend, url=_redact(async_url()))


async def _bootstrap_postgres(engine: AsyncEngine) -> None:
    """PostgreSQL-only hardening: pg_trgm and append-only audit table."""
    statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE EXTENSION IF NOT EXISTS btree_gin",
        # Append-only audit: even an application bug cannot rewrite history.
        "REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC",
        "REVOKE UPDATE, DELETE ON audit_logs FROM crimelink",
        "REVOKE UPDATE, DELETE ON audit_chain_head FROM PUBLIC",
        "REVOKE UPDATE, DELETE ON audit_chain_head FROM crimelink",
    ]
    async with engine.begin() as conn:
        for statement in statements:
            try:
                await conn.execute(text(statement))
            except Exception as exc:  # pragma: no cover - grant-dependent
                log.warning("db.bootstrap_statement_skipped", statement=statement, error=str(exc))


async def dispose_engines() -> None:
    global _async_engine, _sync_engine, _async_sessionmaker, _sync_sessionmaker
    if _async_engine is not None:
        await _async_engine.dispose()
    if _sync_engine is not None and hasattr(_sync_engine, "dispose"):
        _sync_engine.dispose()
    _async_engine = None
    _sync_engine = None
    _async_sessionmaker = None
    _sync_sessionmaker = None
