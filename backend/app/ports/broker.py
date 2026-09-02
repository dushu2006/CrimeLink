"""Job execution port.

Heavy work (parsing, extraction, NLP, injection, pattern detection) never runs
inside the request/response cycle (PRD principle P2).  The broker decides
*where* it runs:

``celery``  — Redis + horizontally scalable Celery workers (production).
``inline``  — a bounded in-process worker pool (embedded profile, tests, demos).

Both execute the identical orchestrator, so behaviour cannot diverge between a
laptop and an on-premises cluster.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JobBroker(Protocol):
    backend_name: str

    def dispatch_document_pipeline(
        self,
        *,
        job_id: str,
        doc_id: str,
        case_id: str,
        trace_id: str,
        user_id: str,
    ) -> None:
        """Enqueue the six-stage pipeline for one document."""

    def dispatch_nightly_patterns(self, *, trace_id: str) -> None:
        """Enqueue the scheduled whole-graph pattern pass."""

    def dispatch_audit_anchor(self, *, trace_id: str) -> None:
        """Enqueue the nightly audit-chain anchor write."""

    def health(self) -> dict[str, Any]: ...


@runtime_checkable
class EventBus(Protocol):
    """Fan-out of live pipeline progress to WebSocket subscribers (PRD 10)."""

    backend_name: str

    def publish(self, channel: str, message: dict[str, Any]) -> None: ...

    async def subscribe(self, channel: str):  # -> AsyncIterator[dict]
        """Async generator yielding published messages."""
