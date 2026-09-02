"""NLP extraction port (pipeline stage 3).

Stage 3 handles what deterministic patterns cannot: person names, organisation
names, aliases and the relationships between people in free text.  Three
providers implement this port:

``nim``       — an OpenAI-compatible NVIDIA NIM endpoint (internet-connected
                deployments with an API key).  Works for English, Hindi and the
                regional languages without downloading a model.
``indicner``  — Ai4Bharat IndicNER / XLM-R running locally (air-gapped
                deployments, GPU optional).
``heuristic`` — a deterministic, dependency-free fallback used when no model is
                available.  It never invents entities: it only promotes
                name-shaped spans that a gazetteer or a documented relation
                cue supports, so the system degrades to "fewer extractions"
                rather than "hallucinated extractions" (PRD 16, phase 0 gate).

Whichever provider runs, its output is capped at ``nlp_max_confidence`` (0.8)
because a probabilistic extraction is never graded equal to a regex match on
the same document (PRD 8.2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate


@runtime_checkable
class NLPProvider(Protocol):
    name: str
    supports_languages: frozenset[str]

    def extract(
        self, doc: NormalizedDocument
    ) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
        """Return entity and relation candidates for a normalised document."""
