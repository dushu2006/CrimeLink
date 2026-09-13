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
    case_id: str | None = None
    source_id: str | None = None
    origin_file: str | None = None
    document_type: str | None = None
    source_type: str | None = None
    record_id: str | None = None
    row_number: int | None = None
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    text_span: list[int] | None = None
    excerpt: str | None = None
    content_hash: str | None = None


class EvidenceItem(BaseModel):
    """A single evidence for or against a claim, always provenanced.

    Enhanced with full provenance contract: evidence_id, document_id, case_id,
    source_id, origin_file, document_type, source_type, record_id, row_number,
    page_number, etc. Fields are only populated when source data exists — never
    fabricated.
    """

    kind: Literal["document", "record", "relationship", "metric", "note"]
    summary: str
    inference_label: str = "FACT"
    stance: Literal["supports", "contradicts", "context"] = "supports"
    provenance: list[ProvenanceItem] = Field(default_factory=list)
    # Enhanced contract fields
    evidence_id: str | None = None
    document_id: str | None = None
    case_id: str | None = None
    source_id: str | None = None
    origin_file: str | None = None
    document_type: str | None = None
    source_type: str | None = None
    record_id: str | None = None
    row_number: int | None = None
    page_number: int | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    confidence: float | None = None
    source_confidence: str | None = None


class AnalyticalBasis(BaseModel):
    """Deterministic graph-analytical signals behind a finding."""

    degree_centrality: float | None = None
    weighted_degree: float | None = None
    betweenness_centrality: float | None = None
    pagerank: float | None = None
    community_id: int | str | None = None
    community_size: int | None = None
    cross_case_count: int | None = None
    relationship_count: int | None = None
    evidence_count: int | None = None
    source_count: int | None = None
    temporal_relevance: str | None = None
    evidence_convergence: str | None = None
    relationship_strength: str | None = None
    bridge_info: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    explanations: dict[str, str] = Field(
        default_factory=dict,
        description="Human explanation for each metric, e.g. 'High betweenness indicates ...'",
    )


class InvestigativeRelevanceAssessment(BaseModel):
    """Transparent investigative relevance — never guilt probability."""

    relevance: str = "LOW"
    score: float | None = None
    basis: list[str] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class EvidenceStrengthAssessment(BaseModel):
    """Transparent evidence-strength assessment."""

    strength: str = "INSUFFICIENT"
    basis: list[str] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""


class EvidenceConvergenceAssessment(BaseModel):
    convergence_type: str = "NONE"
    source_categories: list[str] = Field(default_factory=list)
    independent_source_count: int = 0
    record_count: int = 0
    explanation: str = ""


class ResolvedEntity(BaseModel):
    """A question mention resolved (or not) to a canonical graph id.

    Enhanced to explicitly separate legal/source status, network role,
    and investigative relevance — never conflating graph centrality with
    criminality.
    """

    canonical_id: str
    label: str
    display_name: str
    entity_type: str = Field(default="PERSON", description="Explicit entity type: PERSON, PHONE, etc.")
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    matched_by: str = "name"
    entity_keys: list[str] = Field(default_factory=list)
    # Legacy field kept for backward compat
    criminal_status: str | None = Field(
        default=None,
        description="Echoed from the dataset only; never inferred by the investigator.",
    )
    # New explicit separation
    legal_status: str | None = Field(
        default=None, description="Source-derived legal status: VICTIM, WITNESS, ACCUSED, CONVICTED, etc."
    )
    network_role: str | None = Field(
        default=None, description="Analytical network role: HUB, BRIDGE, CROSS_CASE_BRIDGE, etc."
    )
    investigative_relevance: str | None = Field(
        default=None, description="LOW, MODERATE, HIGH, CRITICAL_REVIEW — never guilt probability"
    )
    evidence_strength: str | None = None
    resolution_status: str = "RESOLVED"
    case_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    analytical_basis: AnalyticalBasis | None = None
    resolved: bool = True
    ambiguity_note: str | None = None


class RelationshipPath(BaseModel):
    """A concrete route through the graph between two entities."""

    nodes: list[str] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)
    description: str = ""
    why: str | None = None


class ObservationBlock(BaseModel):
    """Observation / interpretation / assessment are never blurred."""

    observation: str = Field(..., description="What the data shows, no judgement.")
    interpretation: str = Field(..., description="What it could mean, with alternatives.")
    assessment: str = Field(..., description="Investigator judgement incl. confidence.")


class FocusedGraphEdge(BaseModel):
    """Edge in focused evidence graph with explicit WHY."""

    source: str
    target: str
    rel_type: str
    why: str = ""
    source_doc_id: str | None = None
    source_doc_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    date_time: str | None = None
    inference_label: str = "FACT"
    confidence: float = 1.0
    reason: str = ""
    provenance: list[ProvenanceItem] = Field(default_factory=list)


