"""
Investigator Activity — Read-only for Viewer + Investigator
Real persisted data from InvestigationFinding and InvestigationSession
No hardcoded DEMO_ACTIVITY — genuine DB records
"""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import get_container

from app.db.session import get_db_session
from app.db.models import InvestigationFinding, InvestigationSession, Case, User
from app.security.deps import Principal, get_principal, get_scope, JurisdictionScope
from app.errors import NotFoundError
from app.services import cases as case_service

router = APIRouter(tags=["investigator-activity"])


async def _provenance_checks(
    session: AsyncSession, finding: InvestigationFinding
) -> dict[str, Any]:
    """Compute the provenance checks for one finding from stored records.

    The activity feed used to print two permanent ticks — "Evidence verified"
    and "Source traceable" — next to every finding, regardless of whether the
    cited evidence existed at all.  A tick that is not backed by a check is
    worse than no tick: it tells an investigator the chain was verified when
    nobody verified anything.

    These are computed, not asserted:
      * ``evidence_verified`` — every document the finding cites resolves to a
        live ``CaseDocument`` row, and its stored bytes still match the hash
        recorded at ingestion (chain of custody).
      * ``source_traceable`` — at least one of those documents has a
        ``SourceReference`` naming the position in the original dataset file
        it came from, so the chain reaches past the document to its origin.
    """
    from app.db.models import CaseDocument, SourceReference
    from app.domain.provenance import content_hash

    cited: set[str] = set()
    for item in finding.evidence or []:
        if isinstance(item, dict) and item.get("doc_id"):
            cited.add(str(item["doc_id"]))
        elif isinstance(item, str):
            cited.add(item)

    if not cited:
        return {
            "evidence_verified": False,
            "source_traceable": False,
            "evidence_cited": 0,
            "evidence_resolved": 0,
            "detail": "This finding cites no evidence document.",
        }

    rows = list(
        (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.id.in_(cited), CaseDocument.is_deleted.is_(False)
                )
            )
        ).scalars()
    )
    resolved = len(rows)

    # Chain of custody: the stored bytes must still be the recorded bytes.
    hashes_match = 0
    try:
        container = get_container()
        bucket = container.settings.minio_bucket_documents
        for doc in rows:
            raw = container.object_store.get(bucket, doc.storage_key)
            if content_hash(raw) == doc.content_hash:
                hashes_match += 1
    except Exception:  # noqa: BLE001 — unreadable storage is a failed check
        hashes_match = 0

    referenced = 0
    if rows:
        referenced = (
            await session.execute(
                select(func.count(func.distinct(SourceReference.doc_id))).where(
                    SourceReference.doc_id.in_([d.id for d in rows])
                )
            )
        ).scalar_one()

    evidence_verified = resolved == len(cited) and hashes_match == resolved
    source_traceable = referenced > 0

    if not evidence_verified:
        if resolved < len(cited):
            detail = (
                f"{len(cited) - resolved} of {len(cited)} cited evidence documents "
                "do not resolve to a live record."
            )
        else:
            detail = (
                f"{resolved - hashes_match} of {resolved} cited documents no longer "
                "match their recorded hash."
            )
    elif not source_traceable:
        detail = "The cited documents resolve, but none carries a source reference."
    else:
        detail = (
            f"All {resolved} cited documents resolve and match their recorded hash; "
            f"{referenced} carry a source reference."
        )

    return {
        "evidence_verified": evidence_verified,
        "source_traceable": source_traceable,
        "evidence_cited": len(cited),
        "evidence_resolved": resolved,
        "detail": detail,
    }

@router.get("/investigator-activity")
async def list_investigator_activity(
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
):
    """
    Read-only Investigator Activity — both VIEWER and INVESTIGATOR
    Same data scope, different operation permissions
    Backend authorization enforced via JurisdictionScope
    Real persisted investigation findings from PostgreSQL
    """
    # Get allowed case IDs
    from app.services.cases import visible_case_ids
    allowed_ids = await visible_case_ids(session, scope)

    # Query findings that are visible
    stmt = select(InvestigationFinding).where(
        InvestigationFinding.case_id.in_(allowed_ids) if allowed_ids else False
    ).order_by(InvestigationFinding.created_at.desc()).limit(100)

    result = await session.execute(stmt)
    findings = result.scalars().all()

    # Also get case numbers and investigator names
    activities = []
    for finding in findings:
        # Get case
        try:
            case = await session.get(Case, finding.case_id)
            case_number = case.case_number if case else finding.case_id
        except:
            case_number = finding.case_id

        # Get investigator
        investigator_badge = "UNKNOWN"
        investigator_name = "Unknown Investigator"
        try:
            if finding.reviewed_by:
                user = await session.get(User, finding.reviewed_by)
                if user:
                    investigator_badge = user.badge_number
                    investigator_name = user.full_name
            # Also check details
            details = finding.details or {}
            if details.get("investigator"):
                investigator_badge = details.get("investigator")
            if details.get("investigator_name"):
                investigator_name = details.get("investigator_name")
        except:
            pass

        # Extract evidence codes from evidence field and details
        evidence_codes = []
        try:
            # evidence field is list of dicts with doc_id and evidence_id
            for ev in (finding.evidence or []):
                if isinstance(ev, dict):
                    code = ev.get("evidence_id") or ev.get("doc_id")
                    if code:
                        # Convert doc_id back to evidence code if needed
                        if code.startswith("evidence-"):
                            # evidence-042-demo -> E-042
                            num = code.replace("evidence-", "").replace("-demo", "")
                            if num.isdigit():
                                evidence_codes.append(f"E-{int(num):03d}")
                            else:
                                evidence_codes.append(code)
                        else:
                            evidence_codes.append(code)
                elif isinstance(ev, str):
                    evidence_codes.append(ev)
            # Also from details
            details = finding.details or {}
            if details.get("evidence"):
                # Might be list
                pass
        except:
            evidence_codes = []

        # Build activity
        details = finding.details or {}
        activity = {
            "id": finding.id,
            "investigator": investigator_badge,
            "investigatorName": investigator_name,
            "caseId": finding.case_id,
            "caseNumber": case_number,
            "subject": details.get("subject") or " ↔ ".join(finding.entity_keys[:2]) if len(finding.entity_keys) >=2 else finding.entity_keys[0] if finding.entity_keys else "Unknown",
            "finding": finding.title,
            "narrative": finding.narrative,
            "reason": finding.reason,
            "evidence": evidence_codes[:10],
            "evidenceStrength": details.get("evidence_strength") or finding.confidence_band,
            "classification": details.get("classification") or finding.finding_type,
            "confidence": finding.confidence,
            "confidenceBand": finding.confidence_band,
            "completedAt": finding.created_at.isoformat() if finding.created_at else details.get("completed_at") or "Unknown",
            "connectionPath": details.get("connection_path") or finding.entity_keys,
            "limitations": details.get("limitations") or [],
            "provenance": details.get("provenance") or finding.reason,
            "status": finding.status,
            "method": finding.method,
            "entityKeys": finding.entity_keys,
            "provenanceChecks": await _provenance_checks(session, finding),
        }
        activities.append(activity)

    # If no findings yet (fresh DB without seed), return empty honestly — no fake fallback
    if not activities:
        return {"activities": [], "total": 0, "message": "No investigation activity found — run seed_demo.py"}

    return {"activities": activities, "total": len(activities)}

