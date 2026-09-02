"""Dependency-free probabilistic extractor (the offline fallback).

This provider exists because CrimeLink must never *hallucinate* its way through
a missing model.  When no NVIDIA NIM endpoint and no local IndicNER weights are
available, extraction degrades to "fewer, conservative extractions" instead of
"invented entities" — which is exactly the failure mode the Phase-0 NLP gate in
the PRD is designed to prevent.

It is deliberately conservative:

* a span is only proposed as a person if it is name-shaped **and** survives a
  stop-word/gazetteer filter;
* confidence is assembled from small, explainable increments (known Indian
  given name, honorific or role prefix, adjacency to a relation cue);
* every confidence is capped at ``nlp_max_confidence`` (0.8) because a
  probabilistic extraction is never graded equal to a regex hit (PRD 8.2).
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings, get_settings
from app.domain.enums import EntityType, SourceConfidence
from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate
from app.domain.normalize import (
    display_name,
    is_probable_person_name,
    normalize_name,
    normalize_organization,
)
from app.logging import get_logger
from app.pipeline.extraction.gazetteers import (
    COMMON_INDIAN_FIRST_NAMES,
    DISTRICTS,
    EXTRA_ENGLISH_STOPWORDS,
    HINDI_ROLE_TOKENS,
    HINDI_STOPWORDS,
    ORG_NAME_TOKENS,
    ORG_SUFFIXES,
    PERSON_STOPWORDS,
    RELATION_CUES,
    RELATION_CUES_HI,
    STATE_NAMES,
    VEHICLE_TOKENS,
)

log = get_logger("crimelink.nlp.heuristic")

_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,3})\b")
_DEVANAGARI_NAME_RE = re.compile(r"([\u0900-\u0963][\u0900-\u097F]{1,}(?:\s+[\u0900-\u0963][\u0900-\u097F]{1,}){0,3})")
_SENTENCE_RE = re.compile(r"[^।.!?\n]+[।.!?\n]*")
_ALIAS_RE = re.compile(
    r"\b(?:alias|aka|a\.k\.a\.|otherwise known as|@)\s+([\u0900-\u097FA-Z][\w.'\-\u0900-\u097F]+(?:\s+[\u0900-\u097FA-Z][\w.'\-\u0900-\u097F]+){0,3})",
    re.IGNORECASE,
)
_ALIAS_HI_RE = re.compile(r"(?:उर्फ|ऊर्फ)\s+([\u0900-\u0963][\u0900-\u097F]{1,}(?:\s+[\u0900-\u0963][\u0900-\u097F]{1,}){0,3})")
_ROLE_PREFIX_RE = re.compile(
    r"\b(accused|suspect|complainant|witness|victim|deceased|shri|smt|sri|mr|mrs|ms|dr)\b[:\s]+",
    re.IGNORECASE,
)

# A name is at most three tokens long.  Indian names are usually two; three
# covers "Vikram Singh Rathore".  Longer spans in free text are almost always a
# sentence fragment rather than a name.
_MAX_NAME_TOKENS = 3
_TOKEN_RE = re.compile(r"[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\-']*")
_DEVANAGARI_RE = re.compile(r"^[\u0900-\u097F]")
_LATIN_ROLE_TOKENS = frozenset({"shri", "shrimati", "smt", "sri", "mr", "mrs", "ms", "dr", "late"})


def _classify_token(token: str) -> str:
    """Grade one whitespace token as ``name``, ``role`` (title) or ``stop``.

    The offline extractor trades recall for precision: an unknown word in a
    name-shaped position is accepted, but anything that is a known place,
    organisation, vehicle, title or function word is not.  A wrong person node
    is far more expensive for an investigator than a missed mention, because
    every node is *evidenced* and therefore looks trustworthy.
    """
    if _DEVANAGARI_RE.match(token):
        if token in HINDI_ROLE_TOKENS:
            return "role"
        if token in HINDI_STOPWORDS:
            return "stop"
        # Single-syllable Devanagari fragments (एम, आई, बी …) are transliterated
        # initials, not names.
        if len(token) < 3:
            return "stop"
        return "name"
    if not re.match(r"^[A-Z][a-z]{2,}$", token):
        return "stop"
    lowered = token.lower().rstrip(".")
    if token.lower() in _LATIN_ROLE_TOKENS:
        return "role"
    if lowered in PERSON_STOPWORDS or lowered in EXTRA_ENGLISH_STOPWORDS:
        return "stop"
    if lowered in STATE_NAMES or lowered in DISTRICTS:
        return "stop"
    if lowered in ORG_NAME_TOKENS or lowered in ORG_SUFFIXES or lowered in VEHICLE_TOKENS:
        return "stop"
    return "name"


class HeuristicNLPProvider:
    name = "heuristic"
    supports_languages = frozenset({"en", "hi", "mr", "ta", "te", "bn", "unknown"})

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ API
    def extract(
        self, doc: NormalizedDocument
    ) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
        cap = float(self.settings.nlp_max_confidence)
        staging = doc.source_confidence == SourceConfidence.ANONYMOUS_TIP
        entities: dict[str, ExtractionCandidate] = {}
        relations: list[RelationCandidate] = []

        for block in doc.blocks:
            if block.kind == "record":
                continue
            text = block.text or ""
            if not text.strip():
                continue
            for sentence_match in _SENTENCE_RE.finditer(text):
                sentence = sentence_match.group(0)
                offset = block.offset + sentence_match.start()
                found = self._persons_in_sentence(sentence, doc, offset, cap, staging)
                for candidate in found:
                    key = candidate.normalized_value
                    existing = entities.get(key)
                    if existing is None or candidate.confidence > existing.confidence:
                        entities[key] = candidate
                relations.extend(
                    self._relations_in_sentence(sentence, found, doc, offset, cap, staging)
                )

        self._attach_aliases(doc, entities)
        log.info(
            "nlp.heuristic.done",
            doc_id=doc.doc_id,
            persons=len(entities),
            relations=len(relations),
        )
        return list(entities.values()), relations

    # --------------------------------------------------------------- persons
    def _persons_in_sentence(
        self,
        sentence: str,
        doc: NormalizedDocument,
        offset: int,
        cap: float,
        staging: bool,
    ) -> list[ExtractionCandidate]:
        out: list[ExtractionCandidate] = []
        seen: set[str] = set()
        lowered = sentence.lower()
        cue_boost = any(
            re.search(pattern, lowered) for pattern, _, _ in RELATION_CUES
        ) or any(re.search(pattern, sentence) for pattern, _, _ in RELATION_CUES_HI)

        # Slide a window of name-shaped tokens over the sentence.  Starting the
        # next window at i+1 (rather than at the end of the accepted span) is
        # what lets "बजे आरोपी रमेश यादव" still yield "रमेश यादव" instead of the
        # leading verb.
        tokens = [(m.start(), m.end(), m.group(0)) for m in _TOKEN_RE.finditer(sentence)]
        kinds = [_classify_token(t[2]) for t in tokens]
        spans: list[tuple[int, int]] = []
        for i in range(len(tokens)):
            if kinds[i] != "name":
                continue
            run: list[int] = []
            j = i
            while j < len(tokens) and kinds[j] == "name" and len(run) < _MAX_NAME_TOKENS:
                run.append(j)
                j += 1
            if len(run) >= 2:  # a single token is never specific enough
                spans.append((run[0], run[-1]))
        # Keep only maximal spans: drop "रमेश यादव" if "राम प्रसाद यादव" was also
        # accepted, so one person produces one node.
        spans = [
            (a, b)
            for (a, b) in spans
            if not any((x <= a and y >= b and (x, y) != (a, b)) for (x, y) in spans)
        ]

        for first, last in spans:
            start = tokens[first][0]
            end = tokens[last][1]
            raw = " ".join(tokens[idx][2] for idx in range(first, last + 1))
            name = display_name(raw)
            if not is_probable_person_name(name):
                continue
            devanagari = bool(_DEVANAGARI_RE.match(name))
            words = name.split()
            lowered_words = [w.lower() for w in words]
            if devanagari:
                score = 0.55
            else:
                score = 0.5
                if lowered_words[0] in COMMON_INDIAN_FIRST_NAMES:
                    score += 0.15
                if 2 <= len(words) <= 3:
                    score += 0.05
            # A title immediately before the span ("आरोपी रमेश यादव",
            # "Shri Ram Prasad") is the strongest evidence that it is a person.
            if first > 0 and kinds[first - 1] == "role":
                score += 0.1
            elif _ROLE_PREFIX_RE.search(sentence[:start]):
                score += 0.05
            if cue_boost:
                score += 0.05
            score = round(min(score, cap), 3)

            norm = normalize_name(name)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(
                ExtractionCandidate(
                    entity_type=EntityType.PERSON,
                    normalized_value=norm,
                    display_value=name,
                    attributes={"name": name, "source_type": doc.doc_type.lower()},
                    confidence=score,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=(offset + start, offset + end),
                    language=doc.language,
                    extractor="nlp",
                    staging=staging,
                )
            )
        return out

    # ------------------------------------------------------------- relations
    def _relations_in_sentence(
        self,
        sentence: str,
        persons: list[ExtractionCandidate],
        doc: NormalizedDocument,
        offset: int,
        cap: float,
        staging: bool,
    ) -> list[RelationCandidate]:
        if len(persons) < 2:
            return []
        lowered = sentence.lower()
        relation: str | None = None
        confidence = 0.0
        for pattern, rel, score in RELATION_CUES:
            if re.search(pattern, lowered):
                relation, confidence = rel, score
                break
        if relation is None:
            for pattern, rel, score in RELATION_CUES_HI:
                if re.search(pattern, sentence):
                    relation, confidence = rel, score
                    break
        if relation is None:
            return []

        rel_type = _RELATION_TO_EDGE.get(relation)
        if rel_type is None:
            return []
        out: list[RelationCandidate] = []
        anchor = persons[0]
        for other in persons[1:]:
            out.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=anchor.normalized_value,
                    target_type=EntityType.PERSON,
                    target_value=other.normalized_value,
                    rel_type=rel_type,
                    confidence=round(min(confidence, cap), 3),
                    attributes={
                        "cue": relation,
                        "sentence": sentence.strip()[:300],
                        "source_type": doc.doc_type.lower(),
                    },
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=(offset, offset + len(sentence)),
                    extractor="nlp",
                    staging=staging,
                )
            )
        return out

    # ---------------------------------------------------------------- aliases
    def _attach_aliases(
        self, doc: NormalizedDocument, entities: dict[str, ExtractionCandidate]
    ) -> None:
        """Attach 'alias / aka / उर्फ' mentions to a nearby person."""
        text = doc.text
        for match in list(_ALIAS_RE.finditer(text)) + list(_ALIAS_HI_RE.finditer(text)):
            alias = display_name(match.group(1))
            if not alias or not is_probable_person_name(alias):
                continue
            # The alias belongs to the nearest extracted person in the document.
            best: ExtractionCandidate | None = None
            best_distance = 10_000
            for candidate in entities.values():
                span_start = candidate.text_span[0]
                distance = abs(span_start - match.start())
                if distance < best_distance:
                    best, best_distance = candidate, distance
            if best is not None and best_distance < 500:
                aliases = list(best.attributes.get("aliases") or [])
                if alias not in aliases:
                    aliases.append(alias)
                best.attributes["aliases"] = aliases


_RELATION_TO_EDGE: dict[str, str] = {
    "associate_of": "ASSOCIATE_OF",
    "relative_of": "RELATIVE_OF",
    "arrested_with": "ARRESTED_WITH",
    "named_accomplice_of": "NAMED_ACCOMPLICE_OF",
    "member_of": "MEMBER_OF",
    "alias": "ASSOCIATE_OF",
}
