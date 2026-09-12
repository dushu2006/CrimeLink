/**
 * Presentation and interpretation logic for the investigator workspace.
 *
 * Everything here is pure: it takes the backend's structured answer and turns
 * it into the wording, ordering and groupings the workspace renders. Keeping
 * it out of the components means the honesty rules below are unit-testable
 * without a DOM.
 *
 * The rules that matter:
 *
 *  - A value the backend did not send is never rendered as though it existed
 *    ("—" / silence instead of invention).
 *  - "Suspicious" is an investigative signal, never a criminal status. The
 *    vocabulary used on screen says signal, not verdict.
 *  - Evidence strength is expressed as strength of support, never as a
 *    probability of guilt.
 *  - Network importance (degree, betweenness, PageRank, communities) is
 *    described as a property of the *network*, and is kept visually and
 *    textually separate from criminal status.
 */

import type {
  AssessmentSection,
  DataGap,
  EvidenceItem,
  Hypothesis,
  InferenceLabel,
  InvestigatorResponse,
  NextStep,
  ProvenanceItem,
  ResolvedEntity,
  ScopeSection,
  Strength,
  StrengthFactors,
  SuspiciousPattern,
  TimelineEntry,
} from "../api/client";

/** Tone names understood by `components/Status.tsx`'s badge palette. */
export type Tone = "ok" | "warn" | "bad" | "navy" | "muted" | "busy";

/** Human wording for every inference label — never "criminal". */
const LABEL_TEXT: Record<string, string> = {
  FACT: "Fact",
  CORROBORATED_LEAD: "Corroborated lead",
  LEAD: "Lead",
  HYPOTHESIS: "Hypothesis",
  COINCIDENCE: "Coincidence",
  DATA_GAP: "Data gap",
};

const LABEL_TONE: Record<string, Tone> = {
  FACT: "ok",
  CORROBORATED_LEAD: "navy",
  LEAD: "warn",
  HYPOTHESIS: "busy",
  COINCIDENCE: "muted",
  DATA_GAP: "muted",
};

const STRENGTH_TONE: Record<string, Tone> = {
  STRONG: "ok",
  MODERATE: "navy",
  WEAK: "warn",
  INSUFFICIENT: "muted",
};

/** Readable name of an inference label; unknown labels stay neutral. */
export function labelText(label: string | null | undefined): string {
  if (!label) return LABEL_TEXT.LEAD;
  return LABEL_TEXT[String(label).toUpperCase()] ?? String(label);
}

export function labelTone(label: string | null | undefined): Tone {
  if (!label) return "muted";
  return LABEL_TONE[String(label).toUpperCase()] ?? "muted";
}

export function strengthTone(strength: string | null | undefined): Tone {
  if (!strength) return "muted";
  return STRENGTH_TONE[String(strength).toUpperCase()] ?? "muted";
}

/**
 * Evidence strength in words.
 *
 * Deliberately never a percentage and never a probability of guilt — the
 * backend scores *support*, so the wording says support.
 */
export function strengthSentence(strength: Strength | string | null | undefined): string {
  switch (String(strength ?? "").toUpperCase()) {
    case "STRONG":
      return "Supported by three or more independent evidence streams.";
    case "MODERATE":
      return "Supported by two independent evidence streams.";
    case "WEAK":
      return "Supported by a single evidence stream; treat as a lead.";
    case "INSUFFICIENT":
      return "Not enough evidence to support any reading yet.";
    default:
      return "Evidence strength not reported.";
  }
}

