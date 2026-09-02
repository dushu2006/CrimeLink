"""FastAPI dependency chain: authentication, RBAC, jurisdiction scoping, audit.

Every guarantee that has to hold on *every* request is expressed here as a
dependency, so it is enforced by the framework rather than remembered by a
developer (PRD 10, "API-level invariants"):

1. a valid JWT is required on every endpoint except login/refresh/health;
2. every case-scoped query passes through :class:`JurisdictionScope`, which
   injects the allowed-jurisdiction filter **into the query itself** — scoping is
   never something the UI hides;
3. every mutating endpoint is wrapped by :func:`audited`, which writes the
   hash-chained audit row inside the same database transaction as the mutation,
   so an action and its audit record cannot diverge.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.db.models import Case, JurisdictionAccessRequest, User
from app.db.session import get_db_session
from app.domain.enums import AccessRequestStatus, AuditAction, Role
from app.errors import AuthenticationError, JurisdictionDeniedError, PermissionDeniedError
from app.logging import get_logger
from app.security.rate_limit import enforce_rate_limit
from app.security.tokens import decode_access_token

log = get_logger("crimelink.security")

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


class Principal:
    """The authenticated caller."""

    def __init__(self, user: User, ip_address: str | None = None) -> None:
        self.user = user
        self.id = user.id
        self.badge_number = user.badge_number
        self.full_name = user.full_name
        self.role = Role(user.role.value if hasattr(user.role, "value") else user.role)
        self.jurisdiction_id = user.jurisdiction_id
        self.station_id = user.station_id
        self.ip_address = ip_address

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    def require(self, *roles: Role | str) -> None:
        """Enforce a role, accepting either ``Role`` members or their values."""
        allowed = {role.value if isinstance(role, Role) else str(role) for role in roles}
        if not allowed:
            return
        if self.role.value not in allowed:
            raise PermissionDeniedError(
                "This action requires the " f"{' or '.join(sorted(allowed))} role."
            )


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> Principal:
    """Resolve and validate the bearer token on every protected endpoint."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication credentials were not provided.")
    payload = decode_access_token(credentials.credentials)
    user = await session.get(User, str(payload.get("sub")))
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not active.")
    ip = request.client.host if request.client else None
    enforce_rate_limit(request, identity=user.id)
    return Principal(user, ip_address=ip)


def require_roles(*roles: Role | str):
    """Dependency factory enforcing a minimum role.

    Roles may be given as :class:`Role` members or as their string values, so a
    route declaration reads naturally without importing the enum everywhere.
    """
    allowed = tuple(role.value if isinstance(role, Role) else str(role) for role in roles)

    async def _dependency(principal: Principal = Depends(get_principal)) -> Principal:
        principal.require(*allowed)
        return principal

    return _dependency


# ---------------------------------------------------------------------------
# Jurisdiction scoping
# ---------------------------------------------------------------------------


class JurisdictionScope:
    """Allowed-jurisdiction filter injected into queries, not into the UI.

    A Kota station officer does not see a Jaipur case unless an administrator of
    the target jurisdiction approved a time-boxed request.  Expired grants stop
    working automatically; there are no permanent cross-jurisdiction grants.
    """

    def __init__(
        self,
        principal: Principal,
        granted_jurisdictions: set[str],
        granted_case_ids: set[str],
    ) -> None:
        self.principal = principal
        self.granted_jurisdictions = granted_jurisdictions
        self.granted_case_ids = granted_case_ids

    @property
    def allowed_jurisdictions(self) -> set[str]:
        return {self.principal.jurisdiction_id, *self.granted_jurisdictions}

    def assert_jurisdiction(self, jurisdiction_id: str) -> None:
        if jurisdiction_id not in self.allowed_jurisdictions:
            raise JurisdictionDeniedError()

    def assert_case(self, case: Case | None) -> Case:
        """Raise ``JurisdictionDeniedError`` (surfaced as 404) if out of scope."""
        if case is None:
            raise JurisdictionDeniedError()
        jurisdiction = (
            case.jurisdiction_id.value
            if hasattr(case.jurisdiction_id, "value")
            else str(case.jurisdiction_id)
        )
        if jurisdiction in self.allowed_jurisdictions:
            return case
        if case.id in self.granted_case_ids:
            return case
        raise JurisdictionDeniedError()

    def case_filter(self):
        """SQL expression restricting a case query to the caller's scope."""
        jurisdiction = Case.jurisdiction_id
        return or_(
            jurisdiction.in_(sorted(self.allowed_jurisdictions)),
            Case.id.in_(sorted(self.granted_case_ids)) if self.granted_case_ids else False,  # noqa: E712
        )


