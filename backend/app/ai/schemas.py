"""Pydantic contracts for AI output (§24).

AI output is always structured JSON validated against these models.  The
gateway rejects malformed or non-conforming responses — free-form text never
becomes authoritative state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AIEntityRef(BaseModel):
    pseudo_id: str = Field(..., description="Pseudonymous ID (e.g. PERSON_023)")
    label: str | None = None


class EvidenceRef(BaseModel):
    doc_id: str
    description: str | None = None
    text_span: list[int] | None = None


class ReasoningStep(BaseModel):
    step: int
    statement: str
    evidence_level: Literal["FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"] = "UNKNOWN"
    evidence_refs: list[str] = Field(default_factory=list)


class FindingResult(BaseModel):
    """Structured output contract for any AI finding."""

    finding_type: str = "GENERAL"
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_level: Literal["FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"] = "UNKNOWN"
    entities: list[AIEntityRef] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_review: bool = True
    suggested_next_actions: list[str] = Field(default_factory=list)

    @field_validator("evidence_refs")
    @classmethod
    def _require_evidence_for_fact(cls, v, info):
        level = info.data.get("evidence_level")
        if level == "FACT" and not v:
            raise ValueError("FACT-level findings must reference supporting evidence.")
        return v


class AIResponse(BaseModel):
    """Top-level envelope returned by the gateway."""

    query_id: str
    role: str
    model: str | None = None
    finding: FindingResult
    latency_ms: int = 0
    pseudonymized: bool = True
    available: bool = True
    fallback_reason: str | None = None
