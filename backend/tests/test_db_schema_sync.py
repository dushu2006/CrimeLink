"""Embedded SQLite schema upkeep (regression for the PR #7 500s).

PR #7 added ``case_documents.source_metadata`` to the model.  The embedded
profile migrates its SQLite database with ``Base.metadata.create_all``, which
adds missing *tables* but never adds a *column* to a table that already
exists.  A database created by an earlier build therefore drifted, and every
full-entity ``SELECT ... FROM case_documents`` failed with ``no such column:
case_documents.source_metadata`` — surfacing as a 500 on
``GET /admin/quarantine`` and ``GET /admin/database/documents``.

``sync_sqlite_columns`` must bring such a database up to date without losing
rows, so the exact queries those two endpoints run succeed again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Base, CaseDocument
from app.db.session import sync_sqlite_columns
from app.domain.enums import DocumentType, IngestionStatus, SourceConfidence

# case_documents exactly as the initial schema created it (pre-PR #7): no
# source_metadata column.
PRE_PR7_CASE_DOCUMENTS = """
CREATE TABLE case_documents (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    case_id VARCHAR(36) NOT NULL,
    document_type VARCHAR(20) NOT NULL,
    filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    derived_key TEXT,
    content_hash VARCHAR(64) NOT NULL,
    size_bytes INTEGER NOT NULL,
    mime_type VARCHAR(200) NOT NULL,
    language VARCHAR(16),
    ingestion_status VARCHAR(20) NOT NULL,
    ingestion_stage INTEGER NOT NULL,
    failure_reason TEXT,
    source_confidence VARCHAR(20) NOT NULL,
    quarantined BOOLEAN NOT NULL,
    retry_count INTEGER NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    uploaded_by VARCHAR(36),
    created_at DATETIME NOT NULL
);
"""


def _seed_stale_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(PRE_PR7_CASE_DOCUMENTS)
        con.executemany(
            """
            INSERT INTO case_documents (
                id, case_id, document_type, filename, storage_key, derived_key,
                content_hash, size_bytes, mime_type, language, ingestion_status,
                ingestion_stage, failure_reason, source_confidence, quarantined,
                retry_count, is_deleted, uploaded_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-0000000000c1",
                    "FIR", "first-report.txt", "obj/first-report.txt", None,
                    "a" * 64, 2048, "text/plain", "en", "COMPLETE",
                    6, None, "SYNTHETIC", 0,
                    0, 0, None, "2026-09-01 10:00:00",
                ),
                (
                    "00000000-0000-0000-0000-000000000002",
                    "00000000-0000-0000-0000-0000000000c2",
                    "CDR", "broken-cdr.csv", "obj/broken-cdr.csv", None,
                    "b" * 64, 512, "text/csv", "en", "FAILED",
                    1, "Stage 3 failed", "SYNTHETIC", 1,
                    2, 0, None, "2026-09-01 10:00:01",
                ),
            ],
        )
        con.commit()
    finally:
        con.close()


async def test_stale_sqlite_db_is_repaired_without_losing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "stale.db"
    _seed_stale_db(db_path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            # Same startup sequence as init_db(): create_all first (new
            # tables), then the additive column sync.
            await conn.run_sync(Base.metadata.create_all)
            added = await conn.run_sync(sync_sqlite_columns, Base.metadata)

        assert ("case_documents", "source_metadata") in added

        # Second boot must be a no-op (idempotent schema upkeep).
        async with engine.begin() as conn:
            added_again = await conn.run_sync(sync_sqlite_columns, Base.metadata)
        assert added_again == []

        # The exact query that 500'd (full-entity CaseDocument SELECT) works,
        # and both rows survived with the empty-JSON default backfilled.
        async with AsyncSession(engine) as session:
            quarantine_rows = list(
                (
                    await session.execute(
                        select(CaseDocument)
                        .where(CaseDocument.quarantined.is_(True))
                        .order_by(CaseDocument.created_at.desc())
                    )
                ).scalars()
            )
            assert len(quarantine_rows) == 1
            doc = quarantine_rows[0]
            assert doc.filename == "broken-cdr.csv"
            assert doc.document_type is DocumentType.CDR
            assert doc.ingestion_status is IngestionStatus.FAILED
            assert doc.source_confidence is SourceConfidence.SYNTHETIC
            assert doc.source_metadata == {}

            all_rows = (
                (await session.execute(select(CaseDocument).order_by(CaseDocument.created_at.desc())))
                .scalars()
                .all()
            )
            assert len(all_rows) == 2
            # Serialization mirrors GET /admin/database/documents and
            # document_row(): these .value reads are what raised LookupError
            # paths after the column was restored.
            for row in all_rows:
                assert row.document_type.value
                assert row.ingestion_status.value
                assert row.source_confidence.value
                assert row.source_metadata is not None
    finally:
        await engine.dispose()


async def test_sync_is_safe_on_a_fresh_current_schema_db(tmp_path: Path) -> None:
    """A DB created by the current models must report nothing to add."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            added = await conn.run_sync(sync_sqlite_columns, Base.metadata)
        assert added == []
    finally:
        await engine.dispose()
