"""The single-active-dataset invariant survives activation, deterministically.

`datasets` carries a *partial unique index* on `is_active`, so the database —
not the application — guarantees that exactly one dataset can be active.
Switching the active dataset therefore has to be written in an order the index
accepts: the outgoing flag must land before the incoming one.

When both rows are dirty in the same unit of work, SQLAlchemy batches them into
a single `executemany` whose row order is undefined.  Roughly half the time the
`is_active=1` row was applied first and the statement died with
`UNIQUE constraint failed: datasets.is_active`.  These tests pin the ordering
(and the invariant) so the flake cannot come back.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.datasets import registry
from app.db.models import Dataset


async def _make_dataset(db, name: str) -> Dataset:
    """A real dataset row, created the same way ingestion creates one."""
    return await registry.create_dataset(db, name=name, source_kind="folder")


@pytest.mark.asyncio
async def test_activation_never_trips_the_unique_index(db) -> None:
    first = await _make_dataset(db, "Activation Order A")
    await registry.set_only_active(db, first)

    second = await _make_dataset(db, "Activation Order B")
    # Both rows are now dirty in the same unit of work — the exact shape that
    # used to produce a non-deterministic executemany.
    await registry.set_only_active(db, second)

    active = (
        await db.execute(select(Dataset).where(Dataset.is_active.is_(True)))
    ).scalars().all()
    assert [d.id for d in active] == [second.id]


@pytest.mark.asyncio
async def test_repeated_switching_keeps_exactly_one_active(db) -> None:
    datasets = [await _make_dataset(db, f"Switch {i}") for i in range(3)]
    for index, dataset in enumerate(datasets):
        await registry.set_only_active(db, dataset)
        count = await db.scalar(
            select(func.count()).select_from(Dataset).where(Dataset.is_active.is_(True))
        )
        assert count == 1, f"pass {index}: {count} active datasets"
        assert dataset.is_active is True

    # Every earlier dataset was cleared, not merely shadowed.
    stale = (
        await db.execute(
            select(Dataset.id).where(
                Dataset.is_active.is_(True),
                Dataset.id != datasets[-1].id,
            )
        )
    ).scalars().all()
    assert stale == []


@pytest.mark.asyncio
async def test_activation_of_the_only_dataset_is_idempotent(db) -> None:
    dataset = await _make_dataset(db, "Solo Dataset")
    await registry.set_only_active(db, dataset)
    first_at = dataset.activated_at

    await registry.set_only_active(db, dataset)

    assert dataset.is_active is True
    assert dataset.activated_at >= first_at
    count = await db.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.is_active.is_(True))
    )
    assert count == 1
