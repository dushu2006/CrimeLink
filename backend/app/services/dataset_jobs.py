"""Background dataset jobs with observable, honest progress.

A graph rebuild takes long enough that the investigator must be told what is
happening, and the two ways of telling them -- a WebSocket and polling -- have
to agree, because they read the same source of truth:

* the ``dataset_jobs`` row is the authoritative state, written at every stage;
* the event bus is a *notification* that the row changed.

That ordering matters.  Progress is persisted **before** it is published, so a
client that connects late, misses a frame, or falls back to polling can always
recover the true state from ``GET /datasets/jobs/{job_id}``.  Nothing here
invents a percentage: every number comes from the pipeline reporting real
work, and a job that is stuck at 40% is telling the truth about being stuck.

A dropped WebSocket must never affect the job.  The job runs in a task owned
by the application, not by any connection, so closing the browser does not
cancel a rebuild and a failed handshake does not fail an import.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from app.container import get_container
from app.datasets import registry
from app.db.base import utcnow
from app.db.models import Dataset, DatasetJob
from app.db.session import async_session
from app.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cycle avoided at runtime
    from app.datasets.pipeline import ImportOptions

log = get_logger("crimelink.services.dataset_jobs")

#: Event-bus channel for one job.  Subscribed by ``WS /jobs/ws/job/{job_id}``.
def job_channel(job_id: str) -> str:
    return f"job:{job_id}"


TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

#: Tasks are held so the event loop cannot garbage-collect a running job, and
#: so a second rebuild of the same dataset can be refused while one is live.
_running: dict[str, asyncio.Task] = {}


class JobReporter:
    """Writes progress to the job row, then announces it.

    One instance per job.  ``__call__`` matches the pipeline's
    ``(stage, pct, message)`` progress protocol so it can be handed straight
    to :func:`app.datasets.pipeline.run_import` or
    :func:`app.datasets.pipeline.rebuild_graph`.
    """

    def __init__(self, job_id: str, dataset_id: str | None) -> None:
        self.job_id = job_id
        self.dataset_id = dataset_id
        self._last_pct = -1
        #: Last payload we managed to persist, so a write that loses a race
        #: with the importer for the database lock can still be announced.
        self._last_payload: dict[str, Any] = {}

    async def __call__(self, stage: str, pct: int, message: str) -> None:
        await self.update(stage=stage, progress_pct=pct, message=message)

    async def update(
        self,
        *,
        stage: str | None = None,
        progress_pct: int | None = None,
        message: str | None = None,
        status: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Persist a state change and publish it. Returns the new job row.

        **Reporting progress must never break the thing being reported on.**
        The importer holds the database's write lock for long stretches, and
        this writes from its own connection; when the two collide, the update
        is retried and then given up on. Losing a progress frame is a cosmetic
        problem, and failing an hour-long import because a progress bar could
        not be updated is not.
        """
        terminal_update = status in TERMINAL_STATUSES
        attempts = 6 if terminal_update else 2
        for attempt in range(attempts):
            try:
                payload = await self._persist(
                    stage=stage,
                    progress_pct=progress_pct,
                    message=message,
                    status=status,
                    result=result,
                    error=error,
                )
            except Exception as exc:  # noqa: BLE001 - see the docstring
                if attempt == attempts - 1:
                    log.warning(
                        "dataset_jobs.progress_write_failed",
                        job_id=self.job_id,
                        stage=stage,
                        status=status,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    break
                # Back off and let the writer that holds the lock finish.
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            self._last_payload = payload
            self._publish(payload)
            return payload

        # Persisting failed. Announce the intended state anyway so a watching
        # client is not left frozen; the next successful write reconciles it.
        provisional = dict(self._last_payload)
        provisional.update(
            {
                key: value
                for key, value in (
                    ("stage", stage),
                    ("progress_pct", progress_pct),
                    ("message", message),
                    ("status", status),
                    ("error", error),
                )
                if value is not None
            }
        )
        provisional.setdefault("id", self.job_id)
        provisional["terminal"] = provisional.get("status") in TERMINAL_STATUSES
        if provisional.get("terminal"):
            provisional["progress_pct"] = 100
        self._publish(provisional)
        return provisional

    async def _persist(
        self,
        *,
        stage: str | None,
        progress_pct: int | None,
        message: str | None,
        status: str | None,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        async with async_session() as session:
            job = await session.get(DatasetJob, self.job_id)
            if job is None:  # pragma: no cover - the row is created up front
                log.warning("dataset_jobs.row_missing", job_id=self.job_id)
                return {}
            if stage is not None:
                job.stage = stage
            if progress_pct is not None:
                # Progress never goes backwards: a later stage reporting a
                # lower percentage would make the bar jitter and would make
                # the UI look broken when it is not.
                job.progress_pct = max(int(job.progress_pct or 0), int(progress_pct))
            if message is not None:
                job.message = message
            if status is not None:
                job.status = status
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            if job.status in TERMINAL_STATUSES and job.finished_at is None:
                job.finished_at = utcnow()
                job.progress_pct = 100 if job.status == "SUCCEEDED" else job.progress_pct
            job.updated_at = utcnow()

            steps = list(job.steps or [])
            if stage is not None and (not steps or steps[-1].get("stage") != stage):
                steps.append(
                    {
                        "stage": stage,
                        "message": message,
                        "at": utcnow().isoformat(),
                    }
                )
                job.steps = steps[-60:]

            await session.commit()
            return registry.job_row(job)

    async def attach_dataset(self, dataset_id: str) -> None:
        """Bind this job to the dataset it turned out to be building.

        An import creates its dataset partway through, so the job starts
        without one. Recording it as soon as it exists is what lets the
        console link from a running job to the dataset page.
        """
        self.dataset_id = dataset_id
        async with async_session() as session:
            job = await session.get(DatasetJob, self.job_id)
            if job is not None and job.dataset_id != dataset_id:
                job.dataset_id = dataset_id
                job.updated_at = utcnow()
                await session.commit()

    def _publish(self, payload: dict[str, Any]) -> None:
        """Announce a state change; never let a bus failure break the job."""
        event = {
            "type": "job_progress" if not payload.get("terminal") else "job_finished",
            "job_id": self.job_id,
            "dataset_id": self.dataset_id,
            **payload,
        }
        try:
            get_container().event_bus.publish(job_channel(self.job_id), event)
        except Exception:  # noqa: BLE001 - notification is best-effort by design
            log.warning("dataset_jobs.publish_failed", job_id=self.job_id, exc_info=True)


async def start_job(
    *,
    kind: str,
    dataset_id: str | None,
    requested_by: str | None,
    work: Callable[[JobReporter], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Create a job row, launch the work in the background, return the row.

    Returns immediately with a ``job_id`` so the caller can start watching:
    the HTTP request that starts a rebuild must not wait for the rebuild.
    """
    async with async_session() as session:
        job = await registry.create_job(
            session, kind=kind, dataset_id=dataset_id, requested_by=requested_by
        )
        await session.commit()
        row = registry.job_row(job)

    reporter = JobReporter(row["id"], dataset_id)

    async def runner() -> None:
        try:
            await reporter.update(
                status="RUNNING", stage="STARTING", progress_pct=1,
                message="Job started",
            )
            result = await work(reporter)
            await reporter.update(
                status="SUCCEEDED",
                stage="COMPLETED",
                progress_pct=100,
                message="Completed",
                result=result or {},
            )
            log.info("dataset_jobs.succeeded", job_id=row["id"], kind=kind)
        except asyncio.CancelledError:
            await reporter.update(
                status="CANCELLED", stage="CANCELLED", message="Job cancelled"
            )
            raise
        except Exception as exc:  # noqa: BLE001 - the failure must reach the UI
            detail = f"{type(exc).__name__}: {exc}"
            log.exception("dataset_jobs.failed", job_id=row["id"], kind=kind)
            await reporter.update(
                status="FAILED", stage="FAILED", message=detail, error=detail
            )
        finally:
            _running.pop(row["id"], None)

    task = asyncio.create_task(runner(), name=f"dataset-job-{row['id']}")
    _running[row["id"]] = task
    return row


async def get_job(job_id: str) -> dict[str, Any] | None:
    """The authoritative job state — what polling reads."""
    async with async_session() as session:
        job = await session.get(DatasetJob, job_id)
        return registry.job_row(job) if job is not None else None


def is_running(job_id: str) -> bool:
    task = _running.get(job_id)
    return task is not None and not task.done()


async def active_job_for_dataset(dataset_id: str, kind: str | None = None) -> str | None:
    """The id of a live job for this dataset, if any.

    Used to refuse a second concurrent rebuild rather than letting two
    projections race each other into the same graph.
    """
    from sqlalchemy import select

    async with async_session() as session:
        stmt = (
            select(DatasetJob)
            .where(
                DatasetJob.dataset_id == dataset_id,
                DatasetJob.status.in_(["QUEUED", "RUNNING"]),
            )
            .order_by(DatasetJob.created_at.desc())
        )
        if kind:
            stmt = stmt.where(DatasetJob.kind == kind)
        job = (await session.execute(stmt)).scalars().first()
    if job is None:
        return None
    if job.status == "RUNNING" and not is_running(job.id):
        # The process restarted while the job was running: the row is stale,
        # so say so instead of blocking rebuilds forever.
        await JobReporter(job.id, dataset_id).update(
            status="FAILED",
            stage="FAILED",
            message="The server restarted while this job was running.",
            error="interrupted_by_restart",
        )
        return None
    return job.id


# ---------------------------------------------------------------------------
# The jobs themselves
# ---------------------------------------------------------------------------


async def run_graph_rebuild(dataset_id: str, reporter: JobReporter) -> dict[str, Any]:
    """Re-project one dataset's graph, reporting each stage as it happens."""
    from app.datasets.pipeline import rebuild_graph

    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            raise LookupError(f"Dataset {dataset_id} no longer exists.")
        stats = await rebuild_graph(session, dataset, progress=reporter)
    return {
        "dataset_id": dataset_id,
        "nodes_written": stats.get("nodes_written", 0),
        "edges_written": stats.get("edges_written", 0),
        "entities_considered": stats.get("entities_considered", 0),
        "relationships_considered": stats.get("relationships_considered", 0),
        "cases": stats.get("cases", 0),
    }


async def run_dataset_import(
    sources: Sequence[Path | str],
    options: "ImportOptions",
    reporter: JobReporter,
) -> dict[str, Any]:
    """Run a full import as a background job.

    The dataset row is created by the pipeline, so the job's ``dataset_id`` is
    attached as soon as it exists -- otherwise a user watching the job would
    have no way to navigate to the dataset it is building.
    """
    from app.datasets.pipeline import run_import

    async with async_session() as session:
        report = await run_import(session, sources, options, progress=reporter)

    if report.dataset_id:
        await reporter.attach_dataset(report.dataset_id)
    if report.error:
        # The pipeline records the failure on the dataset and returns; the job
        # must fail too, or the console would show a green tick over a broken
        # import.
        raise RuntimeError(report.error)
    return report.as_dict()



def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
