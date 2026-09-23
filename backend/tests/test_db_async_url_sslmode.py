"""Regression: a libpq ``sslmode`` DSN must not break the asyncpg engine.

Managed PostgreSQL providers hand out ``...?sslmode=require`` DSNs.  psycopg2
(Alembic, workers) accepts that natively, but SQLAlchemy forwards URL query
parameters to ``asyncpg.connect()`` as keyword arguments, which has no
``sslmode`` parameter.  The engine object is still created, so the failure only
surfaced on the first async connection during application startup::

    TypeError: connect() got an unexpected keyword argument 'sslmode'

``async_url`` therefore carries ``sslmode`` over to asyncpg's ``ssl`` option
(same libpq mode names) and leaves every other DSN untouched.
"""

from __future__ import annotations

import inspect

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.db.session import _asyncpg_ssl_option, _with_driver


def _asyncpg_connect_kwargs(url: str) -> dict:
    """The keyword arguments SQLAlchemy's asyncpg dialect passes to asyncpg.connect()."""
    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

    _args, kwargs = PGDialect_asyncpg().create_connect_args(make_url(url))
    return kwargs


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        (
            "postgresql+asyncpg://u:pw@db.example.net:5432/crimelink?sslmode=require",
            "postgresql+asyncpg://u:pw@db.example.net:5432/crimelink?ssl=require",
        ),
        # Bare libpq DSN exactly as a provider hands it out, after the driver is added.
        (
            _with_driver("postgresql://u:pw@db.example.net/crimelink?sslmode=verify-full", "asyncpg"),
            "postgresql+asyncpg://u:pw@db.example.net/crimelink?ssl=verify-full",
        ),
        # Other query parameters and the encoded password survive untouched.
        (
            "postgresql+asyncpg://u:p%40ss@host:5432/db?sslmode=require&target_session_attrs=read-write",
            "postgresql+asyncpg://u:p%40ss@host:5432/db?ssl=require&target_session_attrs=read-write",
        ),
        # An explicit asyncpg ``ssl`` option wins; the redundant ``sslmode`` is dropped.
        (
            "postgresql+asyncpg://u:pw@host/db?ssl=verify-ca&sslmode=require",
            "postgresql+asyncpg://u:pw@host/db?ssl=verify-ca",
        ),
    ],
)
def test_sslmode_is_carried_over_to_asyncpg_ssl(dsn: str, expected: str) -> None:
    translated = _asyncpg_ssl_option(dsn)
    assert translated == expected

    kwargs = _asyncpg_connect_kwargs(translated)
    assert "sslmode" not in kwargs
    # Every argument must be one asyncpg.connect() actually accepts.
    inspect.signature(asyncpg.connect).bind(**kwargs)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://crimelink:crimelink@localhost:5432/crimelink",
        "postgresql+asyncpg://crimelink:crimelink@postgres:5432/crimelink",
        "postgresql+asyncpg://u:pw@host:5432/db?ssl=require",
        "sqlite+aiosqlite:////tmp/crimelink/crimelink.db",
    ],
)
def test_urls_without_sslmode_are_returned_unchanged(dsn: str) -> None:
    assert _asyncpg_ssl_option(dsn) == dsn


def test_untranslated_sslmode_is_rejected_by_asyncpg() -> None:
    """Documents why the translation exists: asyncpg has no ``sslmode`` argument."""
    kwargs = _asyncpg_connect_kwargs("postgresql+asyncpg://u:pw@host:5432/db?sslmode=require")
    assert kwargs["sslmode"] == "require"
    with pytest.raises(TypeError, match="sslmode"):
        inspect.signature(asyncpg.connect).bind(**kwargs)
