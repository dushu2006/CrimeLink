"""Ai4Bharat IndicNER provider — local multilingual NER (PRD 8.2).

IndicNER is purpose-built for Indian languages and covers all 22 scheduled
languages, which is why it is the model of choice for Hindi and regional FIR
text; English falls back to XLM-R under the same interface.

This provider needs ``torch`` and ``transformers`` (the ``nlp-local`` extra) and
1–4 GB of weights baked into the ``crimelink-nlp`` image at build time.  It is
therefore only selected when the model is actually loadable — a missing
dependency raises, and the orchestrator falls back to the heuristic extractor
rather than quarantining every document.

Note on the Phase-0 gate (PRD 16): precision must be measured against 20–50 real
Hindi/regional FIRs before this path is trusted in production.  Per-language
confidence thresholds are configured separately because a model's 0.7 in Hindi
is not its 0.7 in English.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.domain.enums import EntityType, SourceConfidence
from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate
from app.domain.normalize import display_name, is_probable_person_name, normalize_name
from app.errors import DependencyUnavailableError
from app.logging import get_logger

log = get_logger("crimelink.nlp.indicner")


class IndicNERProvider:
    name = "indicner"
    supports_languages = frozenset({"hi", "mr", "ta", "te", "bn", "en", "unknown"})

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        try:
            from transformers import pipeline  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise DependencyUnavailableError(
                "transformers/torch not installed — install the nlp-local extra"
            ) from exc
        try:
            self._pipeline = pipeline(
                "ner",
                model=self.settings.indicner_model,
                aggregation_strategy="simple",
                device=-1,  # CPU by default; CUDA is picked up automatically if present
            )
        except Exception as exc:  # pragma: no cover - model weights unavailable
            raise DependencyUnavailableError(f"IndicNER unavailable: {exc}") from exc

    def extract(
        self, doc: NormalizedDocument
    ) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
        cap = float(self.settings.nlp_max_confidence)
        staging = doc.source_confidence == SourceConfidence.ANONYMOUS_TIP
        entities: dict[str, ExtractionCandidate] = {}
        for block in doc.blocks:
            if block.kind == "record":
                continue
            text = block.text or ""
            if len(text.strip()) < 10:
                continue
            try:
                results: list[dict[str, Any]] = self._pipeline(text[:4000])
            except Exception as exc:  # pragma: no cover
                log.warning("nlp.indicner.block_failed", error=str(exc))
                continue
            for item in results:
                label = str(item.get("entity_group") or item.get("entity") or "").upper()
                if label not in ("PER", "PERSON"):
                    continue
                value = display_name(str(item.get("word") or ""))
                if not value or not is_probable_person_name(value):
                    continue
                try:
                    score = float(item.get("score") or 0.0)
                except (TypeError, ValueError):
                    continue
                start = int(item.get("start") or 0)
                end = int(item.get("end") or len(value))
                normalized = normalize_name(value)
                candidate = ExtractionCandidate(
                    entity_type=EntityType.PERSON,
                    normalized_value=normalized,
                    display_value=value,
                    attributes={"name": value, "source_type": doc.doc_type.lower()},
                    confidence=round(min(score, cap), 3),
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=(block.offset + start, block.offset + end),
                    language=doc.language,
                    extractor="nlp",
                    staging=staging,
                )
                if normalized not in entities or candidate.confidence > entities[normalized].confidence:
                    entities[normalized] = candidate
        log.info("nlp.indicner.done", doc_id=doc.doc_id, persons=len(entities))
        # IndicNER is an entity model; relations still come from the cue-based
        # relation stage, which keeps relation evidence explicit and auditable.
        return list(entities.values()), []
