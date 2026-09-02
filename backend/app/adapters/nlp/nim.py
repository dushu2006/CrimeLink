"""NVIDIA NIM provider — OpenAI-compatible LLM extraction (PRD 8.2).

Used when a NIM endpoint and API key are configured.  Advantages for this
deployment: no model weights to ship into an air-gapped image, and one endpoint
covers English plus Hindi and the regional languages.

Extraction discipline
---------------------
The prompt forbids inference, requires the supporting verbatim span for every
item, and demands a self-assessed confidence.  The response is then validated:

* entity/relation types must be in the CrimeLink whitelist;
* confidence is clamped to ``nlp_max_confidence`` (0.8) — a probabilistic
  extraction is never graded equal to a regex hit on the same document;
* an item whose ``span_text`` cannot be located in the source is **dropped**,
  because an orphaned extraction has no evidence pointer, and guarantee G1 means
  an item without evidence is worse than no item at all.

If the endpoint is unreachable the provider raises so the orchestrator can fall
back to the heuristic extractor rather than failing the document.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings, get_settings
from app.domain.enums import EntityType, SourceConfidence
from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate
from app.domain.normalize import display_name, is_probable_person_name, normalize_name
from app.errors import DependencyUnavailableError
from app.logging import get_logger

log = get_logger("crimelink.nlp.nim")

SYSTEM_PROMPT = """You are an information-extraction engine for Indian law-enforcement documents
(FIRs, surveillance notes, intelligence reports). You are used by police investigators who must be
able to verify every extraction against the original text.

Extract ONLY what is explicitly stated in the text. Never guess, never infer, never complete a name
from context. If you are not certain, omit the item — precision matters far more than recall.

Return a single JSON object, no prose, no markdown fences:
{
  "entities": [
    {"type": "Person", "value": "Ramesh Kumar Yadav", "aliases": ["Ramesh Yadav"],
     "confidence": 0.74, "span_text": "verbatim substring from the text"}
  ],
  "relations": [
    {"source": "Ramesh Kumar Yadav", "target": "Suresh Mehta",
     "relation": "associate_of", "confidence": 0.62, "span_text": "verbatim substring"}
  ]
}

