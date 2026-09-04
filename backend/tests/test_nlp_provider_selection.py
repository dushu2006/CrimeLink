"""Provider selection must be honest: a configured key really selects NVIDIA.

An operator who sets only the documented legacy ``NVIDIA_API_KEY`` (no
``CRIMELINK_NIM_API_KEY``) must get NIM extraction — never a silent fallback
to the heuristic extractor that pretends nothing is wrong.  These tests pin
down the resolution order and the fallback chain of ``build_nlp_provider``.
"""

from __future__ import annotations

from app.adapters.nlp.factory import build_nlp_provider
from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(
        nlp_provider=overrides.pop("nlp_provider", "auto"),
        **overrides,
    )


def test_auto_without_any_key_uses_heuristic() -> None:
    provider = build_nlp_provider(_settings())
    assert provider.name == "heuristic"
    assert provider.primary is None
    # The public name is what /system/health and AI Activity report, so it must
    # never claim a model provider that is not actually in use.
    assert provider.name != "nim"


def test_auto_with_crimelink_nim_key_selects_nim() -> None:
    provider = build_nlp_provider(_settings(nim_api_key="test-key"))
    assert provider.name == "nim"
    assert provider.primary is not None


def test_auto_with_legacy_nvidia_key_selects_nim(monkeypatch) -> None:
    """The .env.example documented key must enable NIM, not just the gateway."""
    monkeypatch.setenv("NVIDIA_API_KEY", "legacy-test-key")
    provider = build_nlp_provider(_settings())
    assert provider.name == "nim"
    assert provider.primary is not None


def test_explicit_heuristic_never_constructs_nim() -> None:
    provider = build_nlp_provider(_settings(nlp_provider="heuristic"))
    assert provider.name == "heuristic"
    assert provider.primary is None


def test_explicit_nim_without_any_key_falls_back_with_primary_none() -> None:
    provider = build_nlp_provider(_settings(nlp_provider="nim"))
    # NIM was requested but is unavailable: the wrapper keeps the document
    # pipeline alive on the heuristic extractor and reports the honest name.
    assert provider.primary is None
    assert provider.name == "heuristic"
