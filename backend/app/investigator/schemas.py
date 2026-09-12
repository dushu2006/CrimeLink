"""Strict JSON contracts for the investigator reasoning layer.

Every investigation answer is an :class:`InvestigatorResponse`. The shape is
deliberately closed: the language model authors only
:class:`InvestigatorNarrative` (prose over already-computed results), and
every structural claim — entities, relationships, patterns, hypotheses,
gaps, next steps — is computed deterministically by the orchestrator.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProvenanceItem(BaseModel):
    """One openable pointer behind a claim.

    Conventional ``kind`` values: ``document`` (an ingested file),
    ``source_row`` (a row inside a source file), ``graph_edge`` (an edge key),
    ``metric`` (a computed analytic, e.g. ``centrality:betweenness``),
    ``audit`` (an audit-log row), ``note`` (investigator-supplied context),
    ``dataset`` (a record whose origin is the dataset itself, not a file).
    ``ref`` is the stable identifier; ``label`` is what a human reads.
    """

    kind: str = Field(..., description="Pointer vocabulary, see class docstring.")
    ref: str = Field(..., description="Stable reference: doc id, edge key, metric name, audit id.")
    label: str = Field(..., description="Human label, e.g. 'cdr.csv · row 18342'.")
    detail: str | None = None
    doc_id: str | None = None
    origin_file: str | None = None
    row_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    content_hash: str | None = None


class EvidenceItem(BaseModel):
    """A single evidence for or against a claim, always provenanced."""

    kind: Literal["document", "record", "relationship", "metric", "note"]
    summary: str
    inference_label: str = "FACT"
    stance: Literal["supports", "contradicts", "context"] = "supports"
    provenance: list[ProvenanceItem] = Field(default_factory=list)


class ResolvedEntity(BaseModel):
    """A question mention resolved (or not) to a canonical graph id."""

    canonical_id: str
    label: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    matched_by: str = "name"
    entity_keys: list[str] = Field(default_factory=list)
    criminal_status: str | None = Field(
        default=None,
        description="Echoed from the dataset only; never inferred by the investigator.",
    )
    resolved: bool = True
    ambiguity_note: str | None = None


class RelationshipPath(BaseModel):
    """A concrete route through the graph between two entities."""

    nodes: list[str] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)
    description: str = ""


class ObservationBlock(BaseModel):
    """Observation / interpretation / assessment are never blurred."""

    observation: str = Field(..., description="What the data shows, no judgement.")
    interpretation: str = Field(..., description="What it could mean, with alternatives.")
    assessment: str = Field(..., description="Investigator judgement incl. confidence.")


class RelationshipFinding(BaseModel):
    """One discovered relationship between resolved entities."""

    kind: Literal[
        "direct",
        "indirect",
        "temporal",
        "repeated",
        "cross_case",
        "suspicious",
        "coincidental",
    ]
    entities: list[str] = Field(default_factory=list)
    title: str
    description: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    inference_label: str = "LEAD"
    provenance: list[ProvenanceItem] = Field(
        default_factory=list,
        description="Flat pointers rolled up from the evidence, so each edge is openable.",
    )
    path: RelationshipPath | None = None
    analysis: ObservationBlock | None = None


class StrengthFactors(BaseModel):
    """The auditable inputs behind a strength rating."""

    independent_sources: int = 0
    corroborating_records: int = 0
    temporal_relevance: str = "unknown"
    directness: str = "unknown"
    consistency: str = "unknown"
    contradiction_level: str = "none"
    entity_certainty: str = "resolved"
    notes: list[str] = Field(default_factory=list)


class SuspiciousPattern(BaseModel):
    """One deterministic detector firing (or a transparently set-aside decoy)."""

    kind: str
    title: str
    explanation: str
    entities: list[str] = Field(default_factory=list)
    entity_keys: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    time_range: dict[str, str | None] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    inference_label: str = "LEAD"
    strength: str = "WEAK"
    strength_factors: StrengthFactors = Field(default_factory=StrengthFactors)
    contradictions_considered: list[str] = Field(default_factory=list)
    innocent_alternatives: list[str] = Field(default_factory=list)
    excluded: bool = False
    exclusion_reason: str | None = None
    provenance: list[ProvenanceItem] = Field(default_factory=list)


class Hypothesis(BaseModel):
    """A testable reading of the evidence, with both sides attached."""

    id: str
    statement: str
    entities: list[str] = Field(default_factory=list)
    supporting: list[EvidenceItem] = Field(default_factory=list)
    contradicting: list[EvidenceItem] = Field(default_factory=list)
    innocent_alternatives: list[str] = Field(default_factory=list)
    inference_label: str = "HYPOTHESIS"
    strength: str = "INSUFFICIENT"
    strength_factors: StrengthFactors = Field(default_factory=StrengthFactors)
    provenance: list[ProvenanceItem] = Field(default_factory=list)
    analysis: ObservationBlock | None = None


class DataGap(BaseModel):
    """Missing evidence, reported honestly instead of filled in."""

    category: str
    description: str
    what_would_help: str
    inference_label: str = "DATA_GAP"
    entities: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)


class NextStep(BaseModel):
    """A lawful, identity-first follow-up recommendation."""

    action: str
    rationale: str
    priority: Literal["high", "medium", "low"] = "medium"
    links: dict[str, list[str]] = Field(default_factory=dict)
    provenance: list[ProvenanceItem] = Field(
        default_factory=list,
        description=(
            "Pointers behind the finding this step follows up. Gap-closing steps "
            "cite nothing on purpose: the record they ask for is not in the data."
        ),
    )


class ModelSection(BaseModel):
    """What the language model contributed — or why it contributed nothing."""

    available: bool
    role: str = "investigation_reasoning"
    model: str | None = None
    reason: str | None = None
    summary: str = ""
    observation: str = ""
    interpretation: str = ""
    assessment: str = ""
    convergence_note: str = ""
    caveats: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    language_edits: list[str] = Field(default_factory=list)


class AssessmentSection(BaseModel):
    """Overall reading: deterministic judgement plus the model's narrative."""

    overall_strength: str = "INSUFFICIENT"
    overall_confidence: float = 0.0
    convergence: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    interpretation: str = ""
    assessment: str = ""
    caveats: list[str] = Field(default_factory=list)
    model: ModelSection = Field(default_factory=ModelSection)


