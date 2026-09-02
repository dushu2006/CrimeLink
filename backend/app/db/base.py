"""Declarative base and portable column helpers.

Portability note
----------------
The production target is PostgreSQL 15, but the embedded profile (local
development, CI, demos) runs SQLite.  Every column type here is therefore
chosen from the intersection of both dialects:

* UUIDs are stored as ``CHAR(36)`` text with a Python-side default, instead of
  relying on ``gen_random_uuid()``.
* Arrays (``entity_keys``, ``evidence_doc_ids``) are stored as JSON.  On
  PostgreSQL the deployment migration upgrades these to native ``JSONB``;
  the application code is identical either way.
* IP addresses are ``VARCHAR(45)`` rather than ``INET``.
* Timestamps are timezone-naive **UTC** everywhere.  One convention, no
  ambiguity, and it round-trips identically on both engines.

What is *not* portability-compromised is enforcement: every enum becomes a real
``CHECK`` constraint on both engines, and the audit log's append-only guarantee
is installed by ``bootstrap_postgres`` in production.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, MetaData, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Current UTC time as a timezone-naive datetime (single convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid4())


class TextUUID(TypeDecorator):
    """UUID value stored as text so both SQLite and PostgreSQL accept it."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return None if value is None else str(value)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {dict[str, Any]: JSON, dict: JSON, list: JSON}


def pk_column() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_uuid)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(), default=utcnow, nullable=False)
