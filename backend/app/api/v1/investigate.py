"""Evidence-driven investigation endpoints.

POST /investigate answers a question with a structured, provenanced
investigation; GET /investigate/patterns runs the detectors without a
question; GET /investigate/sessions/{id} reads a thread's memory.
INVESTIGATOR or ADMIN only; investigation calls are hash-chain audited.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import registry
from app.db.session import get_db_session
from app.domain.enums import AuditAction
from app.errors import ValidationFailedError
from app.investigator.memory import get_session, memory_section
from app.investigator.orchestrator import detect_patterns_standalone
from app.investigator.orchestrator import investigate as run_orchestrator
from app.investigator.schemas import InvestigateRequest, InvestigatorResponse
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    audited,
    get_audit_recorder,
    get_scope,
    require_roles,
)

router = APIRouter(prefix="/investigate", tags=["investigate"])


@router.post("", response_model=InvestigatorResponse)
@audited(
    AuditAction.INVESTIGATE,
    target=lambda result, **kw: f"investigation:{result.investigation_id}",
    case_id=lambda result, **kw: result.scope.case_id,
    details=lambda result, **kw: {
        "mode": result.scope.mode,
        "patterns": len(result.patterns),
        "hypotheses": len(result.hypotheses),
    },
)
async def run_investigation(
    payload: InvestigateRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> InvestigatorResponse:
    """Answer an investigation question with evidence attached."""
    return await run_orchestrator(
        session,
        scope,
        principal,
        question=payload.question,
        case_id=payload.case_id,
        investigation_id=payload.investigation_id,
        objective=payload.objective,
        max_patterns=payload.max_patterns,
        include_excluded=payload.include_excluded,
    )


@router.get("/patterns")
@audited(
    AuditAction.INVESTIGATE,
    target=lambda result, **kw: f"patterns:{kw.get('case_id') or 'master'}",
    case_id=lambda result, **kw: kw.get("case_id"),
    details=lambda result, **kw: {
        "mode": result.get("mode"),
        "count": result.get("count"),
    },
)
async def investigation_patterns(
    case_id: str | None = None,
    max_patterns: int = Query(25, ge=1, le=100),
    include_excluded: bool = Query(True),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """On-demand structured pattern detection over a case or master scope."""
    return await detect_patterns_standalone(
        session,
        scope,
        case_id=case_id,
        max_patterns=max_patterns,
        include_excluded=include_excluded,
    )


class NetworkAnalysisRequest(BaseModel):
    """Explicit network-analysis trigger over one of the three graph scopes."""

    mode: Literal["master", "case", "person"] = "master"
    case_id: str | None = Field(default=None, description="Required when mode=case.")
    person_key: str | None = Field(default=None, description="Required when mode=person.")
    max_patterns: int = Field(default=25, ge=1, le=100)
    include_excluded: bool = True


@router.post("/network-analysis")
@audited(
    AuditAction.INVESTIGATE,
    target=lambda result, **kw: f"network-analysis:{kw.get('payload').mode}",
    case_id=lambda result, **kw: kw.get("payload").case_id,
    details=lambda result, **kw: {
        "mode": kw.get("payload").mode,
        "person_key": kw.get("payload").person_key,
    },
)
async def start_network_analysis(
    payload: NetworkAnalysisRequest,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict[str, Any]:
    """Start a long-running network analysis job (never auto-runs).

    Returns a job id immediately; the client polls GET /investigate/jobs/{id}
    or subscribes to the WebSocket. Deterministic graph/network results are
    preserved even when the reasoning model is unavailable or times out.
    """
    from app.db.session import async_session
    from app.investigator.network_analysis import analyze_network
    from app.services.investigation_jobs import TERMINAL_STATUSES, start_investigation_job

    captured_scope = scope
    captured_principal = principal
    captured = payload

    async def work(reporter):
        async with async_session() as bg_session:
            result = await analyze_network(
                bg_session,
                captured_scope,
                captured_principal,
                mode=captured.mode,
                case_id=captured.case_id,
                person_key=captured.person_key,
                max_patterns=captured.max_patterns,
                include_excluded=captured.include_excluded,
                reporter=reporter,
            )
            status = str(result.get("status", "COMPLETED"))
            terminal_status = status if status in TERMINAL_STATUSES else "COMPLETED"
            await reporter.update(
                status=terminal_status,
                stage="COMPLETED" if terminal_status == "COMPLETED" else status,
                progress_pct=100 if terminal_status == "COMPLETED" else 90,
                message=(
                    "Network analysis completed"
                    if terminal_status == "COMPLETED"
                    else f"Deterministic network analysis preserved ({terminal_status})"
                ),
                result=result,
            )
            return result

    row = await start_investigation_job(
        dataset_id=None,
        case_id=payload.case_id,
        investigation_id=None,
        question=f"Network analysis — {payload.mode.upper()} scope",
        objective=(
            "Graph metrics, community analysis, cross-case analysis, pattern "
            "detection, evidence retrieval and findings for the selected scope."
        ),
        requested_by=getattr(principal, "id", None),
        work=work,
    )
    return row


@router.get("/sessions/{investigation_id}")
async def investigation_memory(
    investigation_id: str,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Read an investigation thread's memory (dataset-pinned)."""
    dataset = await registry.active_dataset(session)
    if dataset is None:
        raise ValidationFailedError("No dataset is currently active.")
    row = await get_session(session, investigation_id, dataset_id=dataset.id)
    state = dict(row.state or {})
    return {
        "investigation_id": row.id,
        "dataset_id": row.dataset_id,
        "case_id": row.case_id,
        "scope": row.scope,
        "title": row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "memory": memory_section(row).model_dump(),
        "objective": state.get("objective", ""),
        "questions": state.get("questions", []),
        "contradictions": state.get("contradictions", [])[-20:],
    }