Rules:
- "type" is one of: Person, Organization, Location.
- "relation" is one of: associate_of, relative_of, arrested_with, named_accomplice_of, member_of.
- Both endpoints of a relation MUST appear in "entities".
- "span_text" MUST be a verbatim substring of the text you were given.
- "confidence" is a float in [0,1] reflecting how explicit the text is.
- Include Indian names written in Devanagari or in Roman script exactly as written.
- If there is nothing to extract, return {"entities": [], "relations": []}."""

_ALLOWED_TYPES = {"Person", "Organization", "Location"}
_ALLOWED_RELATIONS = {
    "associate_of": "ASSOCIATE_OF",
    "relative_of": "RELATIVE_OF",
    "arrested_with": "ARRESTED_WITH",
    "named_accomplice_of": "NAMED_ACCOMPLICE_OF",
    "member_of": "MEMBER_OF",
}
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class NIMNLPProvider:
    name = "nim"
    supports_languages = frozenset({"en", "hi", "mr", "ta", "te", "bn", "unknown"})

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.nim_api_key:
            raise DependencyUnavailableError("NIM provider selected but no API key configured")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise DependencyUnavailableError("openai client is not installed") from exc
        self._client = OpenAI(
            base_url=self.settings.nim_base_url,
            api_key=self.settings.nim_api_key,
            timeout=self.settings.nim_timeout_s,
        )

    # ------------------------------------------------------------------ API
    def extract(
        self, doc: NormalizedDocument
    ) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
        cap = float(self.settings.nlp_max_confidence)
        staging = doc.source_confidence == SourceConfidence.ANONYMOUS_TIP
        entities: dict[str, ExtractionCandidate] = {}
        relations: list[RelationCandidate] = []
        blocks = [b for b in doc.blocks if b.kind != "record"][
            : self.settings.nim_max_blocks_per_doc
        ]

        for block in blocks:
            text = block.text or ""
            if len(text.strip()) < 20:
                continue
            payload = self._call(text, doc.language)
            if not payload:
                continue
            for raw in payload.get("entities") or []:
                candidate = self._entity_from_raw(raw, block, doc, cap, staging)
                if candidate is None:
                    continue
                key = candidate.normalized_value
                if key not in entities or candidate.confidence > entities[key].confidence:
                    entities[key] = candidate
            for raw in payload.get("relations") or []:
                relation = self._relation_from_raw(raw, entities, block, doc, cap, staging)
                if relation is not None:
                    relations.append(relation)

        log.info(
            "nlp.nim.done",
            doc_id=doc.doc_id,
            model=self.settings.nim_model,
            entities=len(entities),
            relations=len(relations),
        )
        return list(entities.values()), relations

    # --------------------------------------------------------------- internals
    def _call(self, text: str, language: str) -> dict[str, Any] | None:
        user_prompt = (
            f"Document language: {language}\n"
            f"Extract entities and relations from the following text.\n\n"
            f"TEXT:\n{text[: self.settings.nim_max_chars_per_block]}"
        )
        try:
            kwargs: dict[str, Any] = {
                "model": self.settings.nim_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.settings.nim_temperature,
                "top_p": 0.95,
                "max_tokens": self.settings.nim_max_tokens,
                "stream": False,
            }
            if self.settings.nim_disable_thinking:
                kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
            completion = self._client.chat.completions.create(**kwargs)
            content = completion.choices[0].message.content or ""
        except Exception as exc:
            log.warning("nlp.nim.call_failed", error=str(exc))
            raise DependencyUnavailableError("NIM endpoint unavailable.") from exc
        return self._parse(content)

    @staticmethod
    def _parse(content: str) -> dict[str, Any] | None:
        match = _JSON_RE.search(content or "")
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            # Tolerate a trailing-comma / truncated-object response.
            try:
                data = json.loads(match.group(0).rsplit("}", 1)[0] + "}")
            except ValueError:
                log.warning("nlp.nim.unparseable_response")
                return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _locate(haystack: str, needle: str, base: int) -> tuple[int, int] | None:
        if not needle:
            return None
        index = haystack.find(needle)
        if index < 0:
            return None
        return (base + index, base + index + len(needle))

    def _entity_from_raw(
        self,
        raw: dict[str, Any],
        block: Any,
        doc: NormalizedDocument,
        cap: float,
        staging: bool,
    ) -> ExtractionCandidate | None:
        entity_type = str(raw.get("type") or "").strip()
        if entity_type not in _ALLOWED_TYPES:
            return None
        value = display_name(str(raw.get("value") or ""))
        if not value:
            return None
        span = self._locate(block.text or "", str(raw.get("span_text") or value), block.offset)
        if span is None:
            # No verifiable location in the source -> no evidence -> drop it.
            return None
        try:
            confidence = min(float(raw.get("confidence") or 0.6), cap)
        except (TypeError, ValueError):
            confidence = 0.6
        if entity_type == "Person":
            if not is_probable_person_name(value):
                return None
            enum_type = EntityType.PERSON
            normalized = normalize_name(value)
            attributes: dict[str, Any] = {
                "name": value,
                "aliases": [display_name(a) for a in (raw.get("aliases") or []) if a],
                "source_type": doc.doc_type.lower(),
            }
        elif entity_type == "Organization":
            enum_type = EntityType.ORGANIZATION
            normalized = normalize_name(value)
            attributes = {"name": value, "org_type": "UNKNOWN"}
        else:
            enum_type = EntityType.LOCATION
            normalized = value.lower()
            attributes = {"address": value, "location_type": "MENTIONED"}
        return ExtractionCandidate(
            entity_type=enum_type,
            normalized_value=normalized,
            display_value=value,
            attributes=attributes,
            confidence=round(max(0.0, min(confidence, cap)), 3),
            source_doc_id=doc.doc_id,
            case_id=doc.case_id,
            text_span=span,
            language=doc.language,
            extractor="nlp",
            staging=staging,
        )

    def _relation_from_raw(
        self,
        raw: dict[str, Any],
        entities: dict[str, ExtractionCandidate],
        block: Any,
        doc: NormalizedDocument,
        cap: float,
        staging: bool,
    ) -> RelationCandidate | None:
        rel = str(raw.get("relation") or "").strip().lower()
        rel_type = _ALLOWED_RELATIONS.get(rel)
        if rel_type is None:
            return None
        source = normalize_name(str(raw.get("source") or ""))
        target = normalize_name(str(raw.get("target") or ""))
        if not source or not target or source == target:
            return None
        if source not in entities or target not in entities:
            # Both endpoints must be extracted entities, otherwise the relation
            # would point at a node that does not exist in the graph.
            return None
        span = self._locate(
            block.text or "",
            str(raw.get("span_text") or raw.get("source") or ""),
            block.offset,
        )
        if span is None:
            return None
        try:
            confidence = min(float(raw.get("confidence") or 0.55), cap)
        except (TypeError, ValueError):
            confidence = 0.55
        return RelationCandidate(
            source_type=EntityType.PERSON,
            source_value=source,
            target_type=EntityType.PERSON,
            target_value=target,
            rel_type=rel_type,
            confidence=round(max(0.0, min(confidence, cap)), 3),
            attributes={"cue": rel, "source_type": doc.doc_type.lower()},
            source_doc_id=doc.doc_id,
            case_id=doc.case_id,
            text_span=span,
            extractor="nlp",
            staging=staging,
        )
