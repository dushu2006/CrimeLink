"""Investigation workflow endpoints (PRD 21: explicit stage execution).

Every button in the investigation workspace maps to one endpoint here:

    GET  /cases/{id}/investigation                stage state + real counts
    POST /cases/{id}/investigation/{stage}/run    execute ONE stage
    GET  /cases/{id}/findings                     persisted findings
    POST /cases/{id}/findings/{fid}/review        confirm / dismiss
    GET  /cases/{id}/persons                      person targets for the case
    GET  /cases/{id}/network/{person_key}         person-centric subgraph

Authorization is the REST model, unchanged: reads require a scoped principal,
runs require INVESTIGATOR or ADMIN, and case access goes through the same
``JurisdictionScope`` as every other case endpoint.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.db.models import InvestigationFinding, InvestigationSession
from app.errors import NotFoundError, ValidationFailedError
from app.security.deps import JurisdictionScope, Principal, get_principal, get_scope, require_roles
from app.services import cases as case_service
from app.services import investigation
from app.services.graph_service import DEFAULT_PERSON_NETWORK_DEPTH, GraphService

router = APIRouter(tags=["investigation"])


@router.get("/cases/{case_id}/investigation")
async def investigation_state(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    await case_service.require_case(session, scope, case_id)
    return await asyncio.to_thread(investigation.workflow_state, case_id)


@router.post("/cases/{case_id}/investigation/{stage_key}/run")
async def run_investigation_stage(
    case_id: str,
    stage_key: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
) -> dict:
    await case_service.require_case(session, scope, case_id)
    try:
        return await asyncio.to_thread(
            investigation.run_stage, case_id, stage_key, principal.id
        )
    except (investigation.StageBlocked, NotFoundError):
        raise
    except Exception as exc:
        raise ValidationFailedError(f"Stage '{stage_key}' failed: {exc}") from exc


@router.get("/cases/{case_id}/findings")
async def case_findings(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    await case_service.require_case(session, scope, case_id)
    return await asyncio.to_thread(investigation.findings_list, case_id)


@router.get("/investigations/{investigation_id}/findings/{finding_id}/graph")
async def finding_graph(
    investigation_id: str,
    finding_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Return only the evidence-derived mini graph for one finding.

    ``investigation_id`` may be a case id for legacy callers or an
    InvestigationSession id for question-based investigations. The trusted
    backend resolves it before applying jurisdiction access checks.
    """
    finding = await session.get(InvestigationFinding, finding_id)
    if finding is None:
        raise NotFoundError("Finding not found.")
    case_id = finding.case_id
    if investigation_id != case_id:
        session_row = await session.get(InvestigationSession, investigation_id)
        if session_row is None or session_row.case_id != case_id:
            raise NotFoundError("Finding not found for this investigation.")
    await case_service.require_case(session, scope, case_id)
    return await asyncio.to_thread(investigation.finding_graph, case_id, finding_id)


@router.post("/cases/{case_id}/findings/{finding_id}/review")
async def review_case_finding(
    case_id: str,
    finding_id: str,
    payload: dict,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
) -> dict:
    await case_service.require_case(session, scope, case_id)
    decision = str(payload.get("decision", "")).upper()
    note = payload.get("note")
    return await asyncio.to_thread(
        investigation.review_finding, case_id, finding_id, decision, note, principal.id
    )


@router.get("/cases/{case_id}/persons")
async def case_persons(
    case_id: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Person nodes of the case — the investigation targets."""
    await case_service.require_case(session, scope, case_id)
    return await GraphService().person_targets(session, scope, case_id)


@router.get("/cases/{case_id}/network/{person_key}")
async def person_network(
    case_id: str,
    person_key: str,
    depth: int = Query(
        DEFAULT_PERSON_NETWORK_DEPTH,
        ge=1,
        description=(
            "How many hops to explore outward from the person. Three by "
            "default; there is deliberately no upper bound, because the hop "
            "count is an exploration parameter and not a property of the "
            "graph. Traversal stops when the depth is reached or when nothing "
            "further is reachable, whichever comes first."
        ),
    ),
    limit: int | None = Query(
        None,
        ge=1,
        description=(
            "Optional cap on the number of nodes returned. Omit it — the "
            "default — to receive everything reachable within the requested "
            "depth."
        ),
    ),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """The person-centric investigation graph: target + typed neighbourhood."""
    await case_service.require_case(session, scope, case_id)
    return await GraphService().person_centric_network(
        session, scope, case_id, person_key, depth=depth, limit=limit
    )


@router.get("/cases/{case_id}/network/{person_key}/findings")
async def person_findings(
    case_id: str,
    person_key: str,
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Findings that involve this person, evidence attached."""
    await case_service.require_case(session, scope, case_id)
    payload = await asyncio.to_thread(investigation.findings_list, case_id)
    items = [
        f for f in payload["items"] if person_key in (f.get("entity_keys") or [])
    ]
    return {"items": items, "target": person_key}
