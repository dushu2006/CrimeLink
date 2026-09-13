"""Background investigation jobs with honest stage tracking.

Mirrors dataset_jobs: progress persisted before publish, so polling and
WebSocket agree. A dropped WebSocket never affects the job; the job runs in
a task owned by the application, not by any connection.

Stages are explicit and never faked:
QUEUED, PREPARING, ANALYZING_GRAPH, DETECTING_PATTERNS, RETRIEVING_EVIDENCE,
SEARCHING_CONTRADICTIONS, REASONING, VALIDATING, GENERATING_EXPLANATION,
COMPLETED plus failure states AI_UNAVAILABLE, AI_TIMEOUT, AI_INVALID_RESPONSE,
DATA_ERROR, GRAPH_ERROR, INTERNAL_ERROR.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from app.container import get_container
from app.db.base import new_uuid, utcnow
from app.db.models import InvestigationJob
from app.db.session import async_session
from app.logging import get_logger

log = get_logger("crimelink.services.investigation_jobs")

TERMINAL_STATUSES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "AI_UNAVAILABLE",
        "AI_TIMEOUT",
        "AI_INVALID_RESPONSE",
        "DATA_ERROR",
        "GRAPH_ERROR",
        "INTERNAL_ERROR",
        "CANCELLED",
    }
)

_running: dict[str, asyncio.Task] = {}


def job_channel(job_id: str) -> str:
    return f"investigation_job:{job_id}"


class InvestigationReporter:
    """Writes investigation progress to DB, then announces it."""

    def __init__(self, job_id: str, dataset_id: str | None) -> None:
        self.job_id = job_id
        self.dataset_id = dataset_id
        self._last_payload: dict[str, Any] = {}

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
        terminal = status in TERMINAL_STATUSES
        attempts = 6 if terminal else 2
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
            except Exception as exc:
                if attempt == attempts - 1:
                    log.warning(
                        "investigation_jobs.progress_write_failed",
                        job_id=self.job_id,
                        stage=stage,
                        status=status,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    break
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            self._last_payload = payload
            self._publish(payload)
            return payload

        provisional = dict(self._last_payload)
        provisional.update(
            {
                k: v
                for k, v in (
                    ("stage", stage),
                    ("progress_pct", progress_pct),
                    ("message", message),
                    ("status", status),
                    ("error", error),
                )
                if v is not None
            }
        )
        provisional.setdefault("id", self.job_id)
        provisional["terminal"] = provisional.get("status") in TERMINAL_STATUSES
        if provisional.get("terminal"):
            provisional["progress_pct"] = 100 if provisional.get("status") == "COMPLETED" else provisional.get("progress_pct", 0)
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
            job = await session.get(InvestigationJob, self.job_id)
            if job is None:
                log.warning("investigation_jobs.row_missing", job_id=self.job_id)
                return {}
            if stage is not None:
                job.stage = stage
            if progress_pct is not None:
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
                if job.status == "COMPLETED":
                    job.progress_pct = 100
            job.updated_at = utcnow()
            steps = list(job.steps or [])
            if stage is not None and (not steps or steps[-1].get("stage") != stage):
                steps.append(
                    {"stage": stage, "message": message, "at": utcnow().isoformat(), "status": status or job.status}
                )
                job.steps = steps[-80:]
            await session.commit()
            return _job_row(job)

    def _publish(self, payload: dict[str, Any]) -> None:
        event = {
            "type": "investigation_progress" if not payload.get("terminal") else "investigation_finished",
            "job_id": self.job_id,
            "dataset_id": self.dataset_id,
            **payload,
        }
        try:
            get_container().event_bus.publish(job_channel(self.job_id), event)
        except Exception:
            log.warning("investigation_jobs.publish_failed", job_id=self.job_id, exc_info=True)


def _job_row(job: InvestigationJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "dataset_id": job.dataset_id,
        "case_id": job.case_id,
        "investigation_id": job.investigation_id,
        "question": job.question,
        "objective": job.objective,
        "status": job.status,
        "stage": job.stage,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "steps": job.steps or [],
        "result": job.result or {},
        "error": job.error,
        "requested_by": job.requested_by,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "terminal": job.status in TERMINAL_STATUSES,
    }


async def start_investigation_job(
    *,
    dataset_id: str | None,
    case_id: str | None,
    investigation_id: str | None,
    question: str,
    objective: str | None,
    requested_by: str | None,
    work: Callable[[InvestigationReporter], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with async_session() as session:
        job = InvestigationJob(
            id=new_uuid(),
            dataset_id=dataset_id,
            case_id=case_id,
            investigation_id=investigation_id,
            question=question,
            objective=objective,
            status="QUEUED",
            stage="QUEUED",
            progress_pct=0,
            message="Queued",
            steps=[],
            result={},
            requested_by=requested_by,
        )
        session.add(job)
        await session.commit()
        row = _job_row(job)

    reporter = InvestigationReporter(row["id"], dataset_id)

    async def runner() -> None:
        try:
            await reporter.update(status="RUNNING", stage="PREPARING", progress_pct=1, message="Preparing investigation")
            result = await work(reporter)
            # work is expected to have already set COMPLETED or failure; if not, mark completed
            if result is not None and isinstance(result, dict) and result.get("status") in TERMINAL_STATUSES:
                # already terminal via reporter
                pass
            else:
                await reporter.update(
                    status="COMPLETED",
                    stage="COMPLETED",
                    progress_pct=100,
                    message="Completed",
                    result=result or {},
                )
            log.info("investigation_jobs.succeeded", job_id=row["id"])
        except asyncio.CancelledError as ce:
            import traceback
            tb = traceback.format_exc()
            log.warning("investigation_jobs.cancelled", job_id=row["id"], traceback=tb)
            try:
                await reporter.update(status="CANCELLED", stage="CANCELLED", message=f"Investigation cancelled: {ce}")
            except Exception:
                pass
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            log.exception("investigation_jobs.failed", job_id=row["id"])
            await reporter.update(status="FAILED", stage="FAILED", message=detail, error=detail)
        finally:
            _running.pop(row["id"], None)

    task = asyncio.create_task(runner(), name=f"investigation-job-{row['id']}")
    _running[row["id"]] = task
    return row


async def get_investigation_job(job_id: str) -> dict[str, Any] | None:
    async with async_session() as session:
        job = await session.get(InvestigationJob, job_id)
        return _job_row(job) if job is not None else None


def is_running(job_id: str) -> bool:
    task = _running.get(job_id)
    return task is not None and not task.done()
