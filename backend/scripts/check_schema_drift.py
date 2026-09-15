#!/usr/bin/env python3
"""Schema drift check — does the database still match ``app/db/models.py``?

Run it after a deploy (and in CI) to catch the class of defect that a local
embedded run cannot see: SQLite ignores ``VARCHAR`` lengths, so a column that
is too narrow for an enum value, a missing table, or a ``CHECK`` constraint the
models outgrew only ever fails on PostgreSQL — in front of a user.

    python scripts/check_schema_drift.py          # exit 1 on drift

What is compared, and what is deliberately *not*:

* tables, columns, indexes, unique constraints — compared exactly;
* enum column widths and enum ``CHECK`` values — compared exactly (these are
  what actually rejects a write);
* nullability — reported, because a database that is *stricter* than the
  models rejects a legitimate insert (a dataset-scoped document with no case,
  for instance);
* ``JSON`` vs ``JSONB`` and column comments — **not** compared.  The models
  declare portable ``JSON``; the PostgreSQL deployment stores ``JSONB`` on
  purpose (see ``app/db/base.py``), and comments are documentation.  Both are
  recorded here as informational notes so a reader is never surprised by what
  ``alembic check`` still lists.

``alembic check`` on a database this script calls clean still reports a fixed,
understood set of differences — do not "fix" them model-side:

* eight ``server_default`` removals (``cases.classification``,
  ``case_documents.classification``, ``dataset_pseudonyms.entity_type``,
  ``investigation_sessions.scope``/``title``, ``quarantined_records.resolved``/
  ``created_at``, ``users.max_classification``) — the defaults were added by
  the revisions that backfilled those columns, and the models deliberately do
  not declare them, so autogenerate wants to *drop* working database defaults;
* three foreign keys the database enforces and the models do not declare
  (``investigation_findings.reviewed_by``, ``investigation_reports.generated_by``
  and ``approved_by``);
* the ``JSONB``/comment notes above, plus two ``audit_*_id_seq`` sequence
  notices that are PostgreSQL bookkeeping.

The deploy gate is ``python -m app.db.upgrade`` followed by this script: both
answer the question that actually fails a request ("can the schema hold what
the code writes?"), while ``alembic check`` also compares documentation and
database-side hardening that are intentional.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import Enum as SAEnum  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import models as _models  # noqa: F401,E402 - registers every table
from app.db.base import Base  # noqa: E402
from app.db.session import sync_url  # noqa: E402


def _enum_check_values(inspector, table: str, constraint: str) -> list[str] | None:
    """Allowed values of a named CHECK constraint, or ``None`` when absent."""
    for check in inspector.get_check_constraints(table):
        if check.get("name") != constraint:
            continue
        text = str(check.get("sqltext") or "")
        values = re.findall(r"'([^']*)'", text)
        if values:
            return values
    return None


def check(url: str | None = None) -> tuple[list[str], list[str]]:
    """Return ``(failures, notes)`` for the database at *url*."""
    settings = get_settings()
    engine = create_engine(url or sync_url(settings))
    inspector = inspect(engine)
    metadata = Base.metadata

    failures: list[str] = []
    notes: list[str] = []

    db_tables = set(inspector.get_table_names()) - {"alembic_version"}
    model_tables = set(metadata.tables)
    for table in sorted(model_tables - db_tables):
        failures.append(f"missing table: {table}")
    for table in sorted(db_tables - model_tables):
        notes.append(f"extra table (not in the models): {table}")

    for table in sorted(model_tables & db_tables):
        model = metadata.tables[table]
        db_columns = {c["name"]: c for c in inspector.get_columns(table)}

        for column in model.columns:
            db_column = db_columns.get(column.name)
            if db_column is None:
                failures.append(f"missing column: {table}.{column.name}")
                continue
            if db_column["nullable"] != column.nullable:
                # NULL-able in the database but NOT NULL in the model list is
                # only a note; the reverse rejects legitimate writes.
                severity = failures if (column.nullable and not db_column["nullable"]) else notes
                severity.append(
                    f"nullability differs: {table}.{column.name} "
                    f"model={'NULL' if column.nullable else 'NOT NULL'} "
                    f"database={'NULL' if db_column['nullable'] else 'NOT NULL'}"
                )
            if isinstance(column.type, SAEnum):
                db_length = int(getattr(db_column["type"], "length", 0) or 0)
                model_length = int(getattr(column.type, "length", 0) or 0)
                if db_length and model_length and db_length < model_length:
                    # SQLite stores the declared width but never enforces it,
                    # so a narrower column there cannot reject a valid value.
                    severity = failures if engine.dialect.name != "sqlite" else notes
                    severity.append(
                        f"column narrower than the models: {table}.{column.name} "
                        f"VARCHAR({db_length}) < VARCHAR({model_length})"
                    )
                if str(db_column["type"]).upper().startswith("JSON"):
                    notes.append(f"JSON column stored as JSONB: {table}.{column.name}")

        db_indexes = {i["name"] for i in inspector.get_indexes(table)}
        for index in sorted({i.name for i in model.indexes} - db_indexes):
            failures.append(f"missing index: {table}.{index}")

        db_uniques = {c["name"] for c in inspector.get_unique_constraints(table)}
        model_uniques = {
            c.name for c in model.constraints if c.__class__.__name__ == "UniqueConstraint"
        }
        for unique in sorted(model_uniques - db_uniques):
            failures.append(f"missing unique constraint: {table}.{unique}")

    bind = engine.connect()
    try:
        for table, model in metadata.tables.items():
            if table not in db_tables:
                continue
            for column in model.columns:
                if not isinstance(column.type, SAEnum):
                    continue
                constraint = f"ck_{table}_{column.type.name}"
                via_sql = None
                if engine.dialect.name == "postgresql":
                    row = bind.exec_driver_sql(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
                        (constraint,),
                    ).fetchone()
                    via_sql = re.findall(r"'([^']*)'", row[0]) if row else None
                values = via_sql
                if values is None:
                    values = _enum_check_values(inspector, table, constraint)
                if values is None:
                    notes.append(f"no named CHECK constraint found for {table}.{column.name}")
                    continue
                expected = [str(v) for v in column.type.enums]
                missing = [v for v in expected if v not in values]
                if missing:
                    failures.append(
                        f"enum CHECK rejects model values: {table}.{column.name} "
                        f"missing={missing}"
                    )
    finally:
        bind.close()

    return failures, notes


def main() -> int:
    failures, notes = check()
    settings = get_settings()
    print(f"schema drift check — {settings.effective_relational_backend} ({sync_url(settings)})")
    for note in notes:
        print(f"  note  {note}")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        print(f"\n{len(failures)} drift finding(s); run `python -m app.db.upgrade`.")
        return 1
    print("  OK    database matches the models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
