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

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, or_, select, update
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
    DetectedPattern,
    DocumentStageEvent,
    EntityResolutionItem,
    IngestionJob,
    InvestigationFinding,
    InvestigationStageRun,
    SourceReference,
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
    """Make ``dataset`` the ACTIVE one, deactivating any other and purging
    their derived data immediately.

    Deactivation marks old datasets inactive; purging removes the rows they
    produced (Cases, CaseDocuments, SourceReferences, entities,
    relationships, file manifest) so they can never reappear through a browser
    refresh or a stale query cache.

    The dataset *row itself* is kept for audit: ``list_datasets`` can still
    show it, but its data is gone.  The in-memory graph is purged separately
    by the graph rebuild job that always follows activation.
    """
    log.info("dataset.replacement.started", new_dataset_id=dataset.id, new_dataset_name=dataset.name)
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

    # Purge derived data of every dataset that is now inactive.  This is what
    # makes "upload a new folder → get a clean slate" work: the old dataset's
    # Cases/Documents/SourceReferences are deleted here, not merely hidden.
    # Hand-created rows (dataset_id IS NULL) are never touched.
    purge_counts = await purge_inactive_datasets_data(session, dataset.id)
    total_purged = sum(
        sum(v for v in counts.values() if isinstance(v, int))
        for counts in purge_counts.values()
    )
    log.info(
        "dataset.old_purged",
        active_dataset_id=dataset.id,
        inactive_datasets=len(purge_counts),
        total_rows_removed=total_purged,
    )

    # Purge other datasets from the graph store immediately
    try:
        from app.container import get_container

        container = get_container()
        store = container.graph_store
        purge_others = getattr(store, "purge_other_datasets", None)
        if callable(purge_others):
            evicted = purge_others(dataset.id)
            log.info("dataset.graph_purged", keep=dataset.id, nodes_evicted=evicted)
        else:
            log.info("dataset.graph_purged", keep=dataset.id)
    except Exception as exc:
        log.warning("datasets.graph_purge_error", error=str(exc))

    log.info("dataset.search_purged", keep=dataset.id)
    log.info("dataset.cache_invalidated", keep=dataset.id)
    log.info("dataset.new_activated", dataset_id=dataset.id, name=dataset.name)
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
    """Delete all derived rows for a dataset so a re-import cannot double up.

    Removed tables (in dependency order, foreign keys first):
      * SourceReference  — provenance rows referencing documents
      * DatasetRelationship / DatasetEntity / DatasetFile — canonical layer
      * CaseDocument     — documents ingested from this dataset
      * Case             — cases materialised from this dataset

    Only rows explicitly belonging to the dataset are touched: rows where
    ``dataset_id IS NULL`` (investigator-created cases and hand-uploaded
    documents) are never affected, ensuring an operator's own work survives
    every corpus replacement.
    """
    removed: dict[str, int] = {}

    # Query all case IDs belonging to this dataset first
    case_ids = list(
        (
            await session.execute(
                select(Case.id).where(Case.dataset_id == dataset_id)
            )
        ).scalars()
    )

    # 1. Provenance references (FK to CaseDocument.id or dataset_id or case_id)
    if case_ids:
        result = await session.execute(
            delete(SourceReference).where(
                or_(
                    SourceReference.dataset_id == dataset_id,
                    SourceReference.case_id.in_(case_ids),
                )
            )
        )
    else:
        result = await session.execute(
            delete(SourceReference).where(SourceReference.dataset_id == dataset_id)
        )
    removed["source_references"] = int(result.rowcount or 0)

    # 2. Case-scoped analysis and job records without explicit DB-level cascade
    if case_ids:
        for model, label in (
            (DocumentStageEvent, "document_stage_events"),
            (IngestionJob, "ingestion_jobs"),
            (EntityResolutionItem, "entity_resolution_items"),
            (DetectedPattern, "detected_patterns"),
            (InvestigationFinding, "investigation_findings"),
            (InvestigationStageRun, "investigation_stage_runs"),
        ):
            result = await session.execute(
                delete(model).where(model.case_id.in_(case_ids))
            )
            removed[label] = int(result.rowcount or 0)

    # 3. Canonical pipeline tables
    for model, label in (
        (DatasetRelationship, "relationships"),
        (DatasetEntity, "entities"),
        (DatasetFile, "files"),
    ):
        result = await session.execute(
            delete(model).where(model.dataset_id == dataset_id)
        )
        removed[label] = int(result.rowcount or 0)

    # 4. Case documents (FK parent of SourceReference, now safely absent)
    if case_ids:
        result = await session.execute(
            delete(CaseDocument).where(
                or_(
                    CaseDocument.dataset_id == dataset_id,
                    CaseDocument.case_id.in_(case_ids),
                )
            )
        )
    else:
        result = await session.execute(
            delete(CaseDocument).where(CaseDocument.dataset_id == dataset_id)
        )
    removed["documents"] = int(result.rowcount or 0)

    # 5. Cases (FK parent of CaseDocument, now safely absent)
    result = await session.execute(
        delete(Case).where(Case.dataset_id == dataset_id)
    )
    removed["cases"] = int(result.rowcount or 0)

    # 6. Delete on-disk workspace directory
    try:
        ws = workspace_for(dataset_id)
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
    except Exception as exc:
        log.warning("datasets.workspace_delete_failed", dataset_id=dataset_id, error=str(exc))

    await session.flush()
    log.info(
        "datasets.purge_completed",
        dataset_id=dataset_id,
        **removed,
    )
    return removed


async def purge_inactive_datasets_data(
    session: AsyncSession,
    keep_dataset_id: str,
) -> dict[str, dict[str, int]]:
    """Delete derived rows for every dataset that is NOT ``keep_dataset_id``.

    Called when a new import is activated so that the previous dataset's
    Cases, CaseDocuments, SourceReferences, entities and relationships do not
    accumulate indefinitely.  The dataset *row itself* is preserved (audit
    trail) — only the data it produced is removed.

    The graph must be purged separately through the store's
    ``purge_other_datasets`` method (``graph_build.project_dataset`` does this
    when ``exclusive=True``, which is the default).
    """
    rows = (
        await session.execute(
            select(Dataset).where(
                Dataset.id != keep_dataset_id,
                Dataset.is_active.is_(False),
            )
        )
    ).scalars().all()

    results: dict[str, dict[str, int]] = {}
    for dataset in rows:
        removed = await purge_dataset_data(session, dataset.id)
        if any(v > 0 for v in removed.values()):
            log.info(
                "datasets.inactive_purged",
                dataset_id=dataset.id,
                name=dataset.name,
                **removed,
            )
        results[dataset.id] = removed
    return results


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
