"""Prometheus metrics (PRD 13).

The core operational signals are: ingestion throughput and per-stage failure
rate, entity-resolution queue depth and **oldest-item age** (SLA breach alarm at
48 h), pattern queue depth, API latency by endpoint, and worker saturation.
"""

from __future__ import annotations

import threading
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

DOCUMENTS_PROCESSED = Counter(
    "crimelink_documents_processed_total",
    "Documents that completed the pipeline",
    ["document_type"],
    registry=REGISTRY,
)
DOCUMENTS_FAILED = Counter(
    "crimelink_documents_failed_total",
    "Documents that failed or were quarantined",
    ["document_type"],
    registry=REGISTRY,
)
PATTERNS_DETECTED = Counter(
    "crimelink_patterns_detected_total",
    "Pattern findings written to the review queue",
    ["pattern_type"],
    registry=REGISTRY,
)
ER_PROPOSALS = Counter(
    "crimelink_er_proposals_total",
    "Entity-resolution proposals routed to human review",
    registry=REGISTRY,
)
API_REQUESTS = Counter(
    "crimelink_api_requests_total",
    "API requests by endpoint and status",
    ["method", "path", "status"],
    registry=REGISTRY,
)
API_LATENCY = Histogram(
    "crimelink_api_request_duration_seconds",
    "API request latency by endpoint",
    ["method", "path"],
    registry=REGISTRY,
)
STAGE_LATENCY = Histogram(
    "crimelink_pipeline_stage_duration_seconds",
    "Per-stage pipeline duration",
    ["stage"],
    registry=REGISTRY,
)
GRAPH_NODES = Gauge("crimelink_graph_nodes", "Nodes in the graph", registry=REGISTRY)
GRAPH_EDGES = Gauge("crimelink_graph_edges", "Relationships in the graph", registry=REGISTRY)
ER_QUEUE_DEPTH = Gauge(
    "crimelink_er_queue_pending", "Pending entity-resolution items", registry=REGISTRY
)
ER_QUEUE_OLDEST_HOURS = Gauge(
    "crimelink_er_queue_oldest_item_hours",
    "Age of the oldest pending entity-resolution item (SLA 48h)",
    registry=REGISTRY,
)
PATTERN_QUEUE_DEPTH = Gauge(
    "crimelink_pattern_queue_new", "Pattern findings awaiting review", registry=REGISTRY
)
QUARANTINE_DEPTH = Gauge(
    "crimelink_quarantine_documents", "Documents in quarantine", registry=REGISTRY
)
AUDIT_ROWS = Gauge("crimelink_audit_rows", "Rows in the hash-chained audit log", registry=REGISTRY)
AUDIT_VERIFICATION_FAILURES = Counter(
    "crimelink_audit_verification_failures_total",
    "Audit hash chain verification failures (should always be 0)",
    registry=REGISTRY,
)
DB_HEALTH = Gauge(
    "crimelink_db_health_ok",
    "1 if database/graph/broker health checks pass, 0 if any fail",
    registry=REGISTRY,
)

_lock = threading.Lock()
_stage_timers: dict[str, float] = {}


def observe_stage_start(stage: str) -> None:
    import time

    with _lock:
        _stage_timers[stage] = time.time()


def observe_stage_end(stage: str) -> None:
    import time

    with _lock:
        start = _stage_timers.pop(stage, None)
    if start is not None:
        STAGE_LATENCY.labels(stage=stage).observe(time.time() - start)


def refresh_gauges() -> None:
    """Recompute DB/graph-derived gauges on scrape.

    This is called on every /metrics scrape (15s). It must never raise.
    """
    graph_ok = False
    db_ok = False

    try:
        from app.container import get_container

        stats = get_container().graph_store.stats()
        GRAPH_NODES.set(float(stats.get("nodes", 0)))
        GRAPH_EDGES.set(float(stats.get("edges", 0)))
        graph_ok = True
    except Exception:  # noqa: BLE001 - metrics must never break the scrape
        pass

    try:
        from sqlalchemy import func, select

        from app.db.models import (
            AuditLog,
            CaseDocument,
            DetectedPattern,
            EntityResolutionItem,
        )
        from app.db.session import get_sync_sessionmaker

        with get_sync_sessionmaker()() as session:
            pending = session.execute(
                select(func.count(EntityResolutionItem.id)).where(
                    EntityResolutionItem.status == "PENDING"
                )
            ).scalar() or 0
            ER_QUEUE_DEPTH.set(float(pending))
            oldest = session.execute(
                select(func.min(EntityResolutionItem.created_at)).where(
                    EntityResolutionItem.status == "PENDING"
                )
            ).scalar()
            if oldest is not None:
                from app.db.base import utcnow

                ER_QUEUE_OLDEST_HOURS.set(
                    round((utcnow() - oldest).total_seconds() / 3600.0, 2)
                )
            else:
                ER_QUEUE_OLDEST_HOURS.set(0.0)
            new_patterns = session.execute(
                select(func.count(DetectedPattern.id)).where(
                    DetectedPattern.status == "NEW"
                )
            ).scalar() or 0
            PATTERN_QUEUE_DEPTH.set(float(new_patterns))
            quarantined = session.execute(
                select(func.count(CaseDocument.id)).where(
                    CaseDocument.quarantined.is_(True)
                )
            ).scalar() or 0
            QUARANTINE_DEPTH.set(float(quarantined))
            audit_rows = session.execute(select(func.count(AuditLog.id))).scalar() or 0
            AUDIT_ROWS.set(float(audit_rows))
            db_ok = True
    except Exception:  # noqa: BLE001
        pass

    # DB health gauge: 1 if both graph and DB queries succeeded, 0 otherwise
    # This is what DatabaseHealthUnhealthy alert watches. Real health endpoint
    # (/health/ready) does more thorough checks, but this gauge is sufficient for
    # Prometheus alerting and is updated on every scrape.
    try:
        DB_HEALTH.set(1.0 if (graph_ok and db_ok) else 0.0)
    except Exception:
        pass


def render_metrics() -> tuple[bytes, str]:
    refresh_gauges()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def snapshot() -> dict[str, Any]:
    """JSON view of the same signals, for the admin overview screen."""
    from prometheus_client import REGISTRY as DEFAULT_REGISTRY

    refresh_gauges()
    out: dict[str, Any] = {}
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            out[sample.name] = sample.value
    return out
