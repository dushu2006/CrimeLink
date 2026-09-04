"""Persistence for corpus rows that could not be attached to a case.

The corpus adapter builds per-case documents.  A row it cannot route to a case
never becomes a document at all, so the existing ``CaseDocument.quarantined``
flag -- which marks a document that failed *processing* -- cannot represent it.
These rows are kept in ``quarantined_records`` instead, with the coordinates
needed to reopen the original record.

Storing them is what turns "3,244 rows were dropped" from a number in a log
into something an investigator can inspect and challenge.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_uuid
from app.db.models import QuarantinedRecord

#: Written to every row of a run so a quarantine list can be filtered to the
#: import that produced it.
__all__ = ["persist_quarantined_records", "quarantine_summary"]


async def persist_quarantined_records(
    session: AsyncSession,
    rows: Iterable[dict[str, Any]],
    *,
    dataset_version: str | None = None,
    import_run_id: str | None = None,
) -> int:
    """Upsert quarantined rows, returning how many were newly recorded.

    Re-running an import must converge on the same set rather than accumulate a
    copy per run, so an existing row at the same position is updated in place.
    The uniqueness key mirrors ``uq_quarantined_records_position``.
    """
    rows = list(rows)
    if not rows:
        return 0

    # One query for the whole batch: 3,244 individual existence checks would
    # dominate the import.
    existing_keys = {
        (r.origin_file, r.row_number, r.record_id): r
        for r in (
            await session.execute(
                select(QuarantinedRecord).where(
                    QuarantinedRecord.origin_file.in_(
                        {str(row.get("origin_file") or "") for row in rows}
                    )
                )
            )
        ).scalars()
    }

    created = 0
    for row in rows:
        origin_file = str(row.get("origin_file") or "")
        row_number = row.get("row_number")
        record_id = row.get("record_id")
        key = (origin_file, row_number, record_id)

        current = existing_keys.get(key)
        if current is not None:
            # Keep the explanation current if the adapter's reasoning improves,
            # but never resurrect a row an operator has already resolved.
            current.reason_code = str(row.get("reason_code") or current.reason_code)
            current.reason = str(row.get("reason") or current.reason)
            current.field_values = dict(row.get("field_values") or {})
            if import_run_id:
                current.import_run_id = import_run_id
            continue

        session.add(
            QuarantinedRecord(
                id=new_uuid(),
                origin_file=origin_file,
                row_number=row_number,
                record_id=record_id,
                source_type=str(row.get("source_type") or "unknown"),
                reason_code=str(row.get("reason_code") or "unknown"),
                reason=str(row.get("reason") or ""),
                unresolved_case_id=row.get("unresolved_case_id"),
                field_values=dict(row.get("field_values") or {}),
                dataset_version=dataset_version,
                import_run_id=import_run_id,
                resolved=False,
            )
        )
        created += 1

    await session.flush()
    return created


async def quarantine_summary(session: AsyncSession) -> dict[str, Any]:
    """Counts by source type and reason, for the data-quality view."""
    total = (
        await session.execute(
            select(func.count(QuarantinedRecord.id)).where(
                QuarantinedRecord.resolved.is_(False)
            )
        )
    ).scalar_one()

    by_reason: dict[str, int] = {}
    by_source: dict[str, int] = {}
    rows = (
        await session.execute(
            select(
                QuarantinedRecord.source_type,
                QuarantinedRecord.reason_code,
                func.count(QuarantinedRecord.id),
            )
            .where(QuarantinedRecord.resolved.is_(False))
            .group_by(QuarantinedRecord.source_type, QuarantinedRecord.reason_code)
        )
    ).all()
    for source_type, reason_code, count in rows:
        by_reason[reason_code] = by_reason.get(reason_code, 0) + int(count)
        by_source[source_type] = by_source.get(source_type, 0) + int(count)

    return {
        "total": int(total),
        "by_reason": dict(sorted(by_reason.items())),
        "by_source_type": dict(sorted(by_source.items())),
    }