async def get_scope(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> JurisdictionScope:
    """Load active cross-jurisdiction grants and expire stale ones."""
    from datetime import datetime

    from app.db.base import utcnow

    now = utcnow()
    rows = (
        await session.execute(
            select(JurisdictionAccessRequest).where(
                JurisdictionAccessRequest.requester_id == principal.id,
                JurisdictionAccessRequest.status == AccessRequestStatus.APPROVED,
                JurisdictionAccessRequest.expires_at.is_not(None),
                JurisdictionAccessRequest.expires_at > now,
            )
        )
    ).scalars().all()

    stale = (
        await session.execute(
            select(JurisdictionAccessRequest).where(
                and_(
                    JurisdictionAccessRequest.requester_id == principal.id,
                    JurisdictionAccessRequest.status == AccessRequestStatus.APPROVED,
                    JurisdictionAccessRequest.expires_at.is_not(None),
                    JurisdictionAccessRequest.expires_at <= now,
                )
            )
        )
    ).scalars().all()
    for row in stale:
        row.status = AccessRequestStatus.EXPIRED
        row.decided_at = row.decided_at or now

    return JurisdictionScope(
        principal=principal,
        granted_jurisdictions={row.target_jurisdiction for row in rows},
        granted_case_ids={row.case_id for row in rows if row.case_id},
    )


# ---------------------------------------------------------------------------
# Audit recording
# ---------------------------------------------------------------------------


class AuditRecorder:
    """Request-scoped writer that shares the endpoint's database session.

    Because the audit row is appended through the same session that performs the
    mutation, the two commit together or not at all.
    """

    def __init__(
        self, session: AsyncSession, principal: Principal | None, request: Request
    ) -> None:
        self.session = session
        self.principal = principal
        self.request = request
        self.entries: list[dict[str, Any]] = []

    def record(
        self,
        action_type: AuditAction,
        *,
        target_resource: str | None = None,
        case_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        from app.logging import get_trace_id

        self.entries.append(
            {
                "action_type": action_type,
                "target_resource": target_resource,
                "case_id": case_id,
                "details": details or {},
                "trace_id": get_trace_id(),
            }
        )

    async def flush(self) -> None:
        """Append every recorded entry to the hash-chained log."""
        for entry in self.entries:
            await audit_service.append_async(
                self.session,
                action_type=entry["action_type"],
                user_id=self.principal.id if self.principal else None,
                badge_number=self.principal.badge_number if self.principal else None,
                target_resource=entry["target_resource"],
                case_id=entry["case_id"],
                jurisdiction_id=self.principal.jurisdiction_id if self.principal else None,
                ip_address=_client_ip(self.request),
                trace_id=entry["trace_id"],
                details=entry["details"],
            )
        self.entries.clear()


async def get_audit_recorder(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> AuditRecorder:
    return AuditRecorder(session, principal, request)


def _resolved_signature(func: Callable) -> inspect.Signature:
    """Resolve a function's postponed annotations against *its own* module.

    ``functools.wraps`` copies ``__annotations__`` but not ``__globals__``, so a
    wrapper defined in this module cannot resolve annotations (``UploadFile``,
    Pydantic models …) that live in the endpoint's module.  FastAPI would then
    see unresolvable forward references and reject the route.  Resolving eagerly
    and pinning the signature keeps decorated endpoints indistinguishable from
    undecorated ones.
    """
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # pragma: no cover - fall back to raw annotations
        hints = {}
    signature = inspect.signature(func)
    parameters = [
        parameter.replace(annotation=hints.get(name, parameter.annotation))
        for name, parameter in signature.parameters.items()
    ]
    return signature.replace(
        parameters=parameters,
        return_annotation=hints.get("return", signature.return_annotation),
    )


def audited(
    action_type: AuditAction,
    *,
    target: Callable[..., str | None] | None = None,
    case_id: Callable[..., str | None] | None = None,
    details: Callable[..., dict] | None = None,
):
    """Decorator that writes the audit row with the mutation, in one transaction."""

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            recorder: AuditRecorder | None = kwargs.get("recorder")
            if recorder is None:
                raise RuntimeError(
                    f"{func.__name__} is marked @audited but does not declare a "
                    "'recorder' dependency."
                )
            recorder.record(
                action_type,
                target_resource=target(result, **kwargs) if target else None,
                case_id=case_id(result, **kwargs) if case_id else None,
                details=details(result, **kwargs) if details else None,
            )
            await recorder.flush()
            return result

        wrapper.__signature__ = _resolved_signature(func)  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


__all__ = [
    "AuditRecorder",
    "JurisdictionScope",
    "Principal",
    "audited",
    "get_audit_recorder",
    "get_principal",
    "get_scope",
    "require_roles",
]