class FocusedGraphNode(BaseModel):
    key: str
    label: str
    name: str
    entity_type: str = "UNKNOWN"
    canonical_id: str | None = None
    focus: bool = False
    is_criminal: bool = False
    criminal_status: str | None = None
    legal_status: str | None = None
    network_role: str | None = None
    case_ids: list[str] = Field(default_factory=list)
    analytical_basis: AnalyticalBasis | None = None


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
    analytical_basis: AnalyticalBasis | None = None
    why: str | None = None
    #: Observed relationship (STRONG/MODERATE/WEAK/INSUFFICIENT): how much
    #: contact the records actually show. Kept separate from evidence
    #: confidence on purpose — a pair can show MODERATE contact on HIGH
    #: evidence, or STRONG contact on a single weak record.
    relationship_strength: str | None = None
    #: Evidentiary confidence band (HIGH/MODERATE/LOW/INSUFFICIENT) derived
    #: from edge confidence (>=0.85 confirmed, >=0.65 corroborated, >=0.40
    #: single-source, else weak). Never a guilt probability.
    evidence_strength: str | None = None
    investigative_relevance: str | None = None


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
    """One deterministic detector firing (or a transparently set-aside decoy).

    Enhanced with analytical_basis, evidence convergence, and explicit
    disclaimer that suspicious != criminal.
    """

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
    # New fields
    analytical_basis: AnalyticalBasis | None = None
    #: Deterministic "why was this surfaced?" — the mechanism, never a verdict.
    why: str | None = None
    evidence_convergence: EvidenceConvergenceAssessment | None = None
    evidence_strength: EvidenceStrengthAssessment | None = None
    investigative_relevance: InvestigativeRelevanceAssessment | None = None
    pattern_type: str | None = None
    evidence_count: int | None = None
    source_count: int | None = None
    disclaimer: str = "Investigative signal — prioritises where to look, and is not a criminal status."


class StructuredFinding(BaseModel):
    """Internal structured finding contract as per spec section 14."""

    finding_id: str
    title: str
    finding_type: str
    objective: str = ""
    #: Deterministic answer to "why was this surfaced?" — the mechanism, not
    #: a verdict.
    why: str = ""
    entities: list[ResolvedEntity] = Field(default_factory=list)
    analytical_basis: AnalyticalBasis | None = None
    relationships: list[RelationshipFinding] = Field(default_factory=list)
    patterns: list[SuspiciousPattern] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradictory_evidence: list[EvidenceItem] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    assessment: dict[str, Any] = Field(default_factory=dict)
    data_gaps: list[Any] = Field(default_factory=list)
    focused_graph: dict[str, Any] = Field(default_factory=dict)
    next_investigative_direction: str = ""


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
    analytical_basis: AnalyticalBasis | None = None
    evidence_strength: EvidenceStrengthAssessment | None = None
    investigative_relevance: InvestigativeRelevanceAssessment | None = None
    evidence_convergence: EvidenceConvergenceAssessment | None = None


class DataGap(BaseModel):
    """Missing evidence, reported honestly instead of filled in."""

    category: str
    description: str
    what_would_help: str
    inference_label: str = "DATA_GAP"
    entities: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    severity: str | None = None
    source_types_missing: list[str] = Field(default_factory=list)


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
    why: str | None = None
    evidence_required: list[str] = Field(default_factory=list)


class ModelSection(BaseModel):
    """What the language model contributed — or why it contributed nothing.

    Strictly prose-only: the model authors summary/observation/interpretation/
    assessment/convergence_note/caveats/suggested_next_actions. It never carries
    evidence objects, provenance, relationships, hypotheses or criminal_status.
    This field set is pinned by test_the_narrative_contract_can_carry_no_evidence_of_its_own.
    """

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


class DataQualityItem(BaseModel):
    category: str
    description: str
    severity: str = "INFO"
    affected_entities: list[str] = Field(default_factory=list)
    affected_cases: list[str] = Field(default_factory=list)
    recommendation: str = ""


class SilentIntermediaryFinding(BaseModel):
    """Potential network intermediary — low visibility but structurally important."""

    entity_id: str
    display_name: str
    entity_type: str = "PERSON"
    why_surfaced: str = ""
    analytical_basis: AnalyticalBasis = Field(default_factory=AnalyticalBasis)
    network_role: str = "POTENTIAL_NETWORK_INTERMEDIARY"
    investigative_relevance: str = "HIGH"
    evidence_strength: str = "MODERATE"
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    community_bridges: list[int] = Field(default_factory=list)
    cross_case_bridges: list[str] = Field(default_factory=list)
    disclaimer: str = "Network position does not establish criminal involvement."


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
    investigative_relevance: InvestigativeRelevanceAssessment | None = None
    evidence_strength: EvidenceStrengthAssessment | None = None
    evidence_convergence: EvidenceConvergenceAssessment | None = None
    analytical_basis_summary: dict[str, Any] = Field(default_factory=dict)


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

    mode: Literal["case", "master", "person"]
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
    Enhanced with analytical_basis, investigative_relevance, evidence
    convergence, structured findings, silent intermediary analysis, data quality.
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
    # New enhanced fields
    structured_findings: list[StructuredFinding] = Field(default_factory=list)
    analytical_basis: dict[str, Any] = Field(default_factory=dict)
    investigative_relevance: InvestigativeRelevanceAssessment | None = None
    evidence_strength: EvidenceStrengthAssessment | None = None
    evidence_convergence: EvidenceConvergenceAssessment | None = None
    silent_intermediaries: list[SilentIntermediaryFinding] = Field(default_factory=list)
    data_quality: list[DataQualityItem] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)


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
