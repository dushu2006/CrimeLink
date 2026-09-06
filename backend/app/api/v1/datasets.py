"""Dataset lifecycle and graph-build endpoints.

The Administration screen drives everything here.  Two design points:

* **Starting work returns a job, not a result.**  ``POST .../graph/rebuild``
  answers in milliseconds with a ``job_id``; the caller then watches that job
  over the WebSocket or by polling.  An HTTP request that blocks for the
  length of a graph build is a request that times out.
* **The job row is the source of truth.**  ``GET /datasets/jobs/{job_id}``
  returns exactly what the WebSocket publishes, so the polling fallback is
  not a degraded approximation -- it is the same data on a slower cadence.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.datasets import registry
from app.datasets.pipeline import DEFAULT_JURISDICTION, ImportOptions
from app.db.base import new_uuid, utcnow
from app.db.models import DatasetFile
from app.db.session import get_db_session
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.logging import get_logger
from app.security.deps import Principal, get_principal, require_roles
from app.services import dataset_jobs

log = get_logger("crimelink.api.datasets")

router = APIRouter(prefix="/datasets", tags=["datasets"])

#: Anything that could escape the staging directory is stripped from an
#: uploaded path. A browser is not a trusted source of file paths.
_UNSAFE_SEGMENT = re.compile(r"^\.+$")
MAX_UPLOAD_FILES = 5000
UPLOAD_CHUNK = 1024 * 1024


class AcceptMappingsRequest(BaseModel):
    """Which mappings the operator is signing off; empty means all of them."""

    file_ids: list[str] = Field(default_factory=list)


def _safe_relative_path(raw: str, fallback: str) -> Path:
    """Turn a client-supplied path into a safe path under the staging root.

    Folder uploads send ``webkitRelativePath``, so the original layout can be
    preserved -- that layout is real provenance and worth keeping. It is not,
    however, worth trusting: absolute paths, drive letters and ``..`` segments
    are discarded rather than quietly reinterpreted.
    """
    candidate = (raw or fallback or "upload.bin").replace("\\", "/")
    parts: list[str] = []
    for segment in candidate.split("/"):
        segment = segment.strip()
        if not segment or _UNSAFE_SEGMENT.match(segment) or ":" in segment:
            continue
        parts.append(segment)
    if not parts:
        parts = [Path(fallback or "upload.bin").name or "upload.bin"]
    return Path(*parts)


async def _require_dataset(session: AsyncSession, dataset_id: str):
    dataset = await registry.get_dataset(session, dataset_id)
    if dataset is None:
        raise NotFoundError("Dataset not found.")
    return dataset


@router.get("")
async def list_datasets(
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Every dataset the platform knows about, newest first."""
    datasets = await registry.list_datasets(session, limit=limit)
    return {"items": [registry.dataset_row(dataset) for dataset in datasets]}


