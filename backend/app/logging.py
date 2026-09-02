"""Structured JSON logging with trace-ID propagation.

A ``trace_id`` is minted at the API boundary and propagated through the job
broker into every pipeline stage, so one query reconstructs the complete
processing history of a document (PRD 13).
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from typing import Any

import structlog

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "crimelink_trace_id", default=None
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "crimelink_user_id", default=None
)
case_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "crimelink_case_id", default=None
)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str:
    """Return the current trace id, minting one if absent."""
    value = trace_id_var.get()
    if value is None:
        value = new_trace_id()
        trace_id_var.set(value)
    return value


def set_trace_id(value: str | None) -> None:
    trace_id_var.set(value)


def bind_context(**kwargs: Any) -> None:
    for key, value in kwargs.items():
        if key == "trace_id":
            trace_id_var.set(value)
        elif key == "user_id":
            user_id_var.set(value)
        elif key == "case_id":
            case_id_var.set(value)


def _inject_context(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    trace_id = trace_id_var.get()
    if trace_id:
        event_dict.setdefault("trace_id", trace_id)
    user_id = user_id_var.get()
    if user_id:
        event_dict.setdefault("user_id", user_id)
    case_id = case_id_var.get()
    if case_id:
        event_dict.setdefault("case_id", case_id)
    event_dict.setdefault("service", "crimelink")
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog + stdlib logging for the whole process."""
    level_name = (level or "INFO").upper()
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level_name)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level_name)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "neo4j", "httpx"):
        logging.getLogger(noisy).handlers = [handler]
        logging.getLogger(noisy).propagate = True


def get_logger(name: str = "crimelink") -> Any:
    return structlog.get_logger(name)
