"""In-process job executor (embedded profile).

Implements the same :class:`~app.ports.broker.JobBroker` contract as the Celery
worker pool.  Jobs run on a bounded thread pool so a burst of uploads cannot
exhaust the API process, and failures are handled by exactly the same
retry/quarantine policy the Celery path uses (PRD 9.4).
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.logging import get_logger

log = get_logger("crimelink.broker.inline")


class InlineBroker:
    backend_name = "inline"

    def __init__(
        self,
        handler: Callable[..., None],
        nightly_handler: Callable[..., None] | None = None,
        anchor_handler: Callable[..., None] | None = None,
        max_workers: int = 2,
    ) -> None:
        self._handler = handler
        self._nightly_handler = nightly_handler
        self._anchor_handler = anchor_handler
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="crimelink-job")
        self._pending = 0
        self._lock = threading.Lock()

    def dispatch_document_pipeline(
        self, *, job_id: str, doc_id: str, case_id: str, trace_id: str, user_id: str
    ) -> None:
        with self._lock:
            self._pending += 1

        def _run() -> None:
            try:
                self._handler(
                    job_id=job_id,
                    doc_id=doc_id,
                    case_id=case_id,
                    trace_id=trace_id,
                    user_id=user_id,
                )
            except Exception:
                log.exception("broker.inline.job_crashed", job_id=job_id, doc_id=doc_id)
            finally:
                with self._lock:
                    self._pending -= 1

        self._pool.submit(_run)
        log.info("broker.inline.dispatched", job_id=job_id, doc_id=doc_id, trace_id=trace_id)

    def dispatch_nightly_patterns(self, *, trace_id: str) -> None:
        if self._nightly_handler is None:
            return

        def _run() -> None:
            try:
                self._nightly_handler(trace_id=trace_id)
            except Exception:
                log.exception("broker.inline.nightly_failed", trace_id=trace_id)

        self._pool.submit(_run)

    def dispatch_audit_anchor(self, *, trace_id: str) -> None:
        if self._anchor_handler is None:
            return

        def _run() -> None:
            try:
                self._anchor_handler(trace_id=trace_id)
            except Exception:
                log.exception("broker.inline.anchor_failed", trace_id=trace_id)

        self._pool.submit(_run)

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {"backend": self.backend_name, "pending_jobs": self._pending, "alive": True}


class InProcessEventBus:
    """Async fan-out used by the WebSocket status channel (PRD 10)."""

    backend_name = "inprocess"

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def publish(self, channel: str, message: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._subscribers.get(channel, []))
        for queue in queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover - bounded queues
                pass

    async def subscribe(self, channel: str):
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.setdefault(channel, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subscribers.get(channel, []).remove(queue)
