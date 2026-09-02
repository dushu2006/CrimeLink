"""Alembic environment for CrimeLink.

The URL comes from :mod:`app.config` so the app, the Celery workers and the
migrations can never drift apart.  Two details matter for an investigation
platform:

* the audit chain tables are created here too — a migration that silently
  skipped them would leave the tamper-evident log unable to start;
* every migration runs inside a transaction by default, which PostgreSQL
  honours with true DDL rollback.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models as _models  # noqa: F401 - registers every table

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """Resolve the sync database URL (Alembic is a synchronous tool)."""
    override = os.environ.get("CRIMELINK_ALEMBIC_URL")
    if override:
        return override
    settings = get_settings()
    if settings.effective_relational_backend == "postgres":
        return settings.postgres_dsn_sync
    return settings.sqlite_url_sync


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,  # SQLite cannot ALTER in place
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
