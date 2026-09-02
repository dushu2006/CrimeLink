"""Celery task definitions (production broker).

The tasks are thin: all logic lives in :mod:`app.pipeline.orchestrator`, so the
inline executor used by the embedded profile and the Celery worker used in
production run exactly the same code.

Retry policy (PRD 9.4) is deliberately *not* delegated to Celery's
``autoretry_for``: the orchestrator distinguishes deterministic failures (fail
and quarantine immediately) from transient ones (retry, then quarantine after
five attempts), and that distinction is a domain decision rather than a
transport concern.
"""

from __future__ import annotations

from app.logging import bind_context, get_logger
from app.pipeline.orchestrator import (
    process_document,
    run_audit_anchor,
    run_nightly_patterns,
)

log = get_logger("crimelink.tasks")

try:
    from app.adapters.broker.celery_app import celery_app
except Exception:  # pragma: no cover - celery is optional in embedded profile
    celery_app = None  # type: ignore


if celery_app is not None:

    @celery_app.task(name="crimelink.pipeline.process_document", bind=True, acks_late=True)
    def process_document_task(
        self, *, job_id: str, doc_id: str, case_id: str, trace_id: str, user_id: str
    ) -> dict:
        bind_context(trace_id=trace_id, case_id=case_id, user_id=user_id)
        process_document(
            job_id=job_id, doc_id=doc_id, case_id=case_id, trace_id=trace_id, user_id=user_id
        )
        return {"job_id": job_id, "doc_id": doc_id, "status": "processed"}

    @celery_app.task(name="crimelink.patterns.nightly")
    def nightly_patterns_task(trace_id: str | None = None) -> dict:
        run_nightly_patterns(trace_id=trace_id)
        return {"status": "ok"}

    @celery_app.task(name="crimelink.audit.anchor")
    def audit_anchor_task(trace_id: str | None = None) -> dict:
        run_audit_anchor(trace_id=trace_id)
        return {"status": "ok"}


if celery_app is not None:  # pragma: no cover - scheduling config only
    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "nightly-pattern-detection": {
            "task": "crimelink.patterns.nightly",
            "schedule": crontab(hour=1, minute=30),
        },
        "nightly-audit-anchor": {
            "task": "crimelink.audit.anchor",
            "schedule": crontab(hour=2, minute=0),
        },
    }