@router.get("/active")
async def get_active_dataset(
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """The one dataset every page reads. ``null`` when none is active."""
    dataset = await registry.active_dataset(session)
    if dataset is None:
        return {"active": None}
    stats = await registry.dataset_stats(session, dataset.id)
    return {"active": registry.dataset_row(dataset, stats)}


# NOTE: registered before "/{dataset_id}" on purpose. FastAPI matches routes in
# declaration order, so a literal segment must be declared ahead of the
# parameterised one it would otherwise be swallowed by — "/datasets/jobs/abc"
# would bind dataset_id="jobs" and 404.
@router.get("/jobs/{job_id}")
async def get_dataset_job(
    job_id: str,
    principal: Principal = Depends(get_principal),
) -> dict:
    """Authoritative job state — the polling fallback reads this.

    Identical in shape to the payload pushed over the WebSocket, so a client
    that switches between the two sees one continuous stream of truth.
    """
    job = await dataset_jobs.get_job(job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return job


@router.post("/import")
async def import_dataset(
    files: list[UploadFile] = File(..., description="Any mix of files, or a ZIP."),
    name: str = Form(""),
    version: str = Form("1"),
    paths: list[str] | None = Form(None),
    activate: bool = Form(True),
    build_graph: bool = Form(True),
    jurisdiction_id: str = Form(DEFAULT_JURISDICTION),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Upload and import a dataset. Returns a ``job_id`` to watch.

    Deliberately unopinionated about what arrives: one CSV, forty mixed files,
    a ZIP, or a whole folder tree uploaded by the browser. There is no
    required folder name and no required file naming -- discovery, type
    detection and schema mapping work it out downstream. Rejecting an upload
    because it lacks an ``operational/`` directory would be an artificial
    failure, and users do not organise their evidence for our convenience.
    """
    if not files:
        raise ValidationFailedError("Select at least one file to import.")
    if len(files) > MAX_UPLOAD_FILES:
        raise ValidationFailedError(
            f"{len(files)} files is more than this endpoint accepts "
            f"({MAX_UPLOAD_FILES}). Upload them as a ZIP archive instead."
        )

    settings = get_settings()
    staging = Path(settings.data_dir) / "uploads" / new_uuid()
    staging.mkdir(parents=True, exist_ok=True)

    relative_paths = list(paths or [])
    written = 0
    total_bytes = 0
    for index, upload in enumerate(files):
        hint = relative_paths[index] if index < len(relative_paths) else ""
        target = staging / _safe_relative_path(hint, upload.filename or f"file-{index}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            while chunk := await upload.read(UPLOAD_CHUNK):
                handle.write(chunk)
                total_bytes += len(chunk)
        await upload.close()
        written += 1

    label = name.strip() or (
        Path(files[0].filename or "Uploaded dataset").stem
        if written == 1
        else f"Upload of {written} files"
    )
    options = ImportOptions(
        name=label,
        version=str(version or "1"),
        source_kind="upload",
        origin_note=f"{written} file(s), {total_bytes} bytes, uploaded by {principal.id}",
        activate=activate,
        build_graph=build_graph,
        copy_inputs=True,
        jurisdiction_id=jurisdiction_id or DEFAULT_JURISDICTION,
        created_by=principal.id,
    )

    async def work(reporter):
        return await dataset_jobs.run_dataset_import([staging], options, reporter)

    job = await dataset_jobs.start_job(
        kind="import",
        dataset_id=None,
        requested_by=principal.id,
        work=work,
    )
    log.info(
        "datasets.import_started",
        job_id=job["id"],
        files=written,
        bytes=total_bytes,
        actor=principal.id,
    )
    return {"job_id": job["id"], "job": job, "files_received": written}


@router.post("/import/path")
async def import_dataset_from_path(
    payload: dict,
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Import a dataset that is already on the server's disk.

    This is how the bundled corpus is loaded, and how an operator imports a
    directory too large to push through a browser. The path is read from the
    request rather than hardcoded, so no particular dataset is privileged.
    """
    raw = str(payload.get("path") or "").strip()
    if not raw:
        raise ValidationFailedError("Provide the path of the folder or file to import.")
    source = Path(raw).expanduser()
    if not source.is_absolute():
        source = (Path(get_settings().data_dir).parent / source).resolve()
    if not source.exists():
        raise ValidationFailedError(f"Nothing exists at {source}.")

    options = ImportOptions(
        name=str(payload.get("name") or source.name),
        version=str(payload.get("version") or "1"),
        source_kind="folder" if source.is_dir() else "file",
        origin_note=f"Server path {source}",
        activate=bool(payload.get("activate", True)),
        build_graph=bool(payload.get("build_graph", True)),
        # The source stays where it is: copying a multi-gigabyte corpus into
        # the workspace to read it once wastes disk for no benefit.
        copy_inputs=bool(payload.get("copy_inputs", False)),
        jurisdiction_id=str(payload.get("jurisdiction_id") or DEFAULT_JURISDICTION),
        created_by=principal.id,
    )

    async def work(reporter):
        return await dataset_jobs.run_dataset_import([source], options, reporter)

    job = await dataset_jobs.start_job(
        kind="import",
        dataset_id=None,
        requested_by=principal.id,
        work=work,
    )
    log.info(
        "datasets.import_started", job_id=job["id"], source=str(source), actor=principal.id
    )
    return {"job_id": job["id"], "job": job, "source": str(source)}


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    dataset = await _require_dataset(session, dataset_id)
    stats = await registry.dataset_stats(session, dataset.id)
    return registry.dataset_row(dataset, stats)


@router.get("/{dataset_id}/mappings")
async def dataset_mappings(
    dataset_id: str,
    needs_review: bool = Query(False, description="Only tables an operator should look at"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """How each tabular file was mapped onto the canonical schema.

    Column mapping is inference, and inference can be wrong: a header may be
    stale, shifted, or contradicted by the values underneath it. Everything the
    mapper concluded -- and everything it disbelieved -- is reported here so a
    human can accept it or re-import with a correction, rather than discovering
    the problem later as a strange row on the People page.
    """
    dataset = await _require_dataset(session, dataset_id)
    query = (
        select(DatasetFile)
        .where(DatasetFile.dataset_id == dataset.id, DatasetFile.file_kind == "table")
        .order_by(DatasetFile.classification_confidence.asc(), DatasetFile.relative_path.asc())
    )
    rows = list((await session.execute(query)).scalars())

    def review_needed(row: DatasetFile) -> bool:
        return bool(row.mapping_accepted_at is None and (row.mapping_notes or []))

    items = [
        {
            "file_id": row.id,
            "path": row.relative_path,
            "filename": row.filename,
            "semantic_type": row.semantic_type,
            "confidence": round(float(row.classification_confidence or 0.0), 3),
            "row_count": row.row_count,
            "sheets": row.sheet_names,
            "unmapped_columns": row.unmapped_columns,
            "columns": row.column_mapping,
            "notes": row.mapping_notes,
            "needs_review": review_needed(row),
            "accepted_at": row.mapping_accepted_at.isoformat() if row.mapping_accepted_at else None,
            "accepted_by": row.mapping_accepted_by,
        }
        for row in rows
    ]
    if needs_review:
        items = [item for item in items if item["needs_review"]]
    return {
        "dataset_id": dataset.id,
        "items": items[:limit],
        "total": len(items),
        "review_pending": sum(1 for row in rows if review_needed(row)),
    }


@router.post("/{dataset_id}/mappings/accept")
async def accept_dataset_mappings(
    dataset_id: str,
    payload: AcceptMappingsRequest,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Record that an operator has reviewed and accepted these mappings."""
    dataset = await _require_dataset(session, dataset_id)
    query = select(DatasetFile).where(DatasetFile.dataset_id == dataset.id)
    if payload.file_ids:
        query = query.where(DatasetFile.id.in_(payload.file_ids))
    rows = list((await session.execute(query)).scalars())
    if payload.file_ids and len(rows) != len(set(payload.file_ids)):
        raise NotFoundError("One or more files do not belong to this dataset.")
    accepted = 0
    for row in rows:
        if not (row.mapping_notes or []):
            continue
        row.mapping_accepted_at = utcnow()
        row.mapping_accepted_by = principal.id
        accepted += 1
    await session.commit()
    log.info(
        "datasets.mappings_accepted",
        dataset_id=dataset.id,
        files=accepted,
        actor=principal.id,
    )
    return {"dataset_id": dataset.id, "accepted": accepted}


@router.post("/{dataset_id}/activate")
async def activate_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Make this dataset the active one, and re-project the graph to match.

    Activation is not just a flag: every page reads the active dataset, so the
    graph has to follow it in the same breath. A rebuild job is started and
    its id returned, which both evicts the previous dataset's nodes and
    projects this one -- otherwise the tables would show one dataset while the
    graph showed another.
    """
    dataset = await _require_dataset(session, dataset_id)
    await registry.activate(session, dataset)
    await session.commit()
    stats = await registry.dataset_stats(session, dataset.id)
    row = registry.dataset_row(dataset, stats)

    job_id = None
    if not await dataset_jobs.active_job_for_dataset(dataset.id):
        async def work(reporter):
            return await dataset_jobs.run_graph_rebuild(dataset.id, reporter)

        job = await dataset_jobs.start_job(
            kind="build_graph",
            dataset_id=dataset.id,
            requested_by=principal.id,
            work=work,
        )
        job_id = job["id"]
        row["job"] = job
    log.info(
        "datasets.activated_via_api",
        dataset_id=dataset.id,
        job_id=job_id,
        actor=principal.id,
    )
    row["job_id"] = job_id
    return row


@router.post("/{dataset_id}/graph/rebuild")
async def rebuild_dataset_graph(
    dataset_id: str,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Start a graph rebuild. Returns a ``job_id`` to watch.

    Refuses to start a second rebuild while one is already running for this
    dataset: two concurrent projections would interleave writes into the same
    graph and neither would be trustworthy.
    """
    dataset = await _require_dataset(session, dataset_id)

    existing = await dataset_jobs.active_job_for_dataset(dataset.id)
    if existing:
        raise ConflictError(
            f"A job is already running for this dataset (job {existing}). "
            "Watch that job, or wait for it to finish before starting another."
        )

    async def work(reporter):
        return await dataset_jobs.run_graph_rebuild(dataset.id, reporter)

    job = await dataset_jobs.start_job(
        kind="build_graph",
        dataset_id=dataset.id,
        requested_by=principal.id,
        work=work,
    )
    log.info(
        "datasets.graph_rebuild_started",
        dataset_id=dataset.id,
        job_id=job["id"],
        actor=principal.id,
    )
    # ``job_id`` is duplicated at the top level because that is the field the
    # console reads to start watching; the full row is included so the UI can
    # render the initial state without a second round trip.
    return {"job_id": job["id"], "job": job}

