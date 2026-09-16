"""Regression coverage for the single-active-dataset registry invariant."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.datasets import registry
from app.db.models import Dataset
from app.db.session import async_session


def _dataset(dataset_id: str, name: str) -> Dataset:
    return Dataset(
        id=dataset_id,
        name=name,
        version="1",
        status="READY",
        is_active=False,
        source_kind="builtin",
        root_path="",
        origin_note="test",
        stage_detail={},
        stats={},
    )


@pytest.mark.asyncio
async def test_activation_deactivates_every_other_dataset():
    ids = ("invariant-a", "invariant-b", "invariant-c")
    async with async_session() as session:
        await session.execute(delete(Dataset).where(Dataset.id.in_(ids)))
        rows = [_dataset(ids[0], "A"), _dataset(ids[1], "B"), _dataset(ids[2], "C")]
        session.add_all(rows)
        await registry.set_only_active(session, rows[0])
        await registry.set_only_active(session, rows[2])
        await session.commit()

        active = list(
            (await session.execute(select(Dataset.id).where(Dataset.is_active.is_(True)))).scalars()
        )
        assert active == [ids[2]]
        assert await registry.active_dataset_id(session) == ids[2]

        await session.execute(delete(Dataset).where(Dataset.id.in_(ids)))
        await session.commit()


@pytest.mark.asyncio
async def test_database_rejects_two_active_datasets():
    ids = ("invariant-unique-a", "invariant-unique-b")
    async with async_session() as session:
        await session.execute(delete(Dataset).where(Dataset.id.in_(ids)))
        first = _dataset(ids[0], "A")
        second = _dataset(ids[1], "B")
        first.is_active = True
        second.is_active = True
        session.add_all([first, second])
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
