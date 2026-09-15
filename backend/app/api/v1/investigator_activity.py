"""
Investigator Activity — Read-only for Viewer + Investigator
Real persisted data from InvestigationFinding and InvestigationSession
No hardcoded DEMO_ACTIVITY — genuine DB records
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.db.models import InvestigationFinding, InvestigationSession, Case, User
from app.security.deps import Principal, get_principal, get_scope, JurisdictionScope
from app.errors import NotFoundError
from app.services import cases as case_service

router = APIRouter(tags=["investigator-activity"])

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
    }
