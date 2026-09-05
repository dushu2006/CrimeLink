"""Engine / session management for both the async API and the sync workers.

The API (FastAPI) uses the async engine; Celery workers use the sync engine.
Both point at the same database and the same models, and both are created from
``Settings`` so switching profiles is purely declarative.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
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


def _async_engine_kwargs(url: str, settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            pool_size=settings.postgres_pool_size,
            max_overflow=settings.postgres_max_overflow,
        )
    return kwargs


def create_dedicated_async_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build an async engine owned exclusively by one event loop."""
    settings = settings or get_settings()
    url = async_url(settings)
    engine = create_async_engine(url, **_async_engine_kwargs(url, settings))
    if url.startswith("sqlite"):
        _configure_sqlite_pragmas(engine, sync=False)
    log.info("db.dedicated_engine_ready", url=_redact(url))
    return engine


def get_async_engine(settings: Settings | None = None) -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        settings = settings or get_settings()
        url = async_url(settings)
        _async_engine = create_async_engine(url, **_async_engine_kwargs(url, settings))
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
        if settings.effective_relational_backend == "sqlite":
            # ``create_all`` creates missing *tables* but never adds a column
            # to a table that already exists.  A database created by an earlier
            # build therefore drifts whenever a model gains a column (PR #7
            # added ``case_documents.source_metadata``), and every full-entity
            # SELECT then fails with "no such column".  Mirror the Alembic
            # migrations the production profile runs so an embedded database
            # is brought up to date without losing its data.
            added = await conn.run_sync(sync_sqlite_columns, Base.metadata)
            for table_name, column_name in added:
                log.warning(
                    "db.sqlite_column_added",
                    table=table_name,
                    column=column_name,
                    reason="model is newer than the existing SQLite schema",
                )
    if settings.effective_relational_backend == "postgres":
        await _bootstrap_postgres(engine)
    log.info("db.ready", backend=settings.effective_relational_backend, url=_redact(async_url()))


# --------------------------------------------------------------------------- #
# Embedded-profile schema upkeep
# --------------------------------------------------------------------------- #

_SQLITE_DIALECT = sqlite_dialect.dialect()


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _scalar_default_literal(column: Any) -> str | None:
    """SQL literal for a static column default, when one exists.

    Only *static* defaults can be expressed in ``ALTER TABLE ... ADD COLUMN``:
    a ``server_default`` of raw SQL text (e.g. ``sa.text("'{}'")``) or a plain
    Python scalar.  Callable defaults (``datetime``/``uuid`` factories, ``dict``)
    cannot be evaluated here; the caller falls back to a nullable add plus a
    Python-side backfill.
    """
    server_default = column.server_default
    if server_default is not None:
        arg = server_default.arg
        if isinstance(arg, bool):
            return "1" if arg else "0"
        if isinstance(arg, (int, float)):
            return repr(arg)
        if isinstance(arg, str):
            # Raw SQL already carries its own quoting (``sa.text("'{}'")``).
            return arg
        return None

    default = column.default
    if default is None or getattr(default, "is_callable", False):
        return None
    arg = default.arg
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return repr(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return None


def _backfill_value(column: Any) -> Any:
    """Python-side default for rows that predate the new column, else None.

    SQLAlchemy wraps plain callables (``dict``, ``datetime`` factories) in a
    context-taking lambda; the original callable is preserved on ``__wrapped__``,
    so evaluating it reproduces exactly what an ORM insert would store.
    """
    default = column.default
    if default is None:
        return None
    if getattr(default, "is_callable", False):
        callable_default = getattr(default.arg, "__wrapped__", default.arg)
        try:
            return callable_default()
        except Exception:  # noqa: BLE001 - a retrofit must never fail on default evaluation
            return None
    return default.arg


def sync_sqlite_columns(connection: Any, metadata: Any) -> list[tuple[str, str]]:
    """Add model columns missing from existing SQLite tables.

    Returns the ``(table, column)`` pairs that were added.  Additions are
    strictly additive and never drop or rewrite data.  New tables created by
    ``create_all`` are skipped because they already carry every column.

    SQLite cannot add a ``NOT NULL`` column to a populated table without a
    static default, so a retrofit column whose default is Python-side is added
    nullable and then backfilled — the same choice the ``source_metadata``
    Alembic migration makes (``nullable=True`` + ``UPDATE ... SET '{}'``).  The
    ORM always supplies a value for new rows, so reads and writes behave
    identically either way.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    added: list[tuple[str, str]] = []

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue

            column_type = column.type.compile(dialect=_SQLITE_DIALECT)
            default_sql = _scalar_default_literal(column)
            # SQLite rejects NOT NULL additions without a static default and a
            # NOT NULL retrofit would also fail on populated tables, so retrofit
            # columns stay nullable at the storage layer (see module docstring).
            ddl = (
                f"ALTER TABLE {_quote_ident(table.name)} ADD COLUMN "
                f"{_quote_ident(column.name)} {column_type}"
            )
            if default_sql is not None:
                ddl += f" DEFAULT {default_sql}"
            connection.execute(text(ddl))

            backfill = _backfill_value(column)
            if backfill is not None:
                if isinstance(backfill, bool):
                    backfill = 1 if backfill else 0
                elif isinstance(backfill, (dict, list)):
                    backfill = json.dumps(backfill)
                if isinstance(backfill, (str, int, float)):
                    connection.execute(
                        text(
                            f"UPDATE {_quote_ident(table.name)} "
                            f"SET {_quote_ident(column.name)} = :value"
                        ),
                        {"value": backfill},
                    )
            added.append((table.name, column.name))
    return added


async def _bootstrap_postgres(engine: AsyncEngine) -> None:
    """Apply optional extensions and audit hardening in independent transactions."""
    extensions = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE EXTENSION IF NOT EXISTS btree_gin",
    ]
    revokes = [
        "REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC",
        "REVOKE UPDATE, DELETE ON audit_logs FROM crimelink",
        "REVOKE UPDATE, DELETE ON audit_chain_head FROM PUBLIC",
        "REVOKE UPDATE, DELETE ON audit_chain_head FROM crimelink",
    ]
    async with engine.connect() as conn:
        for statement in extensions:
            try:
                await conn.execute(text(statement))
                await conn.commit()
                log.info("db.extension_ready", statement=statement)
            except Exception as exc:  # pragma: no cover - grant-dependent
                await conn.rollback()
                log.warning("db.extension_unavailable", statement=statement, error=str(exc))
        for statement in revokes:
            try:
                await conn.execute(text(statement))
                await conn.commit()
                log.info("db.audit_hardening_applied", statement=statement)
            except Exception as exc:  # pragma: no cover - grant-dependent
                await conn.rollback()
                log.error("db.audit_hardening_failed", statement=statement, error=str(exc))


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
