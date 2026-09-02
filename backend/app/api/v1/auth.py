"""Authentication endpoints (PRD 10 / 12.1).

* ``POST /auth/login``    — badge number + password → access + refresh token
* ``POST /auth/refresh``  — refresh-token rotation with reuse detection
* ``POST /auth/logout``   — revoke the caller's session family
* ``GET  /auth/me``       — the caller's identity and effective permissions

Account lockout after five failed attempts (30 minutes) is enforced here, and
both successful and failed sign-ins are written to the audit trail.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.config import get_settings
from app.db.base import utcnow
from app.db.models import User
from app.db.session import get_db_session
from app.domain.enums import AuditAction, Role
from app.errors import AccountLockedError, AuthenticationError
from app.logging import get_trace_id
from app.security.deps import AuditRecorder, Principal, get_audit_recorder, get_principal
from app.security.passwords import verify_password
from app.security.rate_limit import enforce_rate_limit
from app.security.tokens import (
    create_access_token,
    issue_refresh_token_async,
    revoke_family_async,
    rotate_refresh_token_async,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    badge_number: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    badge_number: str
    full_name: str
    jurisdiction_id: str
    station_id: str


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    settings = get_settings()
    enforce_rate_limit(request, identity=payload.badge_number, auth=True)

    user = (
        await session.execute(
            select(User).where(User.badge_number == payload.badge_number.strip())
        )
    ).scalar_one_or_none()

    if user is None:
        await audit_service.append_async(
            session,
            action_type=AuditAction.LOGIN_FAILED,
            badge_number=payload.badge_number,
            ip_address=_client_ip(request),
            trace_id=get_trace_id(),
            details={"reason": "unknown_badge"},
        )
        # Commit before raising: the request-scoped session is rolled back on an
        # error response, and a failed sign-in that leaves no trace is exactly
        # the audit gap G3 forbids.
        await session.commit()
        raise AuthenticationError("Invalid badge number or password.")

    if user.locked_until and user.locked_until > utcnow():
        raise AccountLockedError()

    if not user.is_active or not verify_password(payload.password, user.hashed_password):
        user.failed_login_count = int(user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.login_lockout_threshold:
            user.locked_until = utcnow() + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
        await audit_service.append_async(
            session,
            action_type=AuditAction.LOGIN_FAILED,
            user_id=user.id,
            badge_number=user.badge_number,
            ip_address=_client_ip(request),
            trace_id=get_trace_id(),
            details={
                "reason": "bad_password" if user.is_active else "account_disabled",
                "locked": bool(user.locked_until and user.locked_until > utcnow()),
            },
        )
        # Persist the counter (and therefore the lockout) with the audit row.
        await session.commit()
        if user.locked_until and user.locked_until > utcnow():
            raise AccountLockedError()
        raise AuthenticationError("Invalid badge number or password.")

    user.failed_login_count = 0
    user.locked_until = None
    raw_refresh, record = await issue_refresh_token_async(
        session, user, ip_address=_client_ip(request), settings=settings
    )
    await audit_service.append_async(
        session,
        action_type=AuditAction.LOGIN,
        user_id=user.id,
        badge_number=user.badge_number,
        jurisdiction_id=user.jurisdiction_id,
        ip_address=_client_ip(request),
        trace_id=get_trace_id(),
        details={"session_family": record.family_id},
    )
    return TokenResponse(
        access_token=create_access_token(user, settings),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        role=user.role.value if isinstance(user.role, Role) else str(user.role),
        badge_number=user.badge_number,
        full_name=user.full_name,
        jurisdiction_id=user.jurisdiction_id,
        station_id=user.station_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    settings = get_settings()
    enforce_rate_limit(request, identity="refresh", auth=True)
    try:
        new_raw, _record, user = await rotate_refresh_token_async(
            session, payload.refresh_token, ip_address=_client_ip(request), settings=settings
        )
    except AuthenticationError:
        # Reuse or unknown token: recorded here so the security event is visible
        # even though the caller's identity cannot be established.
        await audit_service.append_async(
            session,
            action_type=AuditAction.LOGIN_FAILED,
            ip_address=_client_ip(request),
            trace_id=get_trace_id(),
            details={"reason": "refresh_reuse_or_invalid"},
        )
        # Commit before raising, or the rollback would undo the family
        # revocation that reuse detection just performed — the stolen token
        # would keep working.
        await session.commit()
        raise
    return TokenResponse(
        access_token=create_access_token(user, settings),
        refresh_token=new_raw,
        expires_in=settings.access_token_ttl_minutes * 60,
        role=user.role.value if isinstance(user.role, Role) else str(user.role),
        badge_number=user.badge_number,
        full_name=user.full_name,
        jurisdiction_id=user.jurisdiction_id,
        station_id=user.station_id,
    )


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=10)


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Revoke the caller's session family (rotation makes this complete)."""
    from app.security.tokens import hash_token
    from app.db.models import RefreshToken as _RT

    row = (
        await session.execute(
            select(_RT).where(_RT.token_hash == hash_token(payload.refresh_token))
        )
    ).scalar_one_or_none()
    if row is not None and row.user_id == principal.id:
        revoked = await revoke_family_async(session, row.family_id)
        return {"status": "logged_out", "sessions_revoked": revoked}
    return {"status": "logged_out", "sessions_revoked": 0}


@router.get("/me")
async def me(
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    return {
        "id": principal.id,
        "badge_number": principal.badge_number,
        "full_name": principal.full_name,
        "role": principal.role.value,
        "jurisdiction_id": principal.jurisdiction_id,
        "station_id": principal.station_id,
        "permissions": {
            "can_upload": principal.has_role(Role.INVESTIGATOR, Role.ADMIN),
            "can_review": principal.has_role(Role.INVESTIGATOR, Role.ADMIN),
            "can_export": principal.has_role(Role.INVESTIGATOR, Role.ADMIN),
            "can_administer": principal.has_role(Role.ADMIN),
        },
    }
