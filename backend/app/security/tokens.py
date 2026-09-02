"""JWT issuance, refresh rotation and reuse detection (PRD 12.1).

Access tokens live 15 minutes; refresh tokens 8 hours.  Refresh tokens are
rotated on every use, and **reusing** a refresh token is treated as theft: the
entire token family is revoked and the event is logged.  Without reuse
detection, a stolen refresh token is indistinguishable from its legitimate
owner, and "we logged the login" is not a security control.

Only a SHA-256 digest of each refresh token is stored, so a database dump does
not hand an attacker usable sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

import jwt
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.base import utcnow
from app.db.models import RefreshToken, User
from app.errors import AuthenticationError, SessionExpiredError

ALGORITHM = "HS256"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_access_token(user: User, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = utcnow()
    payload = {
        "sub": user.id,
        "badge": user.badge_number,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "jurisdiction_id": user.jurisdiction_id,
        "station_id": user.station_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise SessionExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc
    if payload.get("type") != "access":
        raise AuthenticationError("Invalid authentication token.")
    return payload


def issue_refresh_token(
    session, user: User, *, ip_address: str | None = None, family_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, RefreshToken]:
    """Mint a refresh token, creating or extending a rotation family."""
    settings = settings or get_settings()
    raw = secrets.token_urlsafe(48)
    now = utcnow()
    record = RefreshToken(
        user_id=user.id,
        family_id=family_id or secrets.token_hex(16),
        token_hash=hash_token(raw),
        issued_at=now,
        expires_at=now + timedelta(hours=settings.refresh_token_ttl_hours),
        ip_address=ip_address,
    )
    session.add(record)
    session.flush()
    return raw, record


def rotate_refresh_token(
    session, raw: str, *, ip_address: str | None = None, settings: Settings | None = None
) -> tuple[str, RefreshToken, User]:
    """Consume a refresh token and issue its successor.

    Raises :class:`AuthenticationError` if the token is unknown, expired,
    revoked, or **already used** — in which case the whole family is revoked.
    """
    settings = settings or get_settings()
    digest = hash_token(raw)
    record = session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == digest)
    ).scalar_one_or_none()

    if record is None:
        raise AuthenticationError("Invalid refresh token.")
    if record.revoked_at is not None or record.used_at is not None:
        # Reuse detected: revoke every token descended from this family.
        _revoke_family(session, record.family_id)
        session.flush()
        raise AuthenticationError(
            "This refresh token has already been used; the session has been revoked "
            "for security. Please sign in again."
        )
    if record.expires_at < utcnow():
        record.revoked_at = utcnow()
        session.flush()
        raise SessionExpiredError()

    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not active.")

    record.used_at = utcnow()
    new_raw, new_record = issue_refresh_token(
        session, user, ip_address=ip_address, family_id=record.family_id, settings=settings
    )
    session.flush()
    return new_raw, new_record, user


def revoke_family(session, family_id: str) -> int:
    return _revoke_family(session, family_id)


def _revoke_family(session, family_id: str) -> int:
    now = utcnow()
    rows = session.execute(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


def revoke_all_for_user(session, user_id: str) -> int:
    now = utcnow()
    rows = session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


def purge_expired(session) -> int:
    """Soft housekeeping: expired refresh tokens are revoked, never deleted."""
    now = utcnow()
    rows = session.execute(
        select(RefreshToken).where(
            RefreshToken.expires_at < now, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


# --------------------------------------------------------------------------- #
# Async wrappers
# --------------------------------------------------------------------------- #
# The token helpers are written against the synchronous Session because the
# Celery workers and maintenance jobs use it too.  The API hands them an
# AsyncSession, so these wrappers run the same code inside the async session's
# greenlet — one implementation, no drift between the two paths.

async def issue_refresh_token_async(
    session, user: User, *, ip_address: str | None = None,
    family_id: str | None = None, settings: Settings | None = None,
) -> tuple[str, RefreshToken]:
    return await session.run_sync(
        lambda sync: issue_refresh_token(
            sync, user, ip_address=ip_address, family_id=family_id, settings=settings
        )
    )


async def rotate_refresh_token_async(
    session, raw: str, *, ip_address: str | None = None, settings: Settings | None = None
) -> tuple[str, RefreshToken, User]:
    return await session.run_sync(
        lambda sync: rotate_refresh_token(sync, raw, ip_address=ip_address, settings=settings)
    )


async def revoke_family_async(session, family_id: str) -> int:
    return await session.run_sync(lambda sync: revoke_family(sync, family_id))