@router.get("/investigator-activity/{activity_id}")
async def get_investigator_activity(
    activity_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
):
    finding = await session.get(InvestigationFinding, activity_id)
    if not finding:
        # Try session
        sess = await session.get(InvestigationSession, f"session-{activity_id.lower()}")
        if sess:
            # Build from session state
            state = sess.state or {}
            case = await session.get(Case, sess.case_id) if sess.case_id else None
            return {
                "id": activity_id,
                "caseId": sess.case_id,
                "caseNumber": case.case_number if case else sess.case_id,
                "investigator": state.get("investigator") or "DEMO-INVESTIGATOR",
                "investigatorName": "Demo Investigator",
                "subject": state.get("subject") or "Unknown",
                "finding": state.get("finding") or sess.title,
                "evidence": state.get("evidence") or [],
                "evidenceStrength": state.get("evidence_strength") or "HIGH",
                "classification": state.get("classification") or "FACT",
                "completedAt": state.get("completed_at") or sess.created_at.isoformat() if sess.created_at else "Unknown",
                "connectionPath": state.get("connection_path") or [],
                "limitations": state.get("limitations") or [],
                "provenance": state.get("provenance") or "",
                "narrative": state.get("finding") or "",
            }
        raise NotFoundError("Investigation activity not found")

    # Check case scope
    await case_service.require_case(session, scope, finding.case_id)

    case = await session.get(Case, finding.case_id)
    case_number = case.case_number if case else finding.case_id

    # Investigator
    investigator_badge = "UNKNOWN"
    investigator_name = "Unknown Investigator"
    if finding.reviewed_by:
        user = await session.get(User, finding.reviewed_by)
        if user:
            investigator_badge = user.badge_number
            investigator_name = user.full_name
    details = finding.details or {}
    if details.get("investigator"):
        investigator_badge = details.get("investigator")
    if details.get("investigator_name"):
        investigator_name = details.get("investigator_name")

    evidence_codes = []
    for ev in (finding.evidence or []):
        if isinstance(ev, dict):
            code = ev.get("evidence_id") or ev.get("doc_id")
            if code:
                if code.startswith("evidence-"):
                    num = code.replace("evidence-", "").replace("-demo", "")
                    if num.isdigit():
                        evidence_codes.append(f"E-{int(num):03d}")
                    else:
                        evidence_codes.append(code)
                else:
                    evidence_codes.append(code)

    return {
        "id": finding.id,
        "investigator": investigator_badge,
        "investigatorName": investigator_name,
        "caseId": finding.case_id,
        "caseNumber": case_number,
        "subject": details.get("subject") or " ↔ ".join(finding.entity_keys[:2]) if len(finding.entity_keys) >=2 else finding.entity_keys[0] if finding.entity_keys else "Unknown",
        "finding": finding.title,
        "narrative": finding.narrative,
        "reason": finding.reason,
        "evidence": evidence_codes,
        "evidenceStrength": details.get("evidence_strength") or finding.confidence_band,
        "classification": details.get("classification") or finding.finding_type,
        "confidence": finding.confidence,
        "confidenceBand": finding.confidence_band,
        "completedAt": finding.created_at.isoformat() if finding.created_at else details.get("completed_at") or "Unknown",
        "connectionPath": details.get("connection_path") or finding.entity_keys,
        "limitations": details.get("limitations") or [],
        "provenance": details.get("provenance") or finding.reason,
        "status": finding.status,
        "method": finding.method,
        "entityKeys": finding.entity_keys,
        "evidenceDetails": finding.evidence,
        "details": finding.details,
        "provenanceChecks": await _provenance_checks(session, finding),
    }
