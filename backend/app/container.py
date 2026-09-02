"""Dependency wiring — the only place that knows about concrete adapters.

Everything above this module depends on the *ports*, not on Neo4j, MinIO, Redis
or Celery.  That is what makes the system deployable on an air-gapped
on-premises cluster and simultaneously runnable on a laptop with zero
containers: only the adapters change, and they change here.

Adapters are constructed lazily so importing the application never attempts a
network connection (important for tests, CLI tools and the Alembic migration
runner).
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger("crimelink.container")


class Container:
    """Process-wide adapter registry."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._graph_store: Any | None = None
        self._object_store: Any | None = None
        self._broker: Any | None = None
        self._event_bus: Any | None = None
        self._nlp: Any | None = None
        self._injector: Any | None = None
        self._resolver: Any | None = None
        self._pattern_engine: Any | None = None
        self._handlers: dict[str, Any] = {}

    # ----------------------------------------------------------------- graph
    @property
    def graph_store(self):
        if self._graph_store is None:
            backend = self.settings.effective_graph_backend
            if backend == "neo4j":
                from app.adapters.graph.neo4j import Neo4jGraphStore

                store = Neo4jGraphStore(self.settings)
                try:
                    store.ensure_constraints()
                except Exception as exc:  # pragma: no cover - infra dependent
                    log.error("container.neo4j_constraints_failed", error=str(exc))
                self._graph_store = store
            else:
                from app.adapters.graph.embedded import EmbeddedGraphStore

                self._graph_store = EmbeddedGraphStore(self.settings)
            log.info("container.graph_ready", backend=self._graph_store.backend_name)
        return self._graph_store

    @property
    def injector(self):
        if self._injector is None:
            from app.adapters.graph.injector import GraphInjector

            self._injector = GraphInjector(self.graph_store)
        return self._injector

    @property
    def resolver(self):
        if self._resolver is None:
            from app.pipeline.entity_resolution import EntityResolver

            self._resolver = EntityResolver(self.graph_store, self.settings)
        return self._resolver

    # --------------------------------------------------------------- objects
    @property
    def object_store(self):
        if self._object_store is None:
            if self.settings.effective_object_store_backend == "minio":
                from app.adapters.objectstore.minio_store import MinioObjectStore

                store = MinioObjectStore(self.settings)
                try:
                    store.ensure_buckets()
                except Exception as exc:  # pragma: no cover - infra dependent
                    log.error("container.minio_buckets_failed", error=str(exc))
                self._object_store = store
            else:
                from app.adapters.objectstore.local import LocalObjectStore

                self._object_store = LocalObjectStore(self.settings)
            log.info("container.object_store_ready", backend=self._object_store.backend_name)
        return self._object_store

    # --------------------------------------------------------------- broker
    def set_handlers(
        self,
        *,
        process_document: Any,
        nightly_patterns: Any = None,
        audit_anchor: Any = None,
    ) -> None:
        self._handlers = {
            "process_document": process_document,
            "nightly_patterns": nightly_patterns,
            "audit_anchor": audit_anchor,
        }

    @property
    def broker(self):
        if self._broker is None:
            if self.settings.effective_broker_backend == "celery":
                from app.adapters.broker.celery_app import CeleryBroker

                self._broker = CeleryBroker()
            else:
                from app.adapters.broker.inline import InlineBroker

                handler = self._handlers.get("process_document")
                if handler is None:  # local import avoids a circular import at startup
                    from app.pipeline.orchestrator import process_document

                    handler = process_document
                self._broker = InlineBroker(
                    handler=handler,
                    nightly_handler=self._handlers.get("nightly_patterns")
                    or _lazy("app.pipeline.orchestrator", "run_nightly_patterns"),
                    anchor_handler=self._handlers.get("audit_anchor")
                    or _lazy("app.pipeline.orchestrator", "run_audit_anchor"),
                )
            log.info("container.broker_ready", backend=self._broker.backend_name)
        return self._broker

    @property
    def event_bus(self):
        if self._event_bus is None:
            if self.settings.effective_broker_backend == "celery":
                try:
                    from app.adapters.events.redis_bus import RedisEventBus

                    self._event_bus = RedisEventBus(self.settings)
                except Exception as exc:  # pragma: no cover - infra dependent
                    log.warning("container.redis_bus_unavailable", error=str(exc))
                    from app.adapters.broker.inline import InProcessEventBus

                    self._event_bus = InProcessEventBus()
            else:
                from app.adapters.broker.inline import InProcessEventBus

                self._event_bus = InProcessEventBus()
        return self._event_bus

    # ------------------------------------------------------------------ nlp
    @property
    def nlp(self):
        if self._nlp is None:
            from app.adapters.nlp.factory import build_nlp_provider

            self._nlp = build_nlp_provider(self.settings)
            log.info("container.nlp_ready", provider=self._nlp.name)
        return self._nlp

    @property
    def pattern_engine(self):
        if self._pattern_engine is None:
            from app.analytics.patterns import PatternEngine

            self._pattern_engine = PatternEngine(self.settings)
        return self._pattern_engine

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        """Drop every cached adapter (used by tests)."""
        self._graph_store = None
        self._object_store = None
        self._broker = None
        self._event_bus = None
        self._nlp = None
        self._injector = None
        self._resolver = None
        self._pattern_engine = None


def _lazy(module: str, attribute: str):
    """Resolve a handler at call time to avoid an import cycle."""
    import importlib

    def _call(**kwargs):
        resolved = getattr(importlib.import_module(module), attribute)
        return resolved(**kwargs)

    return _call


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container


def set_container(container: Container) -> None:
    global _container
    _container = container
