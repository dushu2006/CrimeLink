"""Regression tests for AI reasoning provider resolution, error classification, and narrative parsing.

Tests verify:
1. Reasoning role resolves NVIDIA provider correctly.
2. Role-specific reasoning API key is recognized.
3. Global key fallback still works if intended.
4. Missing key returns unavailable.
5. NVIDIA 401/403 produces a specific sanitized provider failure.
6. NVIDIA 429 produces rate-limit/unavailable state.
7. NVIDIA timeout produces timeout state.
8. Successful provider response reaches narrative parser.
9. Valid investigator JSON produces ModelSection.available=True.
10. Invalid model JSON produces narrative_unparseable, NOT api_key_unavailable.
11. Deterministic analysis remains intact when provider fails.
12. No API key can appear in logs/errors/responses.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.gateway import AIGateway
from app.ai.router import AIModelRouter, _safe_error
from app.config import Settings
from app.investigator.network_analysis import analyze_network
from app.security.deps import JurisdictionScope, Principal


class FakeCompletions:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.content = content
        self.exc = exc

    async def create(self, **kwargs):
        if self.exc is not None:
            raise self.exc
        choice = SimpleNamespace(message=SimpleNamespace(content=self.content))
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
        return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.chat = SimpleNamespace(completions=FakeCompletions(content=content, exc=exc))


# 1. Reasoning role resolves NVIDIA provider correctly.
def test_reasoning_role_resolves_nvidia_provider(settings: Settings):
    cfg = settings.model_copy(
        update={
            "ai_provider": "nvidia",
            "ai_reasoning_provider": "nvidia",
            "ai_reasoning_model": "deepseek-ai/deepseek-v4-pro-0813",
            "ai_reasoning_api_key": "test-nvapi-key",
            "ai_reasoning_base_url": "https://integrate.api.nvidia.com/v1",
        }
    )
    router = AIModelRouter(cfg)
    inv = router.route("investigation_reasoning")
    assert inv.role == "reasoning"
    assert inv.provider == "nvidia"
    assert inv.base_url == "https://integrate.api.nvidia.com/v1"
    # Legacy/deprecated model aliases to active model
    assert inv.model in ("nvidia/nemotron-3-super-120b-a12b", "deepseek-ai/deepseek-v4.1-flash")
    assert inv.available is True


# 2. Role-specific reasoning API key is recognized.
def test_role_specific_reasoning_api_key_recognized(settings: Settings):
    cfg = settings.model_copy(
        update={
            "ai_api_key": "global-key",
            "ai_reasoning_api_key": "role-specific-key",
        }
    )
    assert cfg.role_config("reasoning")["api_key"] == "role-specific-key"
    assert cfg.ai_role_available("reasoning") is True


# 3. Global key fallback still works if intended.
def test_global_key_fallback_works(settings: Settings):
    cfg = settings.model_copy(
        update={
            "ai_api_key": "global-fallback-key",
            "ai_reasoning_api_key": None,
        }
    )
    assert cfg.role_config("reasoning")["api_key"] == "global-fallback-key"
    assert cfg.ai_role_available("reasoning") is True


# 4. Missing key returns unavailable.
async def test_missing_key_returns_unavailable(settings: Settings):
    cfg = settings.model_copy(
        update={
            "ai_api_key": None,
            "ai_reasoning_api_key": None,
        }
    )
    router = AIModelRouter(cfg)
    result = await router.chat("investigation_reasoning", "system", "user")
    assert result == {"available": False, "reason": "no_api_key_for_role_reasoning"}


# 5. NVIDIA 401/403 produces a specific sanitized provider failure.
async def test_nvidia_auth_failure_produces_specific_sanitized_failure(monkeypatch, settings: Settings):
    class AuthenticationError(Exception):
        status_code = 401

    cfg = settings.model_copy(update={"ai_reasoning_api_key": "invalid-key"})
    router = AIModelRouter(cfg)

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kw: FakeClient(exc=AuthenticationError("Invalid API key provided")),
    )

    result = await router.chat("investigation_reasoning", "system", "user")
    assert result["available"] is False
    assert "authentication_failed" in result["reason"]

    gateway = AIGateway(settings=cfg, router=router)
    model_section = await gateway.investigate_narrative(
        question="Analyze scope", brief="test brief", investigation_id="inv-1"
    )
    assert model_section.available is False
    assert any("authentication failed" in c for c in model_section.caveats)


# 6. NVIDIA 429 produces rate-limit/unavailable state.
async def test_nvidia_rate_limit_produces_rate_limited_state(monkeypatch, settings: Settings):
    class RateLimitError(Exception):
        status_code = 429

    cfg = settings.model_copy(update={"ai_reasoning_api_key": "test-key", "ai_max_retries": 0})
    router = AIModelRouter(cfg)

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kw: FakeClient(exc=RateLimitError("Rate limit exceeded")),
    )

    result = await router.chat("investigation_reasoning", "system", "user", max_retries_override=0)
    assert result["available"] is False
    assert "rate_limited" in result["reason"]

    gateway = AIGateway(settings=cfg, router=router)
    model_section = await gateway.investigate_narrative(
        question="Analyze scope", brief="test brief", investigation_id="inv-1"
    )
    assert model_section.available is False
    assert any("rate-limited" in c for c in model_section.caveats)


# 7. NVIDIA timeout produces timeout state.
async def test_nvidia_timeout_produces_timeout_state(monkeypatch, settings: Settings):
    cfg = settings.model_copy(update={"ai_reasoning_api_key": "test-key", "ai_max_retries": 0})
    router = AIModelRouter(cfg)

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kw: FakeClient(exc=asyncio.TimeoutError("Request timed out")),
    )

    result = await router.chat("investigation_reasoning", "system", "user", max_retries_override=0)
    assert result["available"] is False
    assert "timeout" in result["reason"]

    gateway = AIGateway(settings=cfg, router=router)
    model_section = await gateway.investigate_narrative(
        question="Analyze scope", brief="test brief", investigation_id="inv-1"
    )
    assert model_section.available is False
    assert any("timed out" in c for c in model_section.caveats)


# 8 & 9. Successful provider response reaches narrative parser & valid JSON produces ModelSection.available=True.
async def test_successful_provider_response_produces_available_model_section(monkeypatch, settings: Settings):
    valid_payload = {
        "summary": "Coordinated activity identified between Entity A and Entity B.",
        "observation": "Topology spans 2 nodes and 1 relationship.",
        "interpretation": "Convergence indicates high likelihood of structured transactions.",
        "assessment": "Overall confidence is 85%.",
        "convergence_note": "Evidence streams agree.",
        "caveats": ["Bank records for Entity B remain unverified."],
        "suggested_next_actions": ["Request certified ledger records."],
    }
    cfg = settings.model_copy(update={"ai_reasoning_api_key": "valid-key"})
    router = AIModelRouter(cfg)

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kw: FakeClient(content=json.dumps(valid_payload)),
    )

    gateway = AIGateway(settings=cfg, router=router)
    model_section = await gateway.investigate_narrative(
        question="Analyze network", brief="brief", investigation_id="inv-1"
    )

    assert model_section.available is True
    assert model_section.summary == valid_payload["summary"]
    assert model_section.observation == valid_payload["observation"]
    assert model_section.interpretation == valid_payload["interpretation"]
    assert model_section.assessment == valid_payload["assessment"]
    assert model_section.caveats == valid_payload["caveats"]
    assert model_section.suggested_next_actions == valid_payload["suggested_next_actions"]


# 10. Invalid model JSON produces narrative_unparseable, NOT api_key_unavailable.
async def test_invalid_model_json_produces_narrative_unparseable(monkeypatch, settings: Settings):
    cfg = settings.model_copy(update={"ai_reasoning_api_key": "valid-key"})
    router = AIModelRouter(cfg)

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kw: FakeClient(content="I am a model and here is some text without JSON format."),
    )

    gateway = AIGateway(settings=cfg, router=router)
    model_section = await gateway.investigate_narrative(
        question="Analyze network", brief="brief", investigation_id="inv-1"
    )

    assert model_section.available is False
    assert model_section.reason == "narrative_unparseable"
    assert "api_key" not in model_section.reason
    assert any("not valid JSON" in c for c in model_section.caveats)


# 11. Deterministic analysis remains intact when provider fails.
async def test_deterministic_analysis_remains_intact_when_provider_fails(monkeypatch, db, users, container, settings: Settings):
    from app.datasets import registry
    from app.db.base import new_uuid
    from app.db.models import Case
    from app.domain.enums import CaseStatus
    from app.domain.models import GraphEdge, GraphNode

    ds = await registry.create_dataset(db, name="DetPreserve DS", version="1", source_kind="folder")
    await registry.activate(db, ds)
    case = Case(
        id=new_uuid(),
        case_number="DP-01",
        title="Deterministic Preservation Test",
        jurisdiction_id="RJ-JAIPUR",
        dataset_id=ds.id,
        status=CaseStatus.OPEN,
    )
    db.add(case)
    await db.commit()

    container.injector.inject_nodes([
        GraphNode(provenance_key="p1", label="Person", properties={"name": "Alice", "case_ids": [case.id], "dataset_id": ds.id, "source_doc_id": "doc-dp", "confidence": 1.0}),
        GraphNode(provenance_key="p2", label="Person", properties={"name": "Bob", "case_ids": [case.id], "dataset_id": ds.id, "source_doc_id": "doc-dp", "confidence": 1.0}),
    ])
    container.injector.inject_edges([
        GraphEdge(source_key="p1", target_key="p2", rel_type="ASSOCIATE_OF", properties={"source_doc_id": "doc-dp", "confidence": 0.9}, discriminator="dp-e1")
    ])

    class FailingRouter:
        def route(self, task: str):
            return SimpleNamespace(role="reasoning", provider="nvidia", model="test", available=True)

        async def chat(self, *args, **kwargs):
            return {"available": False, "reason": "invocation_failed: APIStatusError"}

    monkeypatch.setattr("app.investigator.network_analysis.get_ai_gateway", lambda: AIGateway(settings=settings, router=FailingRouter()))

    principal = Principal(users["INV-0001"])
    scope = JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())

    result = await analyze_network(db, scope, principal, mode="master")
    assert result["status"] == "AI_UNAVAILABLE"
    assert result["analysis"]["graph"]["nodes"] == 2
    assert result["analysis"]["graph"]["edges"] == 1
    assert len(result["analysis"]["metrics"]["betweenness"]) > 0
    assert len(result["response"]["entities"]) > 0
    assert result["response"]["assessment"]["model"]["available"] is False
    assert result["response"]["assessment"]["model"]["reason"] == "invocation_failed: APIStatusError"


# 12. No API key can appear in logs/errors/responses.
def test_no_api_key_can_appear_in_logs_errors_or_responses():
    secret_key = "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"
    leaked_exc = Exception(f"Failed to authenticate with Bearer {secret_key} on endpoint")

    sanitized = _safe_error(leaked_exc)
    assert secret_key not in sanitized
    assert "[redacted]" in sanitized