class MemorySection(BaseModel):
    """The thread's memory as returned with an answer."""

    investigation_id: str
    #: The thread's own objective — what this investigation set out to establish.
    objective: str = ""
    questions_asked: int = 0
    prior_questions: list[str] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    open_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    examined_entities: list[str] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    #: What earlier turns in this thread established and what they ruled out —
    #: continuity means remembering the negative results too.
    contradictions: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    rejected_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    prior_findings: list[str] = Field(default_factory=list)


class ScopeSection(BaseModel):
    """What the investigation was allowed to look at.

    ``label`` is the human reading of the scope ("Case C106", "Master Network")
    so every surface renders the same scope wording and no page has to invent
    its own. ``case_number``/``case_title`` are echoed from the case row when a
    single case is in scope — never synthesised.
    """

    mode: Literal["case", "master"]
    label: str = "Master Network"
    dataset_id: str | None = None
    dataset_name: str | None = None
    case_id: str | None = None
    case_number: str | None = None
    case_title: str | None = None
    case_ids: list[str] = Field(default_factory=list)
    nodes_considered: int = 0
    edges_considered: int = 0
    documents_considered: int = 0


class InvestigatorResponse(BaseModel):
    """The complete, structured answer to an investigation question.

    Ordering mirrors the investigative flow the workspace renders:
    objective → facts → relationships → patterns → hypotheses →
    supporting/contradictory evidence → alternatives → assessment →
    gaps → next direction → provenance.
    """

    question: str
    objective: str = Field(
        default="",
        description=(
            "What this investigation is trying to establish. Stable across the "
            "thread's follow-up questions, so continuity is visible."
        ),
    )
    investigation_id: str
    scope: ScopeSection
    entities: list[ResolvedEntity] = Field(default_factory=list)
    facts: list[EvidenceItem] = Field(
        default_factory=list,
        description=(
            "Deterministic FACT-stance observations. These are what the records "
            "directly establish — never a model's reading."
        ),
    )
    relationships: list[RelationshipFinding] = Field(default_factory=list)
    patterns: list[SuspiciousPattern] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(
        default_factory=list,
        description=(
            "Reasonable non-criminal readings collected from the hypotheses and "
            "detected patterns. Grounded in the record types present, not "
            "invented to reassure."
        ),
    )
    assessment: AssessmentSection = Field(default_factory=AssessmentSection)
    gaps: list[DataGap] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    focused_graph: dict[str, Any] = Field(default_factory=dict)
    provenance: list[ProvenanceItem] = Field(default_factory=list)
    memory: MemorySection | None = None
    timing_ms: dict[str, int] = Field(default_factory=dict)


class InvestigateRequest(BaseModel):
    """Question plus explicit scope; nothing is ever inferred about scope."""

    question: str = Field(..., min_length=3, max_length=2000)
    case_id: str | None = Field(
        default=None, description="Case scope; omit for master-network scope."
    )
    investigation_id: str | None = Field(
        default=None, description="Continue a previous investigation (memory)."
    )
    objective: str | None = Field(
        default=None,
        max_length=400,
        description=(
            "Optional investigator-stated objective. Omit to let the first "
            "question of the thread set it."
        ),
    )
    max_patterns: int = Field(default=25, ge=1, le=100)
    include_excluded: bool = Field(
        default=True, description="Also return decoy/coincidence exclusions for transparency."
    )


class InvestigatorNarrative(BaseModel):
    """The only JSON a model may author: narrative over deterministic results.

    The orchestrator computes every structural claim; the model explains the
    already-computed findings in investigator language.  Unknown fields are
    ignored and missing fields default, so a sloppy model degrades to an
    empty narrative instead of breaking the investigation.
    """

    summary: str = ""
    observation: str = ""
    interpretation: str = ""
    assessment: str = ""
    convergence_note: str = ""
    caveats: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}
