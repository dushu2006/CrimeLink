"""Celery application and the broker adapter used in production.

Task policy (PRD 9.4):

* transient failures (DB connection, network, lock contention) → automatic retry
  with exponential backoff, maximum 5 attempts;
* deterministic bad input (malformed CSV, encrypted PDF) → immediate ``FAILED``
  plus quarantine with a human-readable reason — retrying cannot help;
* worker crash mid-batch → the task is re-delivered, and the deterministic
  provenance keys guarantee the re-run converges instead of duplicating.

``acks_late`` + ``task_reject_on_worker_lost`` make a crash re-deliver the
message rather than lose it.
"""

from __future__ import annotations

from typing import Any

from celery import Celery

from app.config import get_settings
from app.logging import get_logger

log = get_logger("crimelink.broker.celery")

settings = get_settings()

celery_app = Celery(
    "crimelink",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.pipeline.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,      # one heavy document at a time per worker
    task_default_retry_delay=5,
    task_max_retries=5,
    result_expires=86_400,
    broker_transport_options={"visibility_timeout": 3600},
)

celery_app.conf.task_routes = {
    "crimelink.pipeline.process_document": {"queue": "pipeline"},
    "crimelink.patterns.nightly": {"queue": "analytics"},
    "crimelink.audit.anchor": {"queue": "maintenance"},
}


class CeleryBroker:
    backend_name = "celery"

    def dispatch_document_pipeline(
        self, *, job_id: str, doc_id: str, case_id: str, trace_id: str, user_id: str
    ) -> None:
        from app.pipeline.tasks import process_document_task  # local import avoids a cycle

        process_document_task.delay(
            job_id=job_id, doc_id=doc_id, case_id=case_id, trace_id=trace_id, user_id=user_id
        )
        log.info("broker.celery.dispatched", job_id=job_id, doc_id=doc_id, trace_id=trace_id)

    def dispatch_nightly_patterns(self, *, trace_id: str) -> None:
        from app.pipeline.tasks import nightly_patterns_task

        nightly_patterns_task.delay(trace_id=trace_id)

    def dispatch_audit_anchor(self, *, trace_id: str) -> None:
        from app.pipeline.tasks import audit_anchor_task

        audit_anchor_task.delay(trace_id=trace_id)

    def health(self) -> dict[str, Any]:
        try:
            inspection = celery_app.control.inspect(timeout=2.0)
            stats = inspection.stats() or {}
            return {"backend": self.backend_name, "workers": len(stats), "alive": bool(stats)}
        except Exception as exc:  # pragma: no cover - broker down
            return {"backend": self.backend_name, "workers": 0, "alive": False, "error": str(exc)}
