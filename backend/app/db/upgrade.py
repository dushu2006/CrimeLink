"""Schema upgrade entry point — one code path for every deployment shape.

A deployment must never depend on ``Base.metadata.create_all()`` to invent the
schema: ``create_all`` creates missing *tables* but never adds a column to a
table that already exists, so a database seeded by an earlier build silently
drifts (``cases.classification does not exist``) until a request fails.  Alembic
owns the schema instead, and this module is the single entry point both the
deployment hook and the application bootstrap use, so a Render pre-deploy, a
Compose start and ``python run.py`` can never disagree about how the database
gets to ``head``.

Three database shapes are handled, in this order:

``managed``
    The database already has an ``alembic_version`` row: plain
    ``alembic upgrade head``.

``fresh``
    An empty database (a new Render Postgres): ``alembic upgrade head`` builds
    the entire schema.  Since revision ``9c0d1e2f003`` that includes the
    dataset-management and job tables, the ``dataset_id`` columns, the widened
    enum columns and the reconciled ``CHECK`` constraints, so ``head`` is the
    model schema — verified by ``scripts/check_schema_drift.py``.

``legacy``
    Tables exist but there is no ``alembic_version`` (a database created by
    ``create_all`` before Alembic was wired into deployment, e.g. a local
    PostgreSQL or ``var/data/crimelink.db``).  Running ``upgrade head`` here
    would fail on the very first revision (``table "audit_anchors" already
    exists``), so the schema is first brought up to the models additively
    (the same idempotent reconciler the embedded profile has always used), then
    stamped with the revision the reconcile just satisfied
    (``LEGACY_BASELINE_REVISION``) and upgraded — which applies the alignment
    revision and every later one.  Nothing is dropped and no row is rewritten;
    the steps are additive and repeatable.

Usage::

    python -m app.db.upgrade            # deploy hook / bootstrap
    python -m app.db.upgrade --check    # report only, writes nothing
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.db import models as _models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.db.session import (
    get_sync_engine,
    sync_database_columns,
    sync_sqlite_enum_constraints,
    sync_url,
)
from app.logging import get_logger

log = get_logger("crimelink.db.upgrade")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = BACKEND_ROOT / "alembic"

#: Revision whose objects the additive reconciler is guaranteed to satisfy.
#: Stamping it (rather than ``head``) keeps the alignment revision and anything
#: added after it in the upgrade path instead of silently skipping them.
LEGACY_BASELINE_REVISION = "8b9c0d1e002"

MODES = ("managed", "fresh", "legacy")

#: Advisory-lock key shared by every process that migrates a given database.
#: The value is arbitrary, but it must stay stable across releases.
MIGRATION_LOCK_KEY = 4_120_709_311


@contextmanager
def migration_lock(engine: Engine) -> Iterator[None]:
    """Serialize migrations between processes sharing one database.

    ``uvicorn --workers N`` runs the application lifespan in every worker, and a
    Compose stack starts the API, the worker and the seed script against the very
    same database, so two ``alembic upgrade head`` runs can begin at the same
    instant.  On an empty database that race ends with one of them failing on a
    ``CREATE TABLE`` whose name the other process has just created.  The upgrade
    therefore happens under a lock every process agrees on: a Postgres advisory
    lock, or an exclusive ``flock`` on a sibling lock file for SQLite.  Nothing
    else is locked, and a process that dies releases the lock with its
    connection.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        conn = engine.connect()
        try:
            conn.exec_driver_sql("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            try:
                yield
            finally:
                conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
        finally:
            conn.close()
        return

    if dialect == "sqlite":
        database = engine.url.database
        if not database or database == ":memory:":
            yield
            return

        lock_path = Path(f"{database}.migrate.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            try:
                import msvcrt

                with open(lock_path, "a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        try:
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                return
            except (ImportError, OSError):
                yield
                return

        try:
            import fcntl

            with open(lock_path, "a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        except (ImportError, OSError):
            yield
            return

    yield


@dataclass(frozen=True)
class UpgradeReport:
    """What the upgrade did, for logs and for the deploy hook."""

    mode: str
    backend: str
    revision: str
    #: ``(mode, revision)`` are enough to explain a deployment in one line.
    def describe(self) -> str:
        return f"{self.mode} database at revision {self.revision} ({self.backend})"


def alembic_config(settings: Settings | None = None) -> Config:
    """Alembic config pinned to the database the application itself uses.

    ``alembic/env.py`` resolves the URL from ``app.config`` (or from
    ``CRIMELINK_ALEMBIC_URL``); setting ``sqlalchemy.url`` on the in-process
    config makes ``sync_url()`` — which also honours a URL forced at runtime by
    the test suite — authoritative, so migrations and requests can never address
    different databases.
    """
    settings = settings or get_settings()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", sync_url(settings))
    return config


def current_revision(engine: Engine) -> str | None:
    """The database's stored revision, or ``None`` when it predates Alembic."""
    with engine.connect() as conn:
        if not inspect(conn).has_table("alembic_version"):
            return None
        row = conn.exec_driver_sql("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def head_revision(config: Config) -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(config).get_current_head()


def detect_mode(engine: Engine) -> str:
    """Classify the database: ``managed``, ``legacy`` or ``fresh``."""
    with engine.connect() as conn:
        inspector = inspect(conn)
        if inspector.has_table("alembic_version"):
            return "managed"
        tables = set(inspector.get_table_names())
    # ``users`` is in the very first revision, so its presence without an
    # ``alembic_version`` row means the schema was created outside Alembic.
    if tables & {"users", "cases"}:
        return "legacy"
    return "fresh"


def reconcile_legacy_schema(engine: Engine, settings: Settings | None = None) -> list[tuple[str, str]]:
    """Bring a pre-Alembic database up to the models, additively.

    Only missing tables and columns are created; nothing is dropped, narrowed or
    rewritten.  Constraint, index and enum alignment is left to revision
    ``9c0d1e2f003``, which runs immediately afterwards and is guarded, so the
    two steps cannot overlap destructively.
    """
    settings = settings or get_settings()
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        added = sync_database_columns(conn, Base.metadata)
        if settings.effective_relational_backend == "sqlite":
            added += sync_sqlite_enum_constraints(conn, Base.metadata)
        conn.commit()
    return added


def upgrade_database(settings: Settings | None = None) -> UpgradeReport:
    """Bring the relational schema to ``head``; safe to run on every deploy."""
    settings = settings or get_settings()
    engine = get_sync_engine(settings)
    config = alembic_config(settings)
    head = head_revision(config)

    with migration_lock(engine):
        # Classified under the lock on purpose: a worker that waited for another
        # migrator must see the database it has *now*, not the one it saw before
        # the wait.
        mode = detect_mode(engine)
        if mode == "legacy":
            added = reconcile_legacy_schema(engine, settings)
            log.warning(
                "db.legacy_schema_adopted",
                backend=settings.effective_relational_backend,
                columns_added=len(added),
                baseline=LEGACY_BASELINE_REVISION,
                reason="database predates Alembic; additive reconcile then stamp",
            )
            if current_revision(engine) is None:
                command.stamp(config, LEGACY_BASELINE_REVISION)

        command.upgrade(config, "head")
        revision = current_revision(engine) or head

    report = UpgradeReport(mode=mode, backend=settings.effective_relational_backend, revision=revision)
    log.info("db.schema_upgraded", detail=report.describe(), head=head)
    return report


def check_database(settings: Settings | None = None) -> UpgradeReport:
    """Report the database's revision without writing anything."""
    settings = settings or get_settings()
    engine = get_sync_engine(settings)
    config = alembic_config(settings)
    return UpgradeReport(
        mode=detect_mode(engine),
        backend=settings.effective_relational_backend,
        revision=current_revision(engine) or "(none)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.db.upgrade", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report the current revision and database shape; write nothing.",
    )
    args = parser.parse_args(argv)

    report = check_database() if args.check else upgrade_database()
    print(report.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
