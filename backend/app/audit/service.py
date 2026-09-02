"""Append-only, hash-chained audit trail (PRD 12.2).

The honest "blockchain" answer
------------------------------
The problem statement sits under *Blockchain & Cybersecurity*.  What law
enforcement actually needs from a ledger is **tamper-evidence**, not a
distributed consensus protocol.  CrimeLink implements that property directly:

* every row stores ``row_hash = SHA256(prev_row_hash || canonical_json(row))``;
  editing any historical row invalidates every hash after it, detectable by one
  linear verification pass;
* the chain head is additionally written nightly to a **separately
  credentialed** object-store bucket, so tampering requires compromising two
  independent stores simultaneously;
* the database itself refuses ``UPDATE``/``DELETE`` on the table in production.

If NCRB later mandates a real distributed ledger for cross-agency anchoring,
the anchor bucket is the single integration point — the hashes are already the
primitive.  That is the correct seam, and it costs nothing today.

Concurrency
-----------
The single-row ``audit_chain_head`` table is updated inside the same transaction
as the insert.  Updating it takes a row lock (PostgreSQL) or the write lock
(SQLite), which serialises appends and makes it impossible for two writers to
fork the chain.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import AuditChainHead, AuditLog
from app.domain.enums import AuditAction
from app.domain.provenance import GENESIS_HASH, canonical_json, chain_hash
from app.logging import get_logger

log = get_logger("crimelink.audit")


def _row_payload(
    *,
    user_id: str | None,
    badge_number: str | None,
    action_type: str,
    target_resource: str | None,
    case_id: str | None,
    jurisdiction_id: str | None,
    ip_address: str | None,
    trace_id: str | None,
    details: dict | None,
    timestamp: Any,
) -> dict[str, Any]:
    """Canonical, hash-stable representation of an audit row.

    Only semantically meaningful fields are hashed — the surrogate ``id`` and the
    two hash columns are excluded, so verification is independent of them.
    """
    return {
        "user_id": user_id,
        "badge_number": badge_number,
        "action_type": action_type,
        "target_resource": target_resource,
        "case_id": case_id,
        "jurisdiction_id": jurisdiction_id,
        "ip_address": ip_address,
        "trace_id": trace_id,
        "details": details or {},
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
    }


class AuditService:
    """Synchronous (worker) and asynchronous (API) audit append + verification."""

    # ---------------------------------------------------------------- append
    def append(
        self,
        session: Session,
        *,
        action_type: AuditAction | str,
        user_id: str | None = None,
        badge_number: str | None = None,
        target_resource: str | None = None,
        case_id: str | None = None,
        jurisdiction_id: str | None = None,
        ip_address: str | None = None,
        trace_id: str | None = None,
        details: dict | None = None,
    ) -> AuditLog:
        """Append one row to the chain.  There is no update and no delete."""
        action = (
            action_type.value if isinstance(action_type, AuditAction) else str(action_type)
        )
        head = session.get(AuditChainHead, 1)
        if head is None:
            head = AuditChainHead(id=1, last_id=0, last_hash=GENESIS_HASH)
            session.add(head)
            session.flush()

        timestamp = utcnow()
        row = AuditLog(
            user_id=user_id,
            badge_number=badge_number,
            action_type=action,
            target_resource=target_resource,
            case_id=case_id,
            jurisdiction_id=jurisdiction_id,
            ip_address=ip_address,
            trace_id=trace_id,
            details=details or {},
            prev_row_hash=head.last_hash,
            row_hash="",
            timestamp=timestamp,
        )
        session.add(row)
        session.flush()  # assign the surrogate id before hashing

        payload = _row_payload(
            user_id=user_id,
            badge_number=badge_number,
            action_type=action,
            target_resource=target_resource,
            case_id=case_id,
            jurisdiction_id=jurisdiction_id,
            ip_address=ip_address,
            trace_id=trace_id,
            details=details,
            timestamp=timestamp,
        )
        row.row_hash = chain_hash(head.last_hash, canonical_json(payload))
        head.last_id = row.id
        head.last_hash = row.row_hash
        session.flush()
        log.debug(
            "audit.append",
            action=action,
            audit_id=row.id,
            prev=head.last_hash[:12],
        )
        return row

    # -------------------------------------------------------------- async IO
    async def append_async(self, session, **kwargs) -> AuditLog:
        """Append from an async endpoint, sharing its session and transaction.

        The chain itself is computed synchronously (it is pure hashing over the
        previous head), so it runs inside the async session's greenlet with
        ``run_sync``.  Going through the endpoint's own session is what keeps the
        audit row and the mutation in one transaction: either both land or
        neither does, and the chain can never skip a row.
        """
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(session, AsyncSession):
            return self.append(session, **kwargs)
        return await session.run_sync(lambda sync_session: self.append(sync_session, **kwargs))

    @staticmethod
    async def head_hash(session) -> str:
        result = await session.execute(select(AuditChainHead).where(AuditChainHead.id == 1))
        head = result.scalar_one_or_none()
        return head.last_hash if head else GENESIS_HASH

    @staticmethod
    async def count(session) -> int:
        result = await session.execute(select(func.count(AuditLog.id)))
        return int(result.scalar() or 0)

    # --------------------------------------------------------------- verify
    @staticmethod
    def verify(session: Session, limit: int | None = None) -> dict[str, Any]:
        """Recompute the whole chain and report the first divergence, if any.

        A single modified, deleted or reordered row causes every subsequent hash
        to mismatch, so this one pass detects any tampering with history.
        """
        query = select(AuditLog).order_by(AuditLog.id.asc())
        if limit:
            query = query.limit(limit)
        rows: Iterable[AuditLog] = session.execute(query).scalars().all()

        expected_prev = GENESIS_HASH
        checked = 0
        first_bad_id: int | None = None
        head_hash = GENESIS_HASH
        for row in rows:
            payload = _row_payload(
                user_id=row.user_id,
                badge_number=row.badge_number,
                action_type=row.action_type.value
                if isinstance(row.action_type, AuditAction)
                else str(row.action_type),
                target_resource=row.target_resource,
                case_id=row.case_id,
                jurisdiction_id=row.jurisdiction_id,
                ip_address=row.ip_address,
                trace_id=row.trace_id,
                details=row.details,
                timestamp=row.timestamp,
            )
            computed = chain_hash(expected_prev, canonical_json(payload))
            if row.prev_row_hash != expected_prev or row.row_hash != computed:
                first_bad_id = first_bad_id if first_bad_id is not None else row.id
            expected_prev = row.row_hash
            head_hash = row.row_hash
            checked += 1

        valid = first_bad_id is None
        if not valid:
            log.error("audit.verification_failed", first_bad_id=first_bad_id, checked=checked)
        return {
            "valid": valid,
            "checked": checked,
            "first_tampered_id": first_bad_id,
            "head_hash": head_hash,
            "genesis": GENESIS_HASH,
        }

    @staticmethod
    async def verify_async(session, limit: int | None = None) -> dict[str, Any]:
        """Async twin of :meth:`verify` for the FastAPI request path."""
        query = select(AuditLog).order_by(AuditLog.id.asc())
        if limit:
            query = query.limit(limit)
        rows: Iterable[AuditLog] = (await session.execute(query)).scalars().all()

        expected_prev = GENESIS_HASH
        checked = 0
        first_bad_id: int | None = None
        head_hash = GENESIS_HASH
        for row in rows:
            payload = _row_payload(
                user_id=row.user_id,
                badge_number=row.badge_number,
                action_type=row.action_type.value
                if isinstance(row.action_type, AuditAction)
                else str(row.action_type),
                target_resource=row.target_resource,
                case_id=row.case_id,
                jurisdiction_id=row.jurisdiction_id,
                ip_address=row.ip_address,
                trace_id=row.trace_id,
                details=row.details,
                timestamp=row.timestamp,
            )
            computed = chain_hash(expected_prev, canonical_json(payload))
            if row.prev_row_hash != expected_prev or row.row_hash != computed:
                first_bad_id = first_bad_id if first_bad_id is not None else row.id
            expected_prev = row.row_hash
            head_hash = row.row_hash
            checked += 1

        valid = first_bad_id is None
        if not valid:
            log.error("audit.verification_failed", first_bad_id=first_bad_id, checked=checked)
        return {
            "valid": valid,
            "checked": checked,
            "first_tampered_id": first_bad_id,
            "head_hash": head_hash,
            "genesis": GENESIS_HASH,
        }

    # --------------------------------------------------------------- anchors
    @staticmethod
    def anchor(session: Session, object_store, bucket: str) -> dict[str, Any]:
        """Write the current head hash to the separately-credentialed bucket."""
        from app.db.models import AuditAnchor

        head = session.get(AuditChainHead, 1)
        head_hash = head.last_hash if head else GENESIS_HASH
        last_id = head.last_id if head else 0
        count = int(session.execute(select(func.count(AuditLog.id))).scalar() or 0)
        timestamp = utcnow()
        filename = f"anchor-{timestamp.strftime('%Y%m%d')}-{last_id}.json"
        payload = canonical_json(
            {
                "anchored_at": timestamp.isoformat(),
                "last_audit_id": last_id,
                "head_hash": head_hash,
                "row_count": count,
            }
        ).encode("utf-8")
        object_store.put(bucket, filename, payload, content_type="application/json")
        session.add(
            AuditAnchor(
                last_audit_id=last_id,
                head_hash=head_hash,
                storage_key=filename,
                row_count=count,
            )
        )
        session.flush()
        log.info("audit.anchored", last_audit_id=last_id, head_hash=head_hash[:16])
        return {"last_audit_id": last_id, "head_hash": head_hash, "row_count": count}


audit_service = AuditService()
