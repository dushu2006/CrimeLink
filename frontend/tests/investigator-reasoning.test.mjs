/**
 * Unit tests for the investigator workspace's reasoning presentation logic.
 *
 * Run against the REAL `src/lib/investigator.ts` with Node's type stripping,
 * using payloads in the exact shape the backend's `/investigate` endpoint
 * serves. No network, no model, no DOM.
 *
 * The properties under test are the ones that keep the workspace honest:
 *
 *   - facts and interpretations stay distinguishable
 *   - suspiciousness never renders as criminality
 *   - evidence strength renders as support, never as a probability of guilt
 *   - contradictions and alternatives are never dropped from a finding
 *   - missing data renders as a gap, never as "nothing happened"
 *   - later evidence never explains an earlier event
 *   - a name the dataset does not contain is never treated as a person
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const LIB_URL = new URL("../src/lib/investigator.ts", import.meta.url).href;
const LIB = await import(LIB_URL);

// ---------------------------------------------------------------------------
// Fixtures in the exact wire shape of POST /api/v1/investigate
// ---------------------------------------------------------------------------

function pointer(overrides = {}) {
  return {
    kind: "document",
    ref: "doc-1",
    label: "cdr.csv",
    detail: "CDR",
    doc_id: "doc-1",
    origin_file: "cdr.csv",
    row_number: null,
    line_start: null,
    line_end: null,
    content_hash: "ab".repeat(32),
    ...overrides,
  };
}

function evidence(overrides = {}) {
  return {
    kind: "record",
    summary: "A record was found.",
    inference_label: "FACT",
    stance: "supports",
    provenance: [pointer()],
    ...overrides,
  };
}

function pattern(overrides = {}) {
  return {
    kind: "CROSS_CASE_ENTITY",
    title: "Cross-case entity",
    explanation: "Appears in more than one case.",
    entities: ["Asha Nair"],
    entity_keys: ["ds:1:PERSON:P1"],
    cases: ["c1", "c2"],
    time_range: { start: "2026-01-01T00:00:00", end: "2026-02-01T00:00:00" },
    evidence: [evidence()],
    inference_label: "LEAD",
    strength: "WEAK",
    strength_factors: {
      independent_sources: 1,
      corroborating_records: 3,
      temporal_relevance: "clustered",
      directness: "direct",
      consistency: "unknown",
      contradiction_level: "none",
      entity_certainty: "resolved",
      notes: ["Cross-case presence is a grouping, not an accusation."],
    },
    contradictions_considered: ["Distinct roles in each case."],
    innocent_alternatives: ["Ordinary business travel."],
    excluded: false,
    exclusion_reason: null,
    provenance: [],
    ...overrides,
  };
}

function hypothesis(overrides = {}) {
  return {
    id: "H1-association",
    statement: "A and B are associated.",
    entities: ["A", "B"],
    supporting: [evidence({ summary: "A called B." })],
    contradicting: [
      evidence({ summary: "The call predates the incident.", stance: "contradicts" }),
    ],
    innocent_alternatives: ["Routine contact."],
    inference_label: "HYPOTHESIS",
    strength: "MODERATE",
    strength_factors: {
      independent_sources: 2,
      corroborating_records: 5,
      temporal_relevance: "clustered",
      directness: "direct",
      consistency: "unknown",
      contradiction_level: "minor",
      entity_certainty: "resolved",
      notes: [],
    },
    analysis: {
      observation: "The records show contact.",
      interpretation: "This could indicate coordination.",
      assessment: "Not established.",
    },
    ...overrides,
  };
}

function response(overrides = {}) {
  return {
    question: "Are A and B connected?",
    objective: "Are A and B connected?",
    investigation_id: "inv-1",
    scope: {
      mode: "master",
      label: "Master Network",
      dataset_id: "ds-1",
      dataset_name: "Imported dataset",
      case_id: null,
      case_number: null,
      case_title: null,
      case_ids: ["c1", "c2"],
      nodes_considered: 5,
      edges_considered: 4,
      documents_considered: 1,
    },
    entities: [],
    facts: [],
    relationships: [],
    patterns: [],
    hypotheses: [],
    alternative_explanations: [],
    assessment: {
      overall_strength: "WEAK",
      overall_confidence: 0.4,
      convergence: { converges: false, note: "Streams do not converge." },
      observation: "One relationship found.",
      interpretation: "Could be coordination.",
      assessment: "Provisional.",
      caveats: [],
      model: {
        available: false,
        role: "investigation_reasoning",
        model: null,
        reason: "api_key_unavailable",
        summary: "",
        observation: "",
        interpretation: "",
        assessment: "",
        convergence_note: "",
        caveats: ["No language model is configured: this answer is the deterministic analysis only."],
        suggested_next_actions: [],
        language_edits: [],
      },
    },
    gaps: [],
    next_steps: [],
    timeline: [],
    focused_graph: { nodes: [], edges: [], truncated: false },
    provenance: [],
    memory: {
      investigation_id: "inv-1",
      objective: "Are A and B connected?",
      questions_asked: 1,
      prior_questions: ["Are A and B connected?"],
      confirmed_facts: [],
      open_hypotheses: [],
      examined_entities: [],
      open_gaps: [],
      unresolved: [],
      contradictions: [],
      relationships: [],
      rejected_hypotheses: [],
      prior_findings: [],
    },
    timing_ms: {},
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Labels and strength vocabulary
// ---------------------------------------------------------------------------

test("every inference label renders as a neutral, non-accusatory word", () => {
  const labels = ["FACT", "CORROBORATED_LEAD", "LEAD", "HYPOTHESIS", "COINCIDENCE", "DATA_GAP"];
  for (const label of labels) {
    const text = LIB.labelText(label);
    assert.ok(text.length > 0);
    for (const banned of ["criminal", "guilt", "guilty", "suspect", "offender"]) {
      assert.ok(
        !text.toLowerCase().includes(banned),
        `label ${label} rendered as ${text}, which implies criminality`,
      );
    }
  }
});

test("strength is described as support, never as a probability of guilt", () => {
  for (const strength of ["STRONG", "MODERATE", "WEAK", "INSUFFICIENT"]) {
    const sentence = LIB.strengthSentence(strength);
    assert.ok(!/%/.test(sentence), `${strength} leaked a percentage: ${sentence}`);
    for (const banned of ["guilt", "guilty", "criminal", "likely offender"]) {
      assert.ok(!sentence.toLowerCase().includes(banned));
    }
  }
  assert.match(LIB.strengthSentence("STRONG"), /independent evidence streams/i);
  assert.match(LIB.strengthSentence("INSUFFICIENT"), /Not enough evidence/i);
});

test("strength factors are explained in words, including the downgrades", () => {
  const lines = LIB.describeStrengthFactors({
    independent_sources: 1,
    corroborating_records: 2,
    temporal_relevance: "clustered",
    directness: "indirect",
    consistency: "unknown",
    contradiction_level: "major",
    entity_certainty: "provisional",
    notes: ["Capped at WEAK: entity identity is not resolved."],
  });
  const text = lines.join(" ");
  assert.match(text, /One independent evidence stream/);
  assert.match(text, /indirect/);
  assert.match(text, /major/);
  assert.match(text, /provisional/);
  assert.match(text, /Capped at WEAK/);
});

// ---------------------------------------------------------------------------
// Suspicious ≠ criminal
// ---------------------------------------------------------------------------

test("every pattern type has a neutral human label", () => {
  const kinds = [
    "CROSS_CASE_ENTITY",
    "CROSS_CASE_LINK",
    "COMMUNICATION_ANOMALY",
    "FINANCIAL_FLOW",
    "VEHICLE_USE_OWNERSHIP_MISMATCH",
    "COLOCATION",
    "TEMPORAL_BURST",
    "NETWORK_BRIDGE",
    "COMMUNITY_SIGNAL",
    "REPEATED_COMBINATION",
    "ER_SIGNAL",
    "SOCIAL_ONLY",
  ];
  for (const kind of kinds) {
    const label = LIB.patternTypeLabel(kind);
    assert.notEqual(label, kind, `${kind} has no human label`);
    assert.ok(!label.toLowerCase().includes("criminal"), `${kind} label implies criminality`);
  }
});

test("the pattern signal note never claims criminality", () => {
  assert.match(LIB.PATTERN_SIGNAL_NOTE, /signal/i);
  assert.match(LIB.PATTERN_SIGNAL_NOTE, /not a criminal status/i);
});

test("an unknown pattern kind still renders as readable words", () => {
  assert.equal(LIB.patternTypeLabel("SOMETHING_NEW"), "SOMETHING NEW");
});

// ---------------------------------------------------------------------------
// Ordering: strongest first, set-asides last
// ---------------------------------------------------------------------------

test("live patterns come before set-aside ones and strongest first", () => {
  const ordered = LIB.orderPatterns([
    pattern({ title: "weak", strength: "WEAK" }),
    pattern({ title: "set-aside", excluded: true, strength: "INSUFFICIENT" }),
    pattern({ title: "strong", strength: "STRONG" }),
  ]);
  assert.deepEqual(
    ordered.map((item) => item.title),
    ["strong", "weak", "set-aside"],
  );
});

// ---------------------------------------------------------------------------
// Contradictions and alternatives are never dropped
// ---------------------------------------------------------------------------

test("a challenged pattern is reported as challenged", () => {
  assert.equal(LIB.patternHasChallenge(pattern()), true);
  assert.equal(
    LIB.patternHasChallenge(
      pattern({ contradictions_considered: [], innocent_alternatives: [] }),
    ),
    false,
  );
});

test("hypothesis evidence splits into supporting and contradictory", () => {
  const item = hypothesis();
  const supports = LIB.evidenceByStance(item.supporting, "supports");
  const contradicts = LIB.evidenceByStance(item.contradicting, "contradicts");
  assert.equal(supports.length, 1);
  assert.equal(contradicts.length, 1);
  assert.equal(contradicts[0].summary, "The call predates the incident.");
});

test("hypotheses render strongest first without losing any", () => {
  const ordered = LIB.orderHypotheses([
    hypothesis({ id: "H1", strength: "WEAK" }),
    hypothesis({ id: "H2", strength: "STRONG" }),
    hypothesis({ id: "H3", strength: "INSUFFICIENT" }),
  ]);
  assert.deepEqual(
    ordered.map((item) => item.id),
    ["H2", "H1", "H3"],
  );
});

test("merged evidence deduplicates without reordering", () => {
  const merged = LIB.mergeEvidence([
    [evidence({ summary: "first" }), evidence({ summary: "shared" })],
    [evidence({ summary: "shared" }), evidence({ summary: "last" })],
  ]);
  assert.deepEqual(
    merged.map((item) => item.summary),
    ["first", "shared", "last"],
  );
});

// ---------------------------------------------------------------------------
// Data gaps: absence of a source is not absence of activity
// ---------------------------------------------------------------------------

test("a missing source is phrased as a gap, not as a negative finding", () => {
  const sentence = LIB.gapSentence({
    category: "missing-source",
    description: "No CDR records in the in-scope cases.",
    what_would_help: "Obtain call-detail records.",
    inference_label: "DATA_GAP",
    entities: [],
    cases: [],
  });
  assert.match(sentence, /No CDR records/);
  assert.match(sentence, /not evidence of absence/i);
  assert.ok(!/did not communicate/i.test(sentence));
});

test("a gap that already names what is unavailable is not double-qualified", () => {
  const sentence = LIB.gapSentence({
    category: "missing-source",
    description: "Records are unavailable for the relevant period.",
    what_would_help: "",
    inference_label: "DATA_GAP",
    entities: [],
    cases: [],
  });
  assert.equal(sentence, "Records are unavailable for the relevant period.");
});

test("gap headings fall back to the raw category when unknown", () => {
  assert.equal(LIB.gapHeading({ category: "missing-source" }), "Missing source");
  assert.equal(LIB.gapHeading({ category: "some-new-gap" }), "some new gap");
});

// ---------------------------------------------------------------------------
// Criminal status vs analytical standing
// ---------------------------------------------------------------------------

test("a resolved entity with no recorded status is not given one", () => {
  const standing = LIB.entityStanding({
    canonical_id: "ds:1:PERSON:P1",
    label: "Person",
    display_name: "Asha Nair",
    aliases: [],
    confidence: 1,
    matched_by: "name",
    entity_keys: ["ds:1:PERSON:P1"],
    criminal_status: null,
    resolved: true,
    ambiguity_note: null,
  });
  assert.equal(standing.criminalStatus, null);
  assert.equal(standing.criminalStatusText, "No recorded status");
  assert.equal(standing.differs, true);
  assert.match(standing.analyticStanding, /Resolved to a canonical record/);
});

test("a recorded status is echoed verbatim and never inferred", () => {
  const standing = LIB.entityStanding({
    canonical_id: "ds:1:PERSON:P2",
    label: "Person",
    display_name: "Ravi Mehta",
    aliases: [],
    confidence: 1,
    matched_by: "name",
    entity_keys: ["ds:1:PERSON:P2"],
    criminal_status: "convicted",
    resolved: true,
    ambiguity_note: null,
  });
  assert.equal(standing.criminalStatus, "convicted");
  assert.equal(standing.differs, false);
});

test("an unmatched mention is described as unresolved, not as a person", () => {
  const standing = LIB.entityStanding({
    canonical_id: "unresolved:someone",
    label: "Person",
    display_name: "Someone",
    aliases: [],
    confidence: 0,
    matched_by: "none",
    entity_keys: [],
    criminal_status: null,
    resolved: false,
    ambiguity_note: null,
  });
  assert.match(standing.analyticStanding, /No matching record/);
});

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

test("case scope renders the case number, master scope says so plainly", () => {
  assert.equal(
    LIB.scopeLabel({ mode: "case", label: "Case C106", case_number: "C106", case_id: "id" }),
    "Case C106",
  );
  assert.equal(
    LIB.scopeLabel({ mode: "case", label: "", case_number: "C106", case_id: "id" }),
    "Case C106",
  );
  assert.equal(LIB.scopeLabel({ mode: "master", label: "Master Network" }), "Master Network");
  assert.equal(LIB.scopeLabel(null), "Scope unknown");
});

test("scope sentence reports what was actually considered", () => {
  const sentence = LIB.scopeSentence({
    mode: "master",
    label: "Master Network",
    case_ids: ["a", "b"],
    nodes_considered: 7,
    edges_considered: 9,
    documents_considered: 2,
  });
  assert.match(sentence, /2 case\(s\) in the master network/);
  assert.match(sentence, /7 node\(s\)/);
  assert.match(sentence, /9 relationship\(s\)/);
});

test("a follow-up keeps the objective, a fresh question takes its own", () => {
  const current = response({ objective: "Are C104 and C110 connected?" });
  assert.equal(
    LIB.nextObjective(current, "What evidence supports that?", true),
    "Are C104 and C110 connected?",
  );
  assert.equal(
    LIB.nextObjective(current, "Why is P003 important?", false),
    "Why is P003 important?",
  );
});

// ---------------------------------------------------------------------------
// Temporal integrity
// ---------------------------------------------------------------------------

test("events are phased relative to the reference moment", () => {
  const ref = "2026-03-10T12:00:00Z";
  assert.equal(LIB.timelinePhase("2026-03-01T12:00:00Z", ref), "before");
  assert.equal(LIB.timelinePhase("2026-03-10T18:00:00Z", ref), "during");
  assert.equal(LIB.timelinePhase("2026-04-01T12:00:00Z", ref), "after");
  assert.equal(LIB.timelinePhase(null, ref), "unknown");
  assert.equal(LIB.timelinePhase("2026-03-01T12:00:00Z", null), "unknown");
});

test("later evidence is excluded from an around-the-incident reading", () => {
  const upTo = LIB.timelineUpTo(
    [
      { timestamp: "2026-03-01T12:00:00Z", label: "earlier" },
      { timestamp: "2026-03-10T12:00:00Z", label: "incident" },
      { timestamp: "2026-04-01T12:00:00Z", label: "later" },
    ],
    "2026-03-10T12:00:00Z",
  );
  assert.deepEqual(
    upTo.map((entry) => entry.label),
    ["earlier", "incident"],
  );
});

test("undated records survive filtering rather than being silently dropped", () => {
  const kept = LIB.timelineUpTo([{ label: "no timestamp" }], "2026-03-10T12:00:00Z");
  assert.equal(kept.length, 1);
});

// ---------------------------------------------------------------------------
// Provenance
// ---------------------------------------------------------------------------

test("provenance pointers render the file and the exact position", () => {
  assert.equal(
    LIB.provenanceText(pointer({ origin_file: "raw/cdr.csv", row_number: 18342 })),
    "cdr.csv · row 18342",
  );
  assert.equal(
    LIB.provenanceText(
      pointer({ origin_file: "reports/fir.pdf", row_number: null, line_start: 12, line_end: 18 }),
    ),
    "fir.pdf · lines 12–18",
  );
  assert.equal(
    LIB.provenanceText(pointer({ origin_file: "reports/fir.pdf", row_number: null, line_start: 7, line_end: 7 })),
    "fir.pdf · line 7",
  );
  assert.equal(LIB.provenanceText(null), null);
});

test("metrics and edges are not dressed up as openable files", () => {
  assert.equal(LIB.hasOpenableSource([evidence({ provenance: [] })]), false);
  assert.equal(
    LIB.hasOpenableSource([
      evidence({
        provenance: [pointer({ kind: "metric", origin_file: null, doc_id: null, ref: "analytics:X" })],
      }),
    ]),
    false,
  );
  assert.equal(LIB.hasOpenableSource([evidence()]), true);
});

test("pattern source count reflects openable sources only", () => {
  assert.equal(LIB.patternSourceCount(pattern()), 1);
  assert.equal(
    LIB.patternSourceCount(pattern({ evidence: [], provenance: [] })),
    0,
  );
});

// ---------------------------------------------------------------------------
// Model attribution
// ---------------------------------------------------------------------------

test("deterministic-only answers say no model contributed", () => {
  const assessment = response().assessment;
  assert.equal(LIB.modelNarrated(assessment), false);
  assert.match(LIB.provenanceOfProse(assessment), /Deterministic analysis only/);
  assert.equal(LIB.modelUnavailableNote(assessment), assessment.model.caveats[0]);
});

test("narrated answers attribute the narrative to the model", () => {
  const assessment = {
    ...response().assessment,
    model: {
      ...response().assessment.model,
      available: true,
      model: "some-model",
      reason: null,
    },
  };
  assert.equal(LIB.modelNarrated(assessment), true);
  assert.match(LIB.provenanceOfProse(assessment), /Narrative explanation by some-model/);
  assert.equal(LIB.modelUnavailableNote(assessment), null);
});

// ---------------------------------------------------------------------------
// Focused graph: only the finding's own evidence
// ---------------------------------------------------------------------------

test("subgraph keeps only the seed nodes and one hop around them", () => {
  const graph = {
    nodes: [
      { key: "a", label: "PERSON", name: "A", focus: true },
      { key: "b", label: "PHONE", name: "B", focus: false },
      { key: "c", label: "PERSON", name: "C", focus: false },
      { key: "d", label: "CASE", name: "D", focus: false },
    ],
    edges: [
      { source: "a", target: "b", rel_type: "USES_PHONE" },
      { source: "b", target: "c", rel_type: "CALLED" },
      { source: "c", target: "d", rel_type: "PARTICIPATED_IN" },
    ],
    truncated: false,
  };
  const narrowed = LIB.subgraphFor(graph, ["a"]);
  assert.deepEqual(
    narrowed.nodes.map((node) => node.key).sort(),
    ["a", "b"],
  );
  assert.deepEqual(narrowed.edges.map((edge) => edge.rel_type), ["USES_PHONE"]);
  assert.equal(narrowed.nodes.find((node) => node.key === "a").focus, true);
  assert.equal(narrowed.nodes.find((node) => node.key === "b").focus, false);
});

test("an empty seed set yields an empty subgraph rather than the whole graph", () => {
  const narrowed = LIB.subgraphFor(
    { nodes: [{ key: "a", label: "PERSON", name: "A", focus: true }], edges: [] },
    [],
  );
  assert.deepEqual(narrowed.nodes, []);
});

test("selection maps back to canonical entity keys", () => {
  const entities = [
    {
      canonical_id: "ds:1:PERSON:P1",
      label: "Person",
      display_name: "Asha Nair",
      aliases: [],
      confidence: 1,
      matched_by: "name",
      entity_keys: ["ds:1:PERSON:P1"],
      criminal_status: null,
      resolved: true,
      ambiguity_note: null,
    },
  ];
  const keys = LIB.selectionEntityKeys(
    { kind: "hypothesis", value: hypothesis({ entities: ["Asha Nair"] }) },
    entities,
  );
  assert.deepEqual(keys, ["ds:1:PERSON:P1"]);
});

// ---------------------------------------------------------------------------
// Suggested questions: derived from the data, never hardcoded
// ---------------------------------------------------------------------------

test("suggested questions are composed from what was actually detected", () => {
  const chips = LIB.suggestedQuestions([
    pattern({ kind: "NETWORK_BRIDGE", entities: ["Bridge Person"] }),
    pattern({ kind: "FINANCIAL_FLOW", entities: ["Account Holder"] }),
  ]);
  assert.ok(chips.some((chip) => chip.includes("Bridge Person")));
  assert.ok(chips.some((chip) => chip.includes("Account Holder")));
  assert.ok(chips.some((chip) => /investigate next/i.test(chip)));
  for (const chip of chips) {
    // No sample identifiers may appear: prompts must fit any active dataset.
    assert.ok(!/P\d{3}|C1\d{2}|BA\d{3}/.test(chip), `hardcoded identifier in prompt: ${chip}`);
  }
});

test("no patterns still produces useful, non-fabricated prompts", () => {
  const chips = LIB.suggestedQuestions([]);
  assert.ok(chips.length > 0);
  assert.ok(chips.every((chip) => chip.trim().length > 0));
});

// ---------------------------------------------------------------------------
// Next directions
// ---------------------------------------------------------------------------

test("next steps order high priority first and never lose a step", () => {
  const ordered = LIB.orderNextSteps([
    { action: "low", rationale: "", priority: "low", links: {} },
    { action: "high", rationale: "", priority: "high", links: {} },
    { action: "medium", rationale: "", priority: "medium", links: {} },
  ]);
  assert.deepEqual(
    ordered.map((step) => step.action),
    ["high", "medium", "low"],
  );
});

test("next-step links only point at something the console can open", () => {
  assert.deepEqual(LIB.nextStepLink({ links: { case_ids: ["c1"] } }), {
    kind: "case",
    value: "c1",
  });
  assert.deepEqual(LIB.nextStepLink({ links: { entities: ["Asha Nair"] } }), {
    kind: "entity",
    value: "Asha Nair",
  });
  assert.deepEqual(LIB.nextStepLink({ links: {} }), { kind: "none", value: null });
  assert.deepEqual(LIB.nextStepLink({ links: { case_ids: ["a", "b"] } }), {
    kind: "none",
    value: null,
  });
});

// ---------------------------------------------------------------------------
// Whole-answer properties
// ---------------------------------------------------------------------------

test("the label legend only advertises labels the answer actually used", () => {
  const labels = LIB.labelsPresent(
    response({
      facts: [evidence({ inference_label: "FACT" })],
      patterns: [pattern({ inference_label: "LEAD" })],
    }),
  );
  assert.deepEqual(labels, ["FACT", "LEAD"]);
});

test("an empty answer advertises nothing", () => {
  assert.deepEqual(LIB.labelsPresent(response()), []);
  assert.deepEqual(LIB.labelsPresent(null), []);
});

test("centrality signals explain themselves in terms of network shape", () => {
  const lines = LIB.centralityNarrative(
    pattern({
      kind: "COMMUNITY_SIGNAL",
      strength_factors: {
        ...pattern().strength_factors,
        notes: ["Community membership is a grouping, not an accusation."],
      },
      explanation: "Sits on many connection paths. High centrality does not imply wrongdoing.",
    }),
  );
  const text = lines.join(" ");
  assert.match(text, /many connection paths/);
  assert.match(text, /not imply wrongdoing|not an accusation/);
});

// ---------------------------------------------------------------------------
// Provenance roll-up (§34.11 / §35.14)
// ---------------------------------------------------------------------------

test("a finding's openable sources come from its rolled-up pointers, not a count", () => {
  // The backend derives `provenance` from the evidence items, so a pattern that
  // carries only metric pointers still reports the sources it has, and a
  // pattern with nothing attached reports zero rather than a decorative one.
  assert.equal(LIB.patternSourceCount(pattern({ evidence: [], provenance: [] })), 0);

  // References are not sources: two rolled-up pointers that name no file or
  // document must not become "2 sources" on the card.
  const referencesOnly = pattern({
    evidence: [],
    provenance: [
      pointer({ kind: "graph_edge", ref: "e1", doc_id: null, origin_file: null }),
      pointer({ kind: "metric", ref: "centrality:betweenness", doc_id: null, origin_file: null }),
    ],
  });
  assert.equal(LIB.patternSourceCount(referencesOnly), 0);

  const rolledWithFile = pattern({
    evidence: [],
    provenance: [
      pointer({ kind: "metric", ref: "m1", doc_id: null, origin_file: null }),
      pointer({ kind: "source_row", ref: "cdr.csv#row-4", origin_file: "cdr.csv", doc_id: null }),
    ],
  });
  assert.equal(LIB.patternSourceCount(rolledWithFile), 1);

  const evidenceOnly = pattern({ evidence: [evidence()], provenance: [] });
  assert.equal(LIB.patternSourceCount(evidenceOnly), 1);
  assert.equal(LIB.patternSourceCount(null), 0);
});

test("evidence without an openable pointer is not dressed up as a source", () => {
  const item = evidence({
    provenance: [pointer({ kind: "metric", ref: "metric-1", doc_id: null, origin_file: null })],
  });
  assert.equal(LIB.hasOpenableSource([item]), false);
  assert.equal(LIB.hasOpenableSource([evidence()]), true);
  assert.equal(LIB.hasOpenableSource(null), false);
});

// ---------------------------------------------------------------------------
// Provenance navigation (§13/§22)
// ---------------------------------------------------------------------------

test("a pointer opens the surface that owns it, not a document page", () => {
  // A bare pointer: only the fields the kind actually uses.
  const bare = (overrides) =>
    pointer({ doc_id: null, origin_file: null, row_number: null, detail: null, ...overrides });

  // Documents open their own page.
  assert.deepEqual(LIB.provenanceTarget(bare({ kind: "document", doc_id: "doc-7" })), {
    kind: "document",
    to: "/documents/doc-7",
  });
  // Source rows open the file at the row.
  assert.deepEqual(
    LIB.provenanceTarget(bare({ kind: "source_row", origin_file: "cdr.csv", row_number: 12 })),
    { kind: "file", to: "cdr.csv", row: 12, lineStart: null, lineEnd: null },
  );
  // Dataset-level rows, graph edges and metrics open their own surfaces.
  assert.deepEqual(LIB.provenanceTarget(bare({ kind: "dataset" })), {
    kind: "dataset",
    to: "/sources",
  });
  assert.deepEqual(LIB.provenanceTarget(bare({ kind: "graph_edge" }), "c1"), {
    kind: "graph",
    to: "/cases/c1/graph",
  });
  assert.deepEqual(LIB.provenanceTarget(bare({ kind: "metric" }), "c1"), {
    kind: "analytics",
    to: "/cases/c1/investigation",
  });
  // A pointer that names a file opens the file, whatever else it carries: the
  // row is the most specific thing behind the claim.
  assert.equal(
    LIB.provenanceTarget(bare({ kind: "dataset", origin_file: "operational/call_records.csv" })).kind,
    "file",
  );
});

test("a non-document pointer is never routed to a document URL", () => {
  const bare = (overrides) =>
    pointer({ doc_id: null, origin_file: null, row_number: null, detail: null, ...overrides });

  // The negative control: every kind that is not a document must produce a
  // target that is not a /documents/... link, whatever ids it happens to carry.
  const kinds = ["dataset", "metric", "graph_edge", "audit", "note", "source_row"];
  for (const kind of kinds) {
    const target = LIB.provenanceTarget(
      bare({ kind, doc_id: "00000000-0000-0000-0000-000000000000", ref: "dataset:ds-1" }),
      "c1",
    );
    assert.notEqual(target.kind, "document", `${kind} must not open as a document`);
    if (target.to) assert.ok(!target.to.startsWith("/documents/"), `${kind} → ${target.to}`);
  }
  // A document pointer without an id cannot be opened; it degrades to a
  // reference instead of a link that dead-ends.
  assert.deepEqual(LIB.provenanceTarget(bare({ kind: "document" })), {
    kind: "reference",
    to: null,
  });
  // A graph edge or metric with no case in scope is a reference, not a guess.
  assert.equal(LIB.provenanceTarget(bare({ kind: "graph_edge" }), null).kind, "reference");
  assert.equal(LIB.provenanceTarget(bare({ kind: "metric" }), "").kind, "reference");
  assert.equal(LIB.provenanceTarget(null).kind, "reference");
});

test("provenance text names the file when there is one and the label otherwise", () => {
  const bare = (overrides) =>
    pointer({ doc_id: null, origin_file: null, row_number: null, detail: null, ...overrides });

  assert.equal(
    LIB.provenanceText(bare({ kind: "document", doc_id: "d", origin_file: "documents/fir/A.pdf" })),
    "A.pdf",
  );
  assert.equal(
    LIB.provenanceText(bare({ kind: "document", doc_id: "d", origin_file: "A.pdf", detail: "FIR" })),
    "A.pdf · FIR",
  );
  assert.equal(
    LIB.provenanceText(bare({ kind: "source_row", origin_file: "cdr.csv", row_number: 9 })),
    "cdr.csv · row 9",
  );
  assert.equal(LIB.provenanceText(bare({ kind: "metric", label: "betweenness" })), "betweenness");
  assert.equal(LIB.provenanceText(null), null);
});

test("memory surfaces what the thread ruled out, not only what it found", () => {
  const memory = response().memory;
  assert.equal(memory.objective, "Are A and B connected?");
  assert.deepEqual(memory.rejected_hypotheses, []);
  assert.deepEqual(memory.relationships, []);
  const withHistory = response({
    memory: {
      ...memory,
      questions_asked: 2,
      contradictions: ["only one document supports the link"],
      rejected_hypotheses: [{ id: "H1", statement: "s", reason: "single source" }],
      relationships: ["direct: A & B: directly linked (CALLED)"],
      prior_findings: ["WEAK: A is directly linked to B."],
    },
  });
  assert.equal(withHistory.memory.rejected_hypotheses[0].reason, "single source");
  assert.equal(withHistory.memory.contradictions.length, 1);
});

// §22 provenance dispatch -- the console must hand every pointer the case it
// belongs to. A graph-edge or metric reference in a case-scoped answer can only
// open the case surface it names, so a chip rendered without `caseId` degrades
// silently to a dead reference. This reads the workspace source: it is a wiring
// guard, and the runtime rendering of those pointers is covered by smoke.mjs.
{
  const source = readFileSync(
    new URL("../src/pages/InvestigatorWorkspace.tsx", import.meta.url),
    "utf8",
  ).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  const chips = source.match(/<ProvenanceChip[\s\S]{0,160}?\/>/g) || [];
  test("the workspace renders provenance chips", () => assert.ok(chips.length > 0));
  for (const chip of chips) {
    const flat = chip.replace(/\s+/g, " ");
    test(`provenance chip carries the case: ${flat.slice(0, 58)}`, () =>
      assert.match(flat, /caseId=/));
  }
  const lists = source.match(/<EvidenceList[\s\S]{0,240}?\/>/g) || [];
  test("the workspace renders evidence lists", () => assert.ok(lists.length > 1));
  for (const list of lists) {
    test("every evidence list carries the case it belongs to", () =>
      assert.match(list.replace(/\s+/g, " "), /caseId=/));
  }
}
