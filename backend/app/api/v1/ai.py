"""AI investigation endpoints.

Every AI interaction goes through the AI Gateway and is audited.  Results are
returned with pseudonymous IDs; the frontend resolves them after confirming
authorization.

Two contract rules govern this module, both learned from the ``422`` that made
Case AI unusable:

* **A validation error must describe something the caller can fix.**  A
  question is rejected only when it is genuinely empty or absurdly long --
  never for being short, and never for using a field name an older client
  happened to send.
* **A model or provider problem is not an HTTP error.**  Those return ``200``
  with ``available: false`` and an explanation naming the environment variable
  to set, because the request itself was perfectly valid.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import get_ai_gateway
from app.ai.router import get_router
from app.config import get_settings
from app.db.session import get_db_session
from app.logging import get_logger
from app.security.deps import (
    JurisdictionScope,
    Principal,
    get_db_session as _unused_session,  # noqa: F401  (kept for import parity)
    get_principal,
    get_scope,
    require_roles,
)
from app.services import cases as case_service

log = get_logger("crimelink.api.ai")

router = APIRouter(prefix="/ai", tags=["ai"])

#: Generous, but bounded: a prompt has to fit in a context window eventually.
MAX_QUESTION_CHARS = 20_000


class AskRequest(BaseModel):
    """The Case AI request body.

    ``question`` accepts the aliases ``query``, ``prompt`` and ``text``.
    Different callers in this codebase have used different names over time,
    and rejecting a perfectly clear request over a field name is exactly the
    sort of 422 this endpoint became notorious for.  Accepting the aliases is
    additive: every payload that worked before still works.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    question: str = Field(
        default="",
        description="The investigator's question about this case.",
    )
    query: str | None = Field(default=None, exclude=True)
    prompt: str | None = Field(default=None, exclude=True)
    text: str | None = Field(default=None, exclude=True)

    depth: int = Field(
        default=2,
        ge=1,
        description=(
            "How many hops of case context to retrieve around the target. "
            "There is no upper bound; retrieval stops at the edge of the case "
            "subgraph or at the context budget, whichever comes first."
        ),
    )
    target_key: str | None = Field(
        default=None,
        description=(
            "Optional entity to centre retrieval on. Omit to use the whole "
            "case subgraph."
        ),
    )

    @model_validator(mode="after")
    def _coalesce_and_require_text(self) -> "AskRequest":
        text = (
            self.question
            or self.query
            or self.prompt
            or self.text
            or ""
        ).strip()
        if not text:
            raise ValueError(
                "A question is required. Send a non-empty 'question' field "
                "(the aliases 'query', 'prompt' and 'text' are also accepted)."
            )
        if len(text) > MAX_QUESTION_CHARS:
            raise ValueError(
                f"The question is {len(text)} characters long; the maximum is "
                f"{MAX_QUESTION_CHARS}. Ask a more specific question or split it up."
            )
        self.question = text
        return self


class AskResponse(BaseModel):
    """What the browser receives. Never contains credentials."""

    query_id: str
    request_id: str
    available: bool
    fallback_reason: str | None = None
    model: str | None = None
    role: str
    provider: str | None = None
    pseudonymized: bool
    latency_ms: int
    context: dict[str, Any] = Field(default_factory=dict)
    finding: dict[str, Any]