/** "Why is this rated the way it is?" — the auditable factors, in words. */
export function describeStrengthFactors(factors: StrengthFactors | null | undefined): string[] {
  if (!factors) return [];
  const lines: string[] = [];
  const sources = Number(factors.independent_sources ?? 0);
  lines.push(
    sources === 0
      ? "No independent evidence stream recorded yet."
      : sources === 1
        ? "One independent evidence stream."
        : `${sources} independent evidence streams.`,
  );
  if (Number(factors.corroborating_records ?? 0) > 0) {
    lines.push(`${factors.corroborating_records} corroborating record(s).`);
  }
  if (factors.directness && factors.directness !== "unknown") {
    lines.push(`Evidence is ${factors.directness}.`);
  }
  if (factors.temporal_relevance && factors.temporal_relevance !== "unknown") {
    lines.push(`Temporal relevance: ${String(factors.temporal_relevance).replaceAll("_", " ")}.`);
  }
  if (factors.contradiction_level && factors.contradiction_level !== "none") {
    lines.push(`Contradiction level: ${factors.contradiction_level}.`);
  }
  if (factors.entity_certainty && factors.entity_certainty !== "resolved") {
    lines.push(`Entity identity is ${factors.entity_certainty}.`);
  }
  for (const note of factors.notes ?? []) lines.push(note);
  return lines;
}

/* ------------------------------------------------------------------------- */
/* Evidence                                                                  */
/* ------------------------------------------------------------------------- */

export function evidenceByStance(
  items: EvidenceItem[] | null | undefined,
  stance: EvidenceItem["stance"],
): EvidenceItem[] {
  return (items ?? []).filter((item) => item.stance === stance);
}

