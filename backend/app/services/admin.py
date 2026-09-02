"""Administrative services: audit search, users, quarantine, thresholds (PRD 10/12/13).

The audit log doubles as a security sensor: abnormal search volume per user,
repeated denied cross-jurisdiction attempts and out-of-hours bulk exports are all
visible from the same table without extra instrumentation (PRD 13).
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.db.base import new_uuid, utcnow
from app.db.models import AuditLog, PatternConfig, User
from app.domain.enums import AuditAction, Role
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.logging import get_logger
from app.security.deps import Principal
from app.security.passwords import hash_password, validate_password_strength

log = get_logger("crimelink.services.admin")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def audit_search(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    badge_number: str | None = None,
    action: str | None = None,
    case_id: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 200,
) -> list[dict]:
    stmt = select(AuditLog)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if badge_number:
        stmt = stmt.where(AuditLog.badge_number == badge_number)
    if action:
        stmt = stmt.where(AuditLog.action_type == action)
    if case_id:
        stmt = stmt.where(AuditLog.case_id == case_id)
    if from_ts:
        stmt = stmt.where(AuditLog.timestamp >= from_ts)
    if to_ts:
        stmt = stmt.where(AuditLog.timestamp <= to_ts)
    rows = (
        await session.execute(stmt.order_by(AuditLog.id.desc()).limit(limit))
    ).scalars().all()
    return [_audit_row(r) for r in rows]


def _audit_row(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "badge_number": row.badge_number,
        "action_type": row.action_type.value
        if isinstance(row.action_type, AuditAction)
        else str(row.action_type),
        "target_resource": row.target_resource,
        "case_id": row.case_id,
        "jurisdiction_id": row.jurisdiction_id,
        "ip_address": row.ip_address,
        "trace_id": row.trace_id,
        "details": row.details or {},
        "prev_row_hash": row.prev_row_hash,
        "row_hash": row.row_hash,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


async def audit_verify(session: AsyncSession, limit: int | None = None) -> dict:
    return await audit_service.verify_async(session, limit=limit)


async def audit_anomalies(session: AsyncSession, *, days: int = 7) -> dict:
    """Scheduled-security view over the audit trail (PRD 13)."""
    cutoff = utcnow() - timedelta(days=days)
    rows = (
        await session.execute(
            select(AuditLog).where(AuditLog.timestamp >= cutoff)
        )
    ).scalars().all()

    actions = Counter(
        r.action_type.value if isinstance(r.action_type, AuditAction) else str(r.action_type)
        for r in rows
    )
    per_user = Counter(r.badge_number or r.user_id or "unknown" for r in rows)
    failures = int(actions.get(AuditAction.LOGIN_FAILED.value, 0))
    exports = int(actions.get(AuditAction.EXPORT.value, 0))
    denied = int(
        sum(
            1
            for r in rows
            if (r.details or {}).get("denied") or (r.details or {}).get("jurisdiction_denied")
        )
    )
    return {
        "window_days": days,
        "total_events": len(rows),
        "actions": dict(actions),
        "top_users": per_user.most_common(10),
        "failed_logins": failures,
        "exports": exports,
        "access_denials": denied,
        "alerts": [
            message
            for message, condition in (
                (
                    f"{failures} failed sign-in attempts in the last {days} days",
                    failures >= 10,
                ),
                (f"{exports} exports in the last {days} days", exports >= 20),
                (
                    f"{denied} denied access attempts in the last {days} days",
                    denied >= 5,
                ),
            )
            if condition
        ],
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def list_users(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(select(User).order_by(User.badge_number))).scalars().all()
    return [
        {
            "id": u.id,
            "badge_number": u.badge_number,
            "full_name": u.full_name,
            "role": u.role.value if isinstance(u.role, Role) else str(u.role),
            "station_id": u.station_id,
            "jurisdiction_id": u.jurisdiction_id,
            "is_active": u.is_active,
            "locked_until": u.locked_until.isoformat() if u.locked_until else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in rows
    ]


async def create_user(
    session: AsyncSession,
    *,
    principal: Principal,
    badge_number: str,
    full_name: str,
    password: str,
    role: Role,
    station_id: str,
    jurisdiction_id: str,
) -> User:
    if principal.role is not Role.ADMIN:
        from app.errors import PermissionDeniedError

        raise PermissionDeniedError("Only an administrator can create users.")
    existing = (
        await session.execute(select(User).where(User.badge_number == badge_number))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("A user with this badge number already exists.")
    problems = validate_password_strength(password)
    if problems:
        raise ValidationFailedError(" ".join(problems))

    user = User(
        id=new_uuid(),
        badge_number=badge_number,
        full_name=full_name,
        hashed_password=hash_password(password),
        role=role,
        station_id=station_id,
        jurisdiction_id=jurisdiction_id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def set_user_active(session: AsyncSession, user_id: str, active: bool) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    # Soft disable only — no row is ever deleted (PRD 11.2).
    user.is_active = active
    if active:
        user.failed_login_count = 0
        user.locked_until = None
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Pattern thresholds
# ---------------------------------------------------------------------------


DEFAULT_THRESHOLDS: dict[str, float] = {
    "structuring_min_transfers": 4.0,
    "structuring_window_days": 30.0,
    "structuring_max_single_amount": 50_000.0,
    "structuring_min_total_amount": 1_000_000.0,
    "burner_max_lifespan_days": 21.0,
    "burner_min_fanout": 15.0,
    "rapid_movement_min_kmh": 110.0,
    "network_bridge_percentile": 95.0,
}

THRESHOLD_HELP: dict[str, str] = {
    "structuring_min_transfers": "Transfers below the reporting threshold required to flag structuring",
    "structuring_window_days": "Rolling window, in days, within which transfers are counted",
    "structuring_max_single_amount": "Reporting threshold in INR; transfers below this are 'sub-threshold'",
    "structuring_min_total_amount": "Cumulative amount in INR required to flag structuring",
    "burner_max_lifespan_days": "Maximum active lifespan in days for a burner-phone candidate",
    "burner_min_fanout": "Minimum distinct counterparties for a burner-phone candidate",
    "rapid_movement_min_kmh": "Implied transit speed in km/h above which movement is implausible",
    "network_bridge_percentile": "Betweenness percentile required for a network-bridge finding",
}


async def get_thresholds(session: AsyncSession) -> dict[str, float]:
    rows = (await session.execute(select(PatternConfig))).scalars().all()
    values = dict(DEFAULT_THRESHOLDS)
    for row in rows:
        if row.key in values:
            values[row.key] = float(row.value)
    return values


async def set_threshold(
    session: AsyncSession, *, principal: Principal, key: str, value: float
) -> dict:
    if principal.role is not Role.ADMIN:
        from app.errors import PermissionDeniedError

        raise PermissionDeniedError("Only an administrator can change detection thresholds.")
    if key not in DEFAULT_THRESHOLDS:
        raise ValidationFailedError(f"Unknown threshold '{key}'.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationFailedError("Threshold value must be numeric.") from exc
    if numeric <= 0:
        raise ValidationFailedError("Threshold value must be positive.")

    row = (
        await session.execute(select(PatternConfig).where(PatternConfig.key == key))
    ).scalar_one_or_none()
    if row is None:
        row = PatternConfig(id=new_uuid(), key=key, value=numeric)
        session.add(row)
    else:
        row.value = numeric
    row.updated_by = principal.id
    await session.flush()
    return {
        "key": key,
        "value": numeric,
        "description": THRESHOLD_HELP.get(key, ""),
        "updated_by": principal.id,
    }


async def counts(session: AsyncSession) -> dict[str, int]:
    from app.db.models import Case, CaseDocument, DetectedPattern, EntityResolutionItem

    return {
        "users": int((await session.execute(select(func.count(User.id)))).scalar() or 0),
        "cases": int((await session.execute(select(func.count(Case.id)))).scalar() or 0),
        "documents": int(
            (await session.execute(select(func.count(CaseDocument.id)))).scalar() or 0
        ),
        "pending_matches": int(
            (
                await session.execute(
                    select(func.count(EntityResolutionItem.id)).where(
                        EntityResolutionItem.status == "PENDING"
                    )
                )
            ).scalar()
            or 0
        ),
        "new_patterns": int(
            (
                await session.execute(
                    select(func.count(DetectedPattern.id)).where(
                        DetectedPattern.status == "NEW"
                    )
                )
            ).scalar()
            or 0
        ),
    }