@router.post("/cases/{case_id}/ask", response_model=AskResponse)
async def ask_case_question(
    case_id: str,
    payload: AskRequest,
    request: Request,
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
) -> AskResponse:
    """Ask a question about one case, answered from that case's evidence only.

    The case is resolved and jurisdiction-checked *before* the gateway runs:
    an unknown case is a 404 and an out-of-scope case is a 404 as well, so the
    endpoint cannot be used to probe for the existence of cases the caller may
    not see.  Previously neither check happened and any case id at all
    returned 200.
    """
    # 404/403 before any retrieval, model call or audit entry.
    await case_service.require_case(session, scope, case_id)

    request_id = getattr(request.state, "trace_id", None) or str(uuid.uuid4())
    gateway = get_ai_gateway()
    response = await gateway.ask(
        question=payload.question,
        case_id=case_id,
        principal_id=principal.id,
        depth=payload.depth,
        target_key=payload.target_key,
        request_id=request_id,
    )

    if not response.available:
        # Not an error for the transport: the request was valid, the model was
        # not reachable.  Logged with the request id so an operator can join
        # the browser's complaint to the server's log line.
        log.warning(
            "ai.case_ask_unavailable",
            request_id=request_id,
            case_id=case_id,
            reason=response.fallback_reason,
        )

    settings = get_settings()
    return AskResponse(
        query_id=response.query_id,
        request_id=request_id,
        available=response.available,
        fallback_reason=response.fallback_reason,
        model=response.model,
        role=response.role,
        provider=settings.role_config(response.role).get("provider"),
        pseudonymized=response.pseudonymized,
        latency_ms=response.latency_ms,
        context=response.context,
        finding=response.finding.model_dump(),
    )


# ---------------------------------------------------------------------------
# Health / configuration reporting
# ---------------------------------------------------------------------------


class RoleTestRequest(BaseModel):
    role: Literal["extraction", "reasoning", "explanation", "classification", "embedding"]


@router.get("/health")
async def ai_health(
    principal: Principal = Depends(get_principal),
) -> dict:
    """Per-role AI configuration, without ever revealing a key.

    Returns provider, model, base URL, whether a key is present, and which
    environment variable supplied it.  ``key_fingerprint`` is a truncated hash
    so two different keys can be told apart in a support conversation without
    the key itself leaving the server.
    """
    settings = get_settings()
    roles = [settings.ai_role_report(role) for role in settings.AI_ROLES]
    return {
        "configured": any(role["available"] for role in roles),
        "pseudonymize": settings.ai_pseudonymize,
        "allow_raw_pii": settings.ai_allow_raw_pii,
        "context_budget": {
            "max_nodes": settings.ai_max_context_nodes,
            "max_edges": settings.ai_max_context_edges,
        },
        "roles": roles,
    }


@router.post("/health/test")
async def ai_connectivity_test(
    payload: RoleTestRequest,
    principal: Principal = Depends(require_roles("ADMIN")),
) -> dict:
    """Actually call the configured provider for one role and report the result.

    This is the button an administrator presses to find out whether the key in
    the environment works, without having to run an investigation to discover
    it.  It sends a tiny prompt, so the cost is negligible, and it reports the
    real provider error when there is one.
    """
    settings = get_settings()
    report = settings.ai_role_report(payload.role)
    if not report["available"]:
        return {
            **report,
            "ok": False,
            "detail": (
                f"No API key is configured for the {payload.role} role. Set "
                f"CRIMELINK_AI_{payload.role.upper()}_API_KEY or the shared "
                "CRIMELINK_AI_API_KEY."
            ),
        }

    model_router = get_router()
    if payload.role == "embedding":
        result = await model_router.embed("embedding", ["connectivity check"])
        ok = bool(result.get("available"))
        return {
            **report,
            "ok": ok,
            "detail": (
                f"Received a {result.get('dimensions')}-dimension embedding in "
                f"{result.get('latency_ms')} ms."
                if ok
                else str(result.get("reason"))
            ),
            "latency_ms": result.get("latency_ms", 0),
        }

    task = {
        "extraction": "extraction",
        "reasoning": "investigation_reasoning",
        "explanation": "explanation",
        "classification": "classification",
    }[payload.role]
    result = await model_router.chat(
        task,
        system_prompt="You are a connectivity check. Reply with the single word OK.",
        user_prompt="Reply with OK.",
        max_tokens=16,
    )
    ok = bool(result.get("available"))
    return {
        **report,
        "ok": ok,
        "detail": (
            f"Model replied in {result.get('latency_ms')} ms: "
            f"{str(result.get('content', ''))[:120]}"
            if ok
            else str(result.get("reason"))
        ),
        "latency_ms": result.get("latency_ms", 0),
    }
