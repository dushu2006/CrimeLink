"""Regression tests: the AI gateway must be honest about WHY it is unavailable.

The development environment intentionally ships blank AI API keys.  In that
state no provider invocation may be attempted at all, and the structured
result must say "no key configured".  Conversely, when a key *is* configured
and the provider call fails, the result must carry the real provider failure
— never the "no API key" wording, which would be false.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.gateway import AIGateway, unavailable_summary
from app.ai.router import AIModelRouter


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class FakeRouter:
    """Stands in for AIModelRouter with a canned chat() result."""

    def __init__(self, result: dict):
        self.result = result
        self.calls: list[str] = []

    async def chat(self, task: str, system_prompt: str, user_prompt: str, **_: object) -> dict:
        self.calls.append(task)
        return self.result


def _gateway(settings, monkeypatch, router) -> AIGateway:
    gateway = AIGateway(settings=settings, router=router)

    async def no_subgraph(case_id: str, *, depth: int = 2):
        return [], []

    monkeypatch.setattr(gateway, "_retrieve_subgraph", no_subgraph)
    return gateway


def _keyless_settings(settings):
    return settings.model_copy(
        update={"ai_api_key": None, "ai_reasoning_api_key": None}
    )


# --------------------------------------------------------------------------- #
# Router: a keyless role must never reach a provider
# --------------------------------------------------------------------------- #


async def test_router_without_key_never_builds_a_client(monkeypatch, settings):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("CRIMELINK_AI_API_KEY", raising=False)
    router = AIModelRouter(_keyless_settings(settings))

    def _boom(*_a, **_k):
        raise AssertionError("no provider client may be built without an API key")

    monkeypatch.setattr("openai.AsyncOpenAI", _boom)

    result = await router.chat("investigation_reasoning", "system", "user")
    assert result == {"available": False, "reason": "no_api_key_for_role_reasoning"}


async def test_router_with_key_reports_the_real_provider_error(monkeypatch, settings):
    """A configured role that fails must surface the provider's own error."""
    configured = settings.model_copy(update={"ai_api_key": "configured-test-key"})
    router = AIModelRouter(configured)

    class NotFoundError(Exception):
        pass

    class FakeCompletions:
        async def create(self, **_kwargs):
            raise NotFoundError("model not found")

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kw: FakeClient())

    result = await router.chat("investigation_reasoning", "system", "user")
    assert result["available"] is False
    assert result["reason"] == "invocation_failed: NotFoundError"


# --------------------------------------------------------------------------- #
# Gateway: the finding must tell the truth for every unavailable reason
# --------------------------------------------------------------------------- #


async def test_ask_without_any_key_says_no_key_is_configured(settings, monkeypatch):
    gateway = _gateway(_keyless_settings(settings), monkeypatch, FakeRouter({
        "available": False, "reason": "no_api_key_for_role_reasoning",
    }))
    response = await gateway.ask(question="Who is connected?", case_id="c-1", principal_id="u-1")

    assert response.available is False
    assert response.fallback_reason == "no_api_key_for_role_reasoning"
    assert "no API key is configured" in response.finding.summary
    # The instruction names the exact variable an operator must set.
    assert "CRIMELINK_AI_REASONING_API_KEY" in response.finding.summary
    # Nothing was attempted, so there is no incident to review.
    assert response.finding.recommended_review is False


async def test_ask_with_key_failure_never_claims_a_missing_key(settings, monkeypatch):
    """Regression: a real provider failure used to be presented as 'no API key'."""
    gateway = _gateway(settings.model_copy(update={"ai_api_key": "configured-test-key"}), monkeypatch, FakeRouter({
        "available": False, "reason": "invocation_failed: NotFoundError",
    }))
    response = await gateway.ask(question="Who is connected?", case_id="c-1", principal_id="u-1")

    assert response.available is False
    assert response.fallback_reason == "invocation_failed: NotFoundError"
    assert "NotFoundError" in response.finding.summary
    assert "provider call failed" in response.finding.summary
    assert "no API key is configured" not in response.finding.summary
    # A configured provider failed: an investigator must review.
    assert response.finding.recommended_review is True


def test_unavailable_summary_covers_every_reason_shape():
    for reason, must_contain, must_not_contain in [
        ("no_api_key_for_role_reasoning", "CRIMELINK_AI_REASONING_API_KEY", "failed"),
        ("invocation_failed: NotFoundError", "NotFoundError", "no API key is configured"),
        ("openai_client_unavailable", "openai", "no API key is configured"),
        (None, "unavailable", None),
    ]:
        summary = unavailable_summary("reasoning", reason)
        assert must_contain in summary
        if must_not_contain is not None:
            assert must_not_contain not in summary


# --------------------------------------------------------------------------- #
# Settings: availability is decided only from CRIMELINK_ configuration
# --------------------------------------------------------------------------- #


def test_role_availability_ignores_ambient_legacy_env_keys(monkeypatch, settings):
    """A legacy variable in the process env must not silently enable a role.

    Regression: role resolution used to fall back to a raw ``os.environ`` peek
    of ``NVIDIA_API_KEY``, so an ambient variable could flip a role to
    "available", trigger a real provider invocation, and present the failure
    as a confusing NotFoundError even though no CrimeLink key was configured.
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "ambient-legacy-key")
    monkeypatch.delenv("CRIMELINK_AI_API_KEY", raising=False)

    blank = settings.model_copy(update={"ai_api_key": None, "ai_reasoning_api_key": None})
    assert blank.ai_role_available("reasoning") is False
    assert blank.role_config("reasoning")["api_key"] is None


def test_global_key_is_the_fallback_for_roles_without_their_own(settings):
    configured = settings.model_copy(update={"ai_api_key": "global-key"})
    cfg = configured.role_config("reasoning")
    assert cfg["api_key"] == "global-key"
    assert configured.ai_role_available("reasoning") is True

    # An explicit role key wins over the global one.
    both = settings.model_copy(
        update={"ai_api_key": "global-key", "ai_reasoning_api_key": "role-key"}
    )
    assert both.role_config("reasoning")["api_key"] == "role-key"
