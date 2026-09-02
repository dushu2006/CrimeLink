"""Uniform error contract.

Every error returned by the API has the shape::

    {"error": {"code": "...", "message": "...", "trace_id": "..."}}

Internal identifiers, raw Cypher and stack traces are never leaked to clients
(PRD 12.6) — only a ``trace_id`` that correlates with server logs.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger, get_trace_id

log = get_logger("crimelink.errors")

GENERIC_DETAIL = "Request could not be completed."


class CrimeLinkError(Exception):
    """Base class for all domain errors."""

    code = "internal_error"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    public_message = GENERIC_DETAIL

    def __init__(self, message: str | None = None, **context: Any) -> None:
        self.detail = message or self.public_message
        self.context = context
        super().__init__(self.detail)

    def to_payload(self, trace_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.public_message if self.http_status >= 500 else self.detail,
            "trace_id": trace_id,
        }
        return {"error": payload}


class NotFoundError(CrimeLinkError):
    code = "not_found"
    http_status = status.HTTP_404_NOT_FOUND
    public_message = "Resource not found."


class AuthenticationError(CrimeLinkError):
    code = "authentication_failed"
    http_status = status.HTTP_401_UNAUTHORIZED
    public_message = "Invalid credentials."


class SessionExpiredError(CrimeLinkError):
    code = "session_expired"
    http_status = status.HTTP_401_UNAUTHORIZED
    public_message = "Session expired. Please sign in again."


class AccountLockedError(CrimeLinkError):
    code = "account_locked"
    http_status = status.HTTP_423_LOCKED
    public_message = (
        "Account temporarily locked after repeated failed sign-in attempts."
    )


class PermissionDeniedError(CrimeLinkError):
    code = "permission_denied"
    http_status = status.HTTP_403_FORBIDDEN
    public_message = "You do not have permission to perform this action."


class JurisdictionDeniedError(CrimeLinkError):
    """Raised when a query escapes the caller's jurisdiction scope.

    Deliberately identical to ``NotFoundError`` from the client's point of view:
    a caller probing another jurisdiction learns nothing about what exists
    there (PRD 12.4).
    """

    code = "not_found"
    http_status = status.HTTP_404_NOT_FOUND
    public_message = "Resource not found."


class ValidationFailedError(CrimeLinkError):
    code = "validation_failed"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    public_message = "The submitted data is invalid."


class ConflictError(CrimeLinkError):
    code = "conflict"
    http_status = status.HTTP_409_CONFLICT
    public_message = "The resource already exists."


class RateLimitError(CrimeLinkError):
    code = "rate_limited"
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    public_message = "Too many requests. Please slow down."


class PipelineError(CrimeLinkError):
    """Raised by pipeline stages for deterministic (non-retryable) failures."""

    code = "pipeline_failed"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    public_message = "Document processing failed."


class TransientPipelineError(PipelineError):
    """Retryable failure (network blip, lock contention, broker hiccup)."""

    code = "pipeline_transient"


class UnevidencedGraphWriteError(CrimeLinkError):
    """Guarantee G1 was about to be violated (PRD 18, checklist item #1)."""

    code = "unevidenced_graph_write"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    public_message = "Internal error: graph write rejected."


class DependencyUnavailableError(CrimeLinkError):
    code = "dependency_unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    public_message = "A required service is temporarily unavailable."


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers that normalise every error to the CrimeLink contract."""

    @app.exception_handler(CrimeLinkError)
    async def _handle_crimelink_error(request: Request, exc: CrimeLinkError) -> JSONResponse:
        trace_id = get_trace_id()
        log.warning(
            "request.error",
            code=exc.code,
            status=exc.http_status,
            path=request.url.path,
            detail=exc.detail,
            context=exc.context,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_payload(trace_id),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trace_id = get_trace_id()
        fields = [
            {"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "The submitted data is invalid.",
                    "trace_id": trace_id,
                    "fields": fields,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        trace_id = get_trace_id()
        message = exc.detail if exc.status_code < 500 else GENERIC_DETAIL
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": message,
                    "trace_id": trace_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        trace_id = get_trace_id()
        log.exception("request.unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": GENERIC_DETAIL,
                    "trace_id": trace_id,
                }
            },
        )
