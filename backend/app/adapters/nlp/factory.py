"""NLP provider selection with graceful degradation.

``auto`` resolution order:

1. ``nim``       — if a NIM API key is configured (internet-connected deployments).
2. ``indicner``  — if local model weights load (air-gapped deployments).
3. ``heuristic`` — always available; conservative, dependency-free.

Whichever provider is selected, the returned object is wrapped so that a runtime
failure (endpoint down, weights missing, CUDA OOM) falls back to the heuristic
extractor instead of quarantining the document.  A document must never fail to
process because a model is unavailable — that is principle P4, "no silent
failure", applied to the model tier.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate
from app.logging import get_logger

log = get_logger("crimelink.nlp.factory")


class ResilientNLPProvider:
    """Primary provider with an automatic fallback to the heuristic extractor."""

    def __init__(self, primary: Any | None, fallback: Any, settings: Settings) -> None:
        self.primary = primary
        self.fallback = fallback
        self.settings = settings
        self.name = primary.name if primary is not None else fallback.name
        self.supports_languages = getattr(
            primary, "supports_languages", fallback.supports_languages
        )
        self.last_fallback_reason: str | None = None

    def extract(
        self, doc: NormalizedDocument
    ) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
        cap = float(self.settings.nlp_max_confidence)
        if self.primary is not None:
            try:
                entities, relations = self.primary.extract(doc)
                return _cap(entities, cap), _cap_relations(relations, cap)
            except Exception as exc:
                self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "nlp.primary_failed_falling_back",
                    provider=getattr(self.primary, "name", "?"),
                    doc_id=doc.doc_id,
                    error=str(exc),
                )
        entities, relations = self.fallback.extract(doc)
        return _cap(entities, cap), _cap_relations(relations, cap)


def _cap(items: list[ExtractionCandidate], cap: float) -> list[ExtractionCandidate]:
    for item in items:
        item.confidence = round(min(float(item.confidence), cap), 3)
    return items


def _cap_relations(items: list[RelationCandidate], cap: float) -> list[RelationCandidate]:
    for item in items:
        item.confidence = round(min(float(item.confidence), cap), 3)
    return items


def build_nlp_provider(settings: Settings | None = None) -> ResilientNLPProvider:
    settings = settings or get_settings()
    from app.adapters.nlp.heuristic import HeuristicNLPProvider

    fallback = HeuristicNLPProvider(settings)
    choice = settings.nlp_provider

    if choice in ("auto", "nim"):
        if settings.nim_api_key:
            try:
                from app.adapters.nlp.nim import NIMNLPProvider

                return ResilientNLPProvider(NIMNLPProvider(settings), fallback, settings)
            except Exception as exc:
                log.warning("nlp.nim_unavailable", error=str(exc))
        if choice == "nim":
            log.warning("nlp.nim_requested_but_unavailable")

    if choice in ("auto", "indicner"):
        try:
            from app.adapters.nlp.indicner import IndicNERProvider

            return ResilientNLPProvider(IndicNERProvider(settings), fallback, settings)
        except Exception as exc:
            log.info("nlp.indicner_unavailable", error=str(exc))

    log.info("nlp.provider_selected", provider=fallback.name)
    return ResilientNLPProvider(None, fallback, settings)
