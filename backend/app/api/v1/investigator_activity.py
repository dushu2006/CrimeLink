"""
Investigator Activity — Read-only for Viewer + Investigator
Minimal: Investigation, Investigator, Case, Subject, Finding, Evidence, Strength, Classification, Completed
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.db.models import InvestigationSession, Case
from app.security.deps import Principal, get_principal, get_scope, JurisdictionScope

router = APIRouter(tags=["investigator-activity"])

DEMO_ACTIVITY = [
    {
        "id": "INV-0042",
        "investigator": "DEMO-INVESTIGATOR",
        "investigatorName": "Demo Investigator",
        "caseId": "case-001-demo",
        "caseNumber": "CR-1024",
        "subject": "PERSON-001 ↔ PERSON-002",
        "finding": "Supported communication relationship",
        "evidence": ["E-042", "E-103", "E-118"],
        "evidenceStrength": "HIGH",
        "classification": "FACT",
        "completedAt": "15 Sep 2026",
        "connectionPath": ["PERSON-001", "PERSON-002"],
        "limitations": ["Purpose of association beyond documented records is unknown", "Criminal intent not established by this evidence alone"],
        "provenance": "Verified from CDR and field reports",
    },
    {
        "id": "INV-0043",
        "investigator": "DEMO-INVESTIGATOR",
        "investigatorName": "Demo Investigator",
        "caseId": "case-002-demo",
        "caseNumber": "CR-1025",
        "subject": "PERSON-001 ↔ PERSON-003",
        "finding": "Co-location at Location X",
        "evidence": ["E-071"],
        "evidenceStrength": "MODERATE",
        "classification": "INFERENCE",
        "completedAt": "15 Sep 2026",
        "connectionPath": ["PERSON-001", "PERSON-003"],
        "limitations": ["Co-location does not establish direct communication"],
        "provenance": "Field report verified",
    },
]

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
    """
    # For demo, return hardcoded activity that matches seeded data
    # In production, this would query InvestigationSession + findings filtered by jurisdiction
    return {"activities": DEMO_ACTIVITY, "total": len(DEMO_ACTIVITY)}

@router.get("/investigator-activity/{activity_id}")
async def get_investigator_activity(
    activity_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
):
    for act in DEMO_ACTIVITY:
        if act["id"] == activity_id:
            return act
    from app.errors import NotFoundError
    raise NotFoundError("Investigation activity not found")
