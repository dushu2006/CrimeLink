"""The dataset registry -- CrimeLink's single source of truth resolver.

Every dataset-aware query in the system funnels through :func:`active_dataset`
or :func:`active_dataset_id`.  That is deliberate: when there is exactly one
place that answers "which dataset are we looking at?", two pages cannot
disagree, and a browser refresh cannot resurrect a previous import.

Stage vocabulary (also what the UI displays):

    UPLOADED -> VALIDATING -> NORMALIZING -> INGESTING ->
    BUILDING_RELATIONSHIPS -> BUILDING_GRAPH -> INDEXING -> READY
                                                         \\-> FAILED
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    DatasetJob,
    DatasetRelationship,
)
from app.logging import get_logger

log = get_logger("crimelink.datasets.registry")

STAGES: tuple[str, ...] = (
    "UPLOADED",
    "VALIDATING",
    "NORMALIZING",
    "INGESTING",
    "BUILDING_RELATIONSHIPS",
    "BUILDING_GRAPH",
    "INDEXING",
    "READY",
)
TERMINAL_STAGES = frozenset({"READY", "FAILED", "ARCHIVED"})


def datasets_root() -> Path:
    """Workspace under which every imported dataset keeps its own files."""
    root = get_settings().data_dir / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_for(dataset_id: str) -> Path:
    path = datasets_root() / dataset_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def create_dataset(
    session: AsyncSession,
    *,
    name: str,
    version: str = "1",
    source_kind: str = "folder",
    origin_note: str = "",
    created_by: str | None = None,
) -> Dataset:
    dataset = Dataset(
        id=new_uuid(),
        name=name.strip() or "Untitled dataset",
        version=str(version),
        status="UPLOADED",
        source_kind=source_kind,
        origin_note=origin_note,
        created_by=created_by,
        root_path="",
        stage_detail={"stage": "UPLOADED", "steps": []},
        stats={},
    )
    session.add(dataset)
    await session.flush()
    dataset.root_path = str(workspace_for(dataset.id))
    await session.flush()
    return dataset


async def set_stage(
    session: AsyncSession,
    dataset: Dataset,
    stage: str,
    *,
    detail: str = "",
    error: str | None = None,
) -> None:
    dataset.status = stage
    steps = list((dataset.stage_detail or {}).get("steps", []))
    steps.append({"stage": stage, "detail": detail, "at": utcnow().isoformat()})
    dataset.stage_detail = {"stage": stage, "detail": detail, "steps": steps[-40:]}
    if error:
        dataset.error = error
    await session.flush()


async def get_dataset(session: AsyncSession, dataset_id: str) -> Dataset | None:
    return await session.get(Dataset, dataset_id)


async def list_datasets(session: AsyncSession, *, limit: int = 100) -> list[Dataset]:
    rows = await session.execute(
        select(Dataset).order_by(Dataset.created_at.desc()).limit(limit)
    )
    return list(rows.scalars())


async def active_dataset(session: AsyncSession) -> Dataset | None:
    """The one dataset every page is allowed to read."""
    row = await session.execute(
        select(Dataset).where(Dataset.is_active.is_(True)).limit(1)
    )
    return row.scalar_one_or_none()


async def active_dataset_id(session: AsyncSession) -> str | None:
    row = await session.execute(
        select(Dataset.id).where(Dataset.is_active.is_(True)).limit(1)
    )
    return row.scalar_one_or_none()


async def visibility_filter(session: AsyncSession, model: Any):
    """A WHERE clause restricting *model* rows to the active dataset.

    Every dataset-owned table carries ``dataset_id``. Rows with none belong to
    no import -- a case an investigator typed in by hand, a document they
    uploaded themselves -- and stay visible always; deleting somebody's own
    work because a corpus was replaced would be indefensible.

    Rows belonging to a dataset are visible only while that dataset is the
    active one. This is the single mechanism that stops a replaced dataset
    from reappearing after a browser refresh, and it is applied in SQL rather
    than filtered afterwards so counts, pagination and search all agree.
    """
    active = await active_dataset_id(session)
    column = model.dataset_id
    if active is None:
        # Nothing is active: only non-dataset rows are legitimate.
        return column.is_(None)
    return or_(column.is_(None), column == active)


def belongs_to_active(row: Any, active_dataset_id_value: str | None) -> bool:
    """Whether a single already-loaded row is visible under the active dataset."""
    owner = getattr(row, "dataset_id", None)
    return owner is None or owner == active_dataset_id_value


async def activate(session: AsyncSession, dataset: Dataset) -> Dataset:
    """Make ``dataset`` the ACTIVE one, deactivating any other.

    Deactivation is not deletion: a previous dataset stays queryable by id for
    audit purposes, it simply stops being what the application shows.
    """
    await session.execute(
        update(Dataset)
        .where(Dataset.id != dataset.id, Dataset.is_active.is_(True))
        .values(is_active=False)
    )
    dataset.is_active = True
    dataset.activated_at = utcnow()
    if dataset.status not in TERMINAL_STAGES:
        dataset.status = "READY"
    await session.flush()
    log.info("datasets.activated", dataset_id=dataset.id, name=dataset.name)
    return dataset


async def deactivate_all(session: AsyncSession) -> None:
    await session.execute(update(Dataset).values(is_active=False))
    await session.flush()


# ---------------------------------------------------------------------------
# Statistics -- always counted, never estimated
# ---------------------------------------------------------------------------


async def dataset_stats(session: AsyncSession, dataset_id: str) -> dict[str, Any]:
    async def count(model: Any, *conditions: Any) -> int:
        stmt = select(func.count()).select_from(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int((await session.execute(stmt)).scalar() or 0)

    by_type_rows = (
        await session.execute(
            select(DatasetEntity.entity_type, func.count(DatasetEntity.id))
            .where(DatasetEntity.dataset_id == dataset_id)
            .group_by(DatasetEntity.entity_type)
        )
    ).all()
    by_rel_rows = (
        await session.execute(
            select(DatasetRelationship.rel_type, func.count(DatasetRelationship.id))
            .where(DatasetRelationship.dataset_id == dataset_id)
            .group_by(DatasetRelationship.rel_type)
        )
    ).all()
    by_file_status = (
        await session.execute(
            select(DatasetFile.status, func.count(DatasetFile.id))
            .where(DatasetFile.dataset_id == dataset_id)
            .group_by(DatasetFile.status)
        )
    ).all()

    return {
        "files": await count(DatasetFile, DatasetFile.dataset_id == dataset_id),
        "files_by_status": {str(k): int(v) for k, v in by_file_status},
        "entities": await count(DatasetEntity, DatasetEntity.dataset_id == dataset_id),
        "entities_by_type": {str(k): int(v) for k, v in sorted(by_type_rows)},
        "relationships": await count(
            DatasetRelationship, DatasetRelationship.dataset_id == dataset_id
        ),
        "relationships_by_type": {str(k): int(v) for k, v in sorted(by_rel_rows)},
        "cases": await count(Case, Case.dataset_id == dataset_id),
        "documents": await count(
            CaseDocument,
            CaseDocument.dataset_id == dataset_id,
            CaseDocument.is_deleted.is_(False),
        ),
    }


def dataset_row(dataset: Dataset, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "status": dataset.status,
        "is_active": bool(dataset.is_active),
        "source_kind": dataset.source_kind,
        "root_path": dataset.root_path,
        "origin_note": dataset.origin_note,
        "error": dataset.error,
        "stage": (dataset.stage_detail or {}).get("stage", dataset.status),
        "steps": (dataset.stage_detail or {}).get("steps", []),
        "stats": stats if stats is not None else (dataset.stats or {}),
        "graph_built_at": _iso(dataset.graph_built_at),
        "search_indexed_at": _iso(dataset.search_indexed_at),
        "ai_indexed_at": _iso(dataset.ai_indexed_at),
        "created_at": _iso(dataset.created_at),
        "activated_at": _iso(dataset.activated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def create_job(
    session: AsyncSession,
    *,
    kind: str,
    dataset_id: str | None = None,
    requested_by: str | None = None,
) -> DatasetJob:
    job = DatasetJob(
        id=new_uuid(),
        dataset_id=dataset_id,
        kind=kind,
        status="QUEUED",
        stage="QUEUED",
        progress_pct=0,
        message="Queued",
        steps=[],
        requested_by=requested_by,
    )
    session.add(job)
    await session.flush()
    return job


def job_row(job: DatasetJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "dataset_id": job.dataset_id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "steps": list(job.steps or []),
        "result": dict(job.result or {}),
        "error": job.error,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "finished_at": _iso(job.finished_at),
        "terminal": job.status in {"SUCCEEDED", "FAILED"},
    }


# ---------------------------------------------------------------------------
# Purge -- removing a dataset's derived data so nothing orphaned survives
# ---------------------------------------------------------------------------


async def purge_dataset_data(session: AsyncSession, dataset_id: str) -> dict[str, int]:
    """Delete a dataset's canonical rows so a re-import cannot double up.

    Only *derived* rows are removed (entities, relationships, file manifest).
    Cases and documents are kept: they are audited objects, and the dataset
    filter already hides them when the dataset is not active.
    """
    from sqlalchemy import delete

    removed: dict[str, int] = {}
    for model, label in (
        (DatasetRelationship, "relationships"),
        (DatasetEntity, "entities"),
        (DatasetFile, "files"),
    ):
        result = await session.execute(
            delete(model).where(model.dataset_id == dataset_id)
        )
        removed[label] = int(result.rowcount or 0)
    await session.flush()
    return removed


async def entity_index(
    session: AsyncSession, dataset_id: str, canonical_ids: Iterable[str]
) -> dict[str, DatasetEntity]:
    ids = [c for c in canonical_ids if c]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(DatasetEntity).where(
                DatasetEntity.dataset_id == dataset_id,
                DatasetEntity.canonical_id.in_(ids),
            )
        )
    ).scalars()
    return {row.canonical_id: row for row in rows}