/** Every supporting item across a set of evidence lists, deduplicated. */
export function mergeEvidence(lists: (EvidenceItem[] | null | undefined)[]): EvidenceItem[] {
  const seen = new Set<string>();
  const out: EvidenceItem[] = [];
  for (const list of lists) {
    for (const item of list ?? []) {
      const key = `${item.stance}|${item.summary}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(item);
    }
  }
  return out;
}

/** One-line pointer text for a provenance item, or null when there is none. */
export function provenanceText(pointer: ProvenanceItem | null | undefined): string | null {
  if (!pointer) return null;
  const file = pointer.origin_file ?? pointer.doc_id;
  if (!file) return pointer.label || pointer.ref || null;
  const name = String(file).split("/").pop() || String(file);
  if (pointer.row_number) return `${name} · row ${pointer.row_number}`;
  if (pointer.line_start) {
    if (pointer.line_end && pointer.line_end !== pointer.line_start) {
      return `${name} · lines ${pointer.line_start}–${pointer.line_end}`;
    }
    return `${name} · line ${pointer.line_start}`;
  }
  return pointer.detail ? `${name} · ${pointer.detail}` : name;
}

/**
 * Where a provenance pointer opens, by what it actually is (§13/§22).
 *
 * A document opens its own page; a row opens the source file at that row; a
 * dataset-level record opens the dataset's file manifest; a graph edge opens the
 * evidence graph; a computed metric opens the analytics surface. Anything the
 * console cannot open is returned as `reference`, which the caller renders as a
 * labelled reference — never as a `/documents/...` link that would dead-end.
 *
 * `caseId` is the case in scope when there is one, because the graph and
 * analytics surfaces are case-scoped.
 */
export type ProvenanceTarget =
  | { kind: "document"; to: string }
  | { kind: "file"; to: string; row?: number | null; lineStart?: number | null; lineEnd?: number | null }
  | { kind: "dataset"; to: string }
  | { kind: "graph"; to: string }
  | { kind: "analytics"; to: string }
  | { kind: "reference"; to: null };

export function provenanceTarget(
  pointer: ProvenanceItem | null | undefined,
  caseId?: string | null,
): ProvenanceTarget {
  if (!pointer) return { kind: "reference", to: null };
  if (pointer.kind === "document") {
    // A document pointer without an id cannot be opened; saying so is better
    // than linking to a URL that 404s.
    return pointer.doc_id
      ? { kind: "document", to: `/documents/${pointer.doc_id}` }
      : { kind: "reference", to: null };
  }
  if (pointer.origin_file) {
    return {
      kind: "file",
      to: pointer.origin_file,
      row: pointer.row_number,
      lineStart: pointer.line_start,
      lineEnd: pointer.line_end,
    };
  }
  if (pointer.kind === "dataset") return { kind: "dataset", to: "/sources" };
  if (pointer.kind === "graph_edge") {
    return caseId
      ? { kind: "graph", to: `/cases/${caseId}/graph` }
      : { kind: "reference", to: null };
  }
  if (pointer.kind === "metric") {
    return caseId
      ? { kind: "analytics", to: `/cases/${caseId}/investigation` }
      : { kind: "reference", to: null };
  }
  return { kind: "reference", to: null };
}

/** True when at least one evidence item behind this list can be opened. */
export function hasOpenableSource(items: EvidenceItem[] | null | undefined): boolean {
  return (items ?? []).some((item) =>
    (item.provenance ?? []).some((pointer) => Boolean(pointer.origin_file || pointer.doc_id)),
  );
}

/* ------------------------------------------------------------------------- */
/* Patterns (§34.4)                                                          */
/* ------------------------------------------------------------------------- */

export const PATTERN_LABELS: Record<string, string> = {
  CROSS_CASE_ENTITY: "Cross-case association",
  CROSS_CASE_LINK: "Cross-case link",
  COMMUNICATION_ANOMALY: "Communication anomaly",
  FINANCIAL_FLOW: "Financial-flow pattern",
  VEHICLE_USE_OWNERSHIP_MISMATCH: "Vehicle usage vs ownership",
  COLOCATION: "Location / co-location",
  TEMPORAL_BURST: "Temporal pattern",
  NETWORK_BRIDGE: "Network bridge",
  COMMUNITY_SIGNAL: "Community / centrality signal",
  REPEATED_COMBINATION: "Repeated combination",
  ER_SIGNAL: "Identity-resolution signal",
  SOCIAL_ONLY: "Social-media-only link",
};

export function patternTypeLabel(kind: string | null | undefined): string {
  if (!kind) return "Pattern";
  return PATTERN_LABELS[String(kind).toUpperCase()] ?? String(kind).replaceAll("_", " ");
}

/**
 * The one-line disclaimer every pattern carries.
 *
 * A pattern is a prioritisation signal computed from the graph and the
 * records; it is not a finding of criminality and the UI must say so.
 */
export const PATTERN_SIGNAL_NOTE =
  "Investigative signal — prioritises where to look, and is not a criminal status.";

/** Live patterns first, strongest first, then the explicitly set-aside ones. */
export function orderPatterns(patterns: SuspiciousPattern[] | null | undefined): SuspiciousPattern[] {
  const rank: Record<string, number> = { STRONG: 0, MODERATE: 1, WEAK: 2, INSUFFICIENT: 3 };
  const all = [...(patterns ?? [])];
  return all.sort((a, b) => {
    if (a.excluded !== b.excluded) return a.excluded ? 1 : -1;
    const byRank = (rank[a.strength] ?? 3) - (rank[b.strength] ?? 3);
    if (byRank !== 0) return byRank;
    return String(a.title).localeCompare(String(b.title));
  });
}

export function livePatterns(patterns: SuspiciousPattern[] | null | undefined): SuspiciousPattern[] {
  return orderPatterns(patterns).filter((pattern) => !pattern.excluded);
}

export function patternContradictionCount(pattern: SuspiciousPattern | null | undefined): number {
  return (pattern?.contradictions_considered ?? []).length;
}

/** Patterns that carry at least one recorded contradiction or alternative. */
export function patternHasChallenge(pattern: SuspiciousPattern | null | undefined): boolean {
  if (!pattern) return false;
  return (
    patternContradictionCount(pattern) > 0 ||
    (pattern.innocent_alternatives ?? []).length > 0
  );
}

/* ------------------------------------------------------------------------- */
/* Hypotheses                                                                */
/* ------------------------------------------------------------------------- */

export function hypothesisChallenged(hypothesis: Hypothesis | null | undefined): boolean {
  if (!hypothesis) return false;
  return (hypothesis.contradicting ?? []).length > 0;
}

/** Hypotheses ordered by strength, strongest first, stable within a rank. */
export function orderHypotheses(hypotheses: Hypothesis[] | null | undefined): Hypothesis[] {
  const rank: Record<string, number> = { STRONG: 0, MODERATE: 1, WEAK: 2, INSUFFICIENT: 3 };
  return [...(hypotheses ?? [])]
    .map((hypothesis, index) => ({ hypothesis, index }))
    .sort((a, b) => {
      const byRank = (rank[a.hypothesis.strength] ?? 3) - (rank[b.hypothesis.strength] ?? 3);
      if (byRank !== 0) return byRank;
      return a.index - b.index;
    })
    .map((entry) => entry.hypothesis);
}

/* ------------------------------------------------------------------------- */
/* Entities: criminal status vs network importance (§34.14)                  */
/* ------------------------------------------------------------------------- */

export interface EntityStanding {
  /** From the dataset's authoritative record only — never inferred. */
  criminalStatus: string | null;
  criminalStatusText: string;
  /** Analytic significance, described as a property of the network. */
  analyticStanding: string;
  differs: boolean;
}

export function entityStanding(entity: ResolvedEntity): EntityStanding {
  const status = entity.criminal_status ? String(entity.criminal_status) : null;
  const analytic = entity.resolved
    ? entity.confidence >= 0.85
      ? "Resolved to a canonical record"
      : `Provisionally matched (${Math.round(entity.confidence * 100)}%)`
    : "No matching record — unresolved mention";
  return {
    criminalStatus: status,
    criminalStatusText: status ? status.replaceAll("_", " ") : "No recorded status",
    analyticStanding: analytic,
    differs: !status && entity.resolved,
  };
}

/**
 * Explain why a node is highlighted, in words rather than a bare number.
 *
 * High centrality means the entity sits on many connection paths or holds a
 * dense neighbourhood — a statement about network shape, not conduct.
 */
export function centralityNarrative(pattern: SuspiciousPattern | null | undefined): string[] {
  if (!pattern) return [];
  const notes = pattern.strength_factors?.notes ?? [];
  let text = pattern.explanation ?? "";
  // The detector explanations already avoid accusation; surface the metric
  // sentence plus any factor notes as the "why" behind the highlight.
  const sentences = text
    .split(/(?<=\.)\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  return [...sentences, ...notes].slice(0, 6);
}

/* ------------------------------------------------------------------------- */
/* Scope (§34.13)                                                            */
/* ------------------------------------------------------------------------- */

export function scopeLabel(scope: ScopeSection | null | undefined): string {
  if (!scope) return "Scope unknown";
  if (scope.label) return scope.label;
  if (scope.mode === "case") return `Case ${scope.case_number ?? scope.case_id ?? "unknown"}`;
  return "Master Network";
}

export function scopeSentence(scope: ScopeSection | null | undefined): string {
  if (!scope) return "";
  const parts = [
    `${scope.nodes_considered} node(s)`,
    `${scope.edges_considered} relationship(s)`,
    `${scope.documents_considered} document(s)`,
  ];
  if (scope.mode === "master") {
    parts.unshift(`${scope.case_ids.length} case(s) in the master network`);
  }
  return parts.join(" · ");
}

/** A follow-up keeps the thread objective; a new question can replace it. */
export function nextObjective(
  current: InvestigatorResponse | null,
  question: string,
  continuing: boolean,
): string {
  if (continuing && current?.objective) return current.objective;
  return question;
}

/* ------------------------------------------------------------------------- */
/* Timeline (§34.8)                                                          */
/* ------------------------------------------------------------------------- */

export function timelineTimestamp(entry: TimelineEntry | null | undefined): string | null {
  if (!entry) return null;
  const value = entry.ts ?? entry.timestamp;
  return value ? String(value) : null;
}

export function timelineLabel(entry: TimelineEntry | null | undefined): string {
  if (!entry) return "";
  const label = entry.label ?? entry.summary ?? entry.rel_type;
  return label ? String(label) : "Recorded event";
}

/**
 * Where an event sits relative to a reference moment.
 *
 * Proximity is reported, never causality: the returned phase is a position in
 * time, and the UI states that ordering on its own proves nothing.
 */
export function timelinePhase(
  timestamp: string | null | undefined,
  referenceIso: string | null | undefined,
): "before" | "during" | "after" | "unknown" {
  if (!timestamp || !referenceIso) return "unknown";
  const at = Date.parse(timestamp);
  const ref = Date.parse(referenceIso);
  if (Number.isNaN(at) || Number.isNaN(ref)) return "unknown";
  const delta = at - ref;
  const day = 24 * 60 * 60 * 1000;
  if (Math.abs(delta) <= day) return "during";
  return delta < 0 ? "before" : "after";
}

/**
 * Drop events that happen after a reference moment.
 *
 * The reasoning layer must never use later evidence to explain an earlier
 * event; when the workspace pins an incident, later records are excluded from
 * the "around the incident" reading rather than quietly included.
 */
export function timelineUpTo(
  entries: TimelineEntry[] | null | undefined,
  referenceIso: string | null | undefined,
): TimelineEntry[] {
  const list = entries ?? [];
  if (!referenceIso) return list;
  const ref = Date.parse(referenceIso);
  if (Number.isNaN(ref)) return list;
  return list.filter((entry) => {
    const ts = timelineTimestamp(entry);
    if (!ts) return true;
    const at = Date.parse(ts);
    if (Number.isNaN(at)) return true;
    return at <= ref;
  });
}

/* ------------------------------------------------------------------------- */
/* Gaps (§34.10)                                                             */
/* ------------------------------------------------------------------------- */

export const GAP_HEADINGS: Record<string, string> = {
  "unresolved-entity": "Unresolved identity",
  "provisional-identity": "Provisional match",
  "missing-source": "Missing source",
  "single-source": "Single-source reading",
  "missing-link": "No connecting record",
};

export function gapHeading(gap: DataGap | null | undefined): string {
  if (!gap) return "Data gap";
  return GAP_HEADINGS[gap.category] ?? String(gap.category).replaceAll("-", " ");
}

/**
 * A gap phrased as missing evidence, never as a negative finding.
 *
 * "No CDR records in scope" is the only thing a missing source supports;
 * "they did not communicate" is a conclusion the data cannot reach.
 */
export function gapSentence(gap: DataGap): string {
  const description = String(gap.description ?? "").trim();
  if (!description) return "Required evidence is not available in scope.";
  if (/^no\s/i.test(description) && !/available|in scope|received/i.test(description)) {
    return `${description} — absence of the source is not evidence of absence of activity.`;
  }
  return description;
}

/* ------------------------------------------------------------------------- */
/* Next directions (§34.16)                                                  */
/* ------------------------------------------------------------------------- */

const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function orderNextSteps(steps: NextStep[] | null | undefined): NextStep[] {
  return [...(steps ?? [])]
    .map((step, index) => ({ step, index }))
    .sort((a, b) => {
      const byRank =
        (PRIORITY_RANK[a.step.priority] ?? 1) - (PRIORITY_RANK[b.step.priority] ?? 1);
      if (byRank !== 0) return byRank;
      return a.index - b.index;
    })
    .map((entry) => entry.step);
}

/** Solid, non-coercive actions the console can actually open. */
export function nextStepLink(step: NextStep | null | undefined): {
  kind: "case" | "entity" | "none";
  value: string | null;
} {
  const links = step?.links ?? {};
  const caseIds = links.case_ids ?? links.cases ?? [];
  if (caseIds.length === 1) return { kind: "case", value: String(caseIds[0]) };
  const entities = links.entities ?? links.entity_keys ?? [];
  if (entities.length === 1) return { kind: "entity", value: String(entities[0]) };
  return { kind: "none", value: null };
}

/* ------------------------------------------------------------------------- */
/* Assessment                                                                */
/* ------------------------------------------------------------------------- */

/** Convergence in words, from the backend's deterministic reading. */
export function convergenceSentence(assessment: AssessmentSection | null | undefined): string {
  const convergence = (assessment?.convergence ?? {}) as Record<string, unknown>;
  const note = convergence.note;
  if (typeof note === "string" && note.trim()) return note;
  return convergence.converges
    ? "Independent evidence streams agree."
    : "Evidence streams do not yet converge.";
}

/** True when the narrative came from a model rather than the deterministic run. */
export function modelNarrated(assessment: AssessmentSection | null | undefined): boolean {
  return Boolean(assessment?.model?.available);
}

/**
 * The honest line about where the prose came from.
 *
 * The workspace must never present model text as if it were deterministic
 * analysis, and must never hide that the deterministic analysis ran anyway.
 */
export function provenanceOfProse(assessment: AssessmentSection | null | undefined): string {
  const model = assessment?.model;
  if (model?.available) {
    return `Narrative explanation by ${model.model ?? "the configured model"} over the deterministic analysis above.`;
  }
  const reason = model?.reason ? ` (${model.reason})` : "";
  return `Deterministic analysis only — no language model contributed${reason}.`;
}

/** Reason a model contributed nothing, in investigator-facing words. */
export function modelUnavailableNote(assessment: AssessmentSection | null | undefined): string | null {
  const model = assessment?.model;
  if (!model || model.available) return null;
  const caveat = (model.caveats ?? [])[0];
  if (caveat) return caveat;
  if (model.reason) return `The reasoning model was unavailable: ${model.reason}.`;
  return "The reasoning model was unavailable; the analysis above is deterministic.";
}

/** Labels the answer actually uses, in display order (for the legend). */
export function labelsPresent(response: InvestigatorResponse | null | undefined): InferenceLabel[] {
  if (!response) return [];
  const order: InferenceLabel[] = [
    "FACT",
    "CORROBORATED_LEAD",
    "LEAD",
    "HYPOTHESIS",
    "COINCIDENCE",
    "DATA_GAP",
  ];
  const present = new Set<string>();
  const collect = (label: string | null | undefined) => {
    if (label) present.add(String(label).toUpperCase());
  };
  (response.facts ?? []).forEach((item) => collect(item.inference_label));
  (response.relationships ?? []).forEach((item) => collect(item.inference_label));
  (response.patterns ?? []).forEach((item) => collect(item.inference_label));
  (response.hypotheses ?? []).forEach((item) => collect(item.inference_label));
  (response.gaps ?? []).forEach((item) => collect(item.inference_label));
  return order.filter((label) => present.has(label));
}

/** Human scope of a pattern's evidence, from what it actually carries. */
export function patternScopeSentence(pattern: SuspiciousPattern | null | undefined): string {
  if (!pattern) return "";
  const parts: string[] = [];
  if ((pattern.entities ?? []).length) parts.push(pattern.entities.join(", "));
  if ((pattern.cases ?? []).length) parts.push(`${pattern.cases.length} case(s)`);
  const start = pattern.time_range?.start;
  const end = pattern.time_range?.end;
  if (start || end) parts.push([start, end].filter(Boolean).join(" → "));
  return parts.join(" · ");
}

/**
 * Count of openable sources behind a pattern.
 *
 * A pointer is openable when it names an ingested document or a row inside a
 * source file. Graph edges, computed metrics and dataset-level records are
 * references, not files, so they are shown as such and never counted here —
 * reporting them as sources would dress up a reference as a record.
 */
export function patternSourceCount(pattern: SuspiciousPattern | null | undefined): number {
  if (!pattern) return 0;
  const openable = (pointer: ProvenanceItem) => Boolean(pointer.origin_file || pointer.doc_id);
  const fromEvidence = mergeEvidence([pattern.evidence]).filter((item) =>
    (item.provenance ?? []).some(openable),
  ).length;
  const fromRollUp = (pattern.provenance ?? []).filter(openable).length;
  return Math.max(fromEvidence, fromRollUp);
}

/* ------------------------------------------------------------------------- */
/* Suggested questions & focused subgraphs                                   */
/* ------------------------------------------------------------------------- */

/**
 * Question prompts derived from what was actually detected.
 *
 * Nothing here is dataset-specific: prompts are composed from the entities,
 * pattern types and entity kinds present in the current answer/scan, so a
 * freshly imported dataset produces its own prompts with no code change.
 */
export function suggestedQuestions(
  patterns: SuspiciousPattern[] | null | undefined,
  entities?: string[] | null,
): string[] {
  const out: string[] = [];
  const push = (question: string) => {
    if (question.trim() && !out.includes(question)) out.push(question);
  };

  const live = livePatterns(patterns);
  const byKind = new Map<string, SuspiciousPattern>();
  for (const pattern of live) {
    if (!byKind.has(pattern.kind)) byKind.set(pattern.kind, pattern);
  }
  const firstEntityOf = (pattern?: SuspiciousPattern): string | null =>
    pattern && (pattern.entities ?? []).length > 0 ? String(pattern.entities[0]) : null;

  const crossCase = firstEntityOf(byKind.get("CROSS_CASE_ENTITY"));
  if (crossCase) push(`Why is ${crossCase} important across these cases?`);

  const bridge = firstEntityOf(byKind.get("NETWORK_BRIDGE"));
  if (bridge) push(`What connects the groups that ${bridge} links?`);

  const comms = firstEntityOf(byKind.get("COMMUNICATION_ANOMALY"));
  if (comms) push(`Explain the communication pattern involving ${comms}.`);

  const money = firstEntityOf(byKind.get("FINANCIAL_FLOW"));
  if (money) push(`Is the financial activity involving ${money} connected to the cases?`);

  const vehicle = firstEntityOf(byKind.get("VEHICLE_USE_OWNERSHIP_MISMATCH"));
  if (vehicle) push(`Who is actually using the vehicle linked to ${vehicle}?`);

  const named = (entities ?? []).filter(Boolean);
  if (named.length >= 2) push(`Is there a connection between ${named[0]} and ${named[1]}?`);

  push("What should I investigate next?");
  push("What information is missing from this analysis?");
  return out.slice(0, 6);
}

/**
 * The focused graph narrowed to one finding.
 *
 * Keeps the seed entities plus the nodes directly attached to them and drops
 * everything else — the point of the focused view is that it shows the
 * finding's own evidence, not the whole neighbourhood the question loaded.
 */
export function subgraphFor(
  graph: { nodes?: { key: string; label: string; name: string; focus?: boolean }[]; edges?: { source: string; target: string; rel_type: string }[]; truncated?: boolean } | null | undefined,
  seeds: string[] | null | undefined,
): { nodes: { key: string; label: string; name: string; focus: boolean }[]; edges: { source: string; target: string; rel_type: string }[]; truncated: boolean } {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const wanted = new Set((seeds ?? []).filter((seed) => nodes.some((node) => node.key === seed)));
  if (wanted.size === 0) return { nodes: [], edges: [], truncated: false };

  const kept = new Set(wanted);
  for (const edge of edges) {
    if (wanted.has(edge.source)) kept.add(edge.target);
    if (wanted.has(edge.target)) kept.add(edge.source);
  }
  const keptNodes = nodes
    .filter((node) => kept.has(node.key))
    .map((node) => ({ ...node, focus: wanted.has(node.key) }));
  const keptEdges = edges.filter(
    (edge) => kept.has(edge.source) && kept.has(edge.target),
  );
  return {
    nodes: keptNodes,
    edges: keptEdges,
    truncated: keptNodes.length < nodes.length,
  };
}

/** Stable identity for a pattern (used as the selection key). */
export function patternKey(pattern: SuspiciousPattern | null | undefined): string | null {
  if (!pattern) return null;
  return `${pattern.kind}-${pattern.title}`;
}

/** The entity keys a selection should highlight in the focused graph. */
export function selectionEntityKeys(
  selection:
    | { kind: "pattern"; value: SuspiciousPattern }
    | { kind: "hypothesis"; value: Hypothesis }
    | { kind: "relationship"; value: { entities?: string[] } }
    | null,
  entities: ResolvedEntity[] | null | undefined,
): string[] {
  if (!selection) return (entities ?? []).map((entity) => entity.canonical_id);
  const list = entities ?? [];
  if (selection.kind === "pattern") return selection.value.entity_keys ?? [];
  const names =
    selection.kind === "hypothesis"
      ? selection.value.entities ?? []
      : selection.value.entities ?? [];
  return list.filter((entity) => names.includes(entity.display_name)).map((entity) => entity.canonical_id);
}

/** Relationship kinds that mean "these two are directly linked". */
export function relationshipIsDirect(kind: string | null | undefined): boolean {
  return String(kind ?? "") === "direct";
}
