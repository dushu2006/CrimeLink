/**
 * CASE INTELLIGENCE header — the metrics must be the authoritative ones.
 *
 * The reported regression: for CR-2020 the assistant's own case context
 * reported 12 documents, 11 evidence types, 10 people, 65 relationships and a
 * dated timeline, while the header above it read
 *
 *   Evidence       12 verified records · 0 evidence types
 *   Entities       0 entities
 *   Relationships  0 case-scoped relationships
 *   Timeline       First recorded: N/A / Latest recorded: N/A
 *
 * Root cause: ``GET /ai/cases/{id}/context`` failed (a frozen dataclass was
 * being mutated server-side), the panel fell back to ``GET /cases/{id}``,
 * and the fallback *fabricated* every metric it did not have as ``0``/``N/A``
 * — presenting an outage as facts about the evidence.
 *
 * These tests pin both halves of the fix on the console side:
 *   - the header derives its cells from the authoritative payload, field by
 *     field, with no coercion of a missing value to zero;
 *   - the degraded context states no metric at all.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const LIB = await import(new URL("../src/lib/caseIntelligence.ts", import.meta.url).href);
const CHAT = read("../src/components/investigator/CaseRagChat.tsx");

// ---------------------------------------------------------------------------
// Authoritative payloads, shaped exactly like CaseAIContext.as_summary_dict()
// ---------------------------------------------------------------------------

/** CR-2020 as the assistant's own case-scoped context reports it. */
const CR_2020 = {
  case_id: "case-d2-019",
  case_number: "CR-2020",
  title: "Tender manipulation and bribery at a ward office",
  case_title: "Tender manipulation and bribery at a ward office",
  status: "OPEN",
  jurisdiction: "METRO-CENTRAL",
  stats: {
    documents_indexed: 12,
    document_count: 12,
    evidence_count: 12,
    evidence_types_count: 11,
    evidence_types: [
      "ANPR", "ARREST_RECORD", "BAIL_RECORD", "CASE_DIARY", "CDR", "CHARGE_SHEET",
      "FINANCIAL", "FIR", "FORENSIC", "SURVEILLANCE", "WITNESS_STATEMENT",
    ],
    entities_extracted: 31,
    entity_count: 31,
    person_count: 10,
    relationships_mapped: 65,
    relationship_count: 65,
    entity_counts_by_type: { people: 10, phones: 8, accounts: 6, vehicles: 3, locations: 2, organizations: 2 },
    coverage_percent: 100,
    confidence_score: 1.0,
  },
  timeline: { first_recorded: "Jan 12, 2026", latest_recorded: "Mar 08, 2026", event_count: 28 },
  timeline_summary: {
    first_recorded: "Jan 12, 2026",
    latest_recorded: "Mar 08, 2026",
    first_recorded_raw: "2026-01-12T09:15:00+00:00",
    latest_recorded_raw: "2026-03-08T18:40:00+00:00",
    total_events: 28,
  },
  suggested_questions: ["Who are the key people in this case?"],
  missing_evidence_types: [],
  evidence_count: 12,
  canonical_entities_count: 31,
};

/** A second, differently shaped case: the numbers must follow the payload. */
const CR_2001 = {
  case_id: "case-d2-000",
  case_number: "CR-2001",
  title: "Organised extortion ring",
  stats: {
    documents_indexed: 5,
    evidence_count: 5,
    evidence_types_count: 5,
    evidence_types: ["ANPR", "CDR", "FINANCIAL", "FIR", "WITNESS_STATEMENT"],
    entities_extracted: 9,
    entity_count: 9,
    person_count: 4,
    relationships_mapped: 14,
    relationship_count: 14,
    entity_counts_by_type: { people: 4, phones: 2, accounts: 2, vehicles: 1 },
  },
  timeline: { first_recorded: "Aug 01, 2024", latest_recorded: "Aug 28, 2024", event_count: 22 },
  suggested_questions: [],
  evidence_count: 5,
  canonical_entities_count: 9,
};

/** A genuinely empty case: the backend states every metric as zero / N/A. */
const EMPTY = {
  case_id: "case-empty",
  case_number: "CR-0000",
  title: "Newly registered case",
  stats: {
    documents_indexed: 0,
    document_count: 0,
    evidence_count: 0,
    evidence_types_count: 0,
    evidence_types: [],
    entities_extracted: 0,
    entity_count: 0,
    person_count: 0,
    relationships_mapped: 0,
    relationship_count: 0,
    entity_counts_by_type: {},
    coverage_percent: 0,
    confidence_score: 1.0,
  },
  timeline: { first_recorded: "N/A", latest_recorded: "N/A", event_count: 0 },
  suggested_questions: [],
  evidence_count: 0,
  canonical_entities_count: 0,
};

// ---------------------------------------------------------------------------
// 1. CR-2020 renders its authoritative, non-zero metrics
// ---------------------------------------------------------------------------

test("CR-2020: every header cell shows the authoritative non-zero figure", () => {
  const m = LIB.deriveCaseIntelligenceMetrics(CR_2020);
  assert.equal(m.available, true);
  assert.equal(m.documents, 12);
  assert.equal(m.evidenceTypes, 11);
  assert.equal(m.entities, 31);
  assert.equal(m.relationships, 65);
  assert.equal(m.evidenceLabel, "12 verified records · 11 evidence types");
  assert.equal(
    m.entitiesLabel,
    "10 people · 8 phones · 6 accounts · 3 vehicles · 2 locations · 2 organizations",
  );
  assert.equal(m.relationshipsLabel, "65 case-scoped relationships");
  assert.equal(m.firstRecorded, "Jan 12, 2026");
  assert.equal(m.latestRecorded, "Mar 08, 2026");
});

test("CR-2020: the regression's exact zero/N-A rendering can no longer be produced from its payload", () => {
  const m = LIB.deriveCaseIntelligenceMetrics(CR_2020);
  assert.notEqual(m.evidenceLabel, "12 verified records · 0 evidence types");
  assert.notEqual(m.entitiesLabel, "0 entities");
  assert.notEqual(m.relationshipsLabel, "0 case-scoped relationships");
  assert.notEqual(m.firstRecorded, "N/A");
  assert.notEqual(m.latestRecorded, "N/A");
});

// ---------------------------------------------------------------------------
// 2. A second case follows its own payload — nothing is hardcoded
// ---------------------------------------------------------------------------

test("CR-2001: a differently populated case shows its own non-zero metrics", () => {
  const m = LIB.deriveCaseIntelligenceMetrics(CR_2001);
  assert.equal(m.available, true);
  assert.equal(m.evidenceLabel, "5 verified records · 5 evidence types");
  assert.equal(m.entitiesLabel, "4 people · 2 phones · 2 accounts · 1 vehicle");
  assert.equal(m.relationshipsLabel, "14 case-scoped relationships");
  assert.equal(m.firstRecorded, "Aug 01, 2024");
  assert.equal(m.latestRecorded, "Aug 28, 2024");
});

test("the derivation is a pure function of the payload", () => {
  const a = LIB.deriveCaseIntelligenceMetrics(CR_2020);
  const b = LIB.deriveCaseIntelligenceMetrics(structuredClone(CR_2020));
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, LIB.deriveCaseIntelligenceMetrics(CR_2001));
});

// ---------------------------------------------------------------------------
// 3. A genuinely empty case still reads zero / N/A
// ---------------------------------------------------------------------------

test("an empty case displays explicit zeros and N/A", () => {
  const m = LIB.deriveCaseIntelligenceMetrics(EMPTY);
  assert.equal(m.available, true);
  assert.equal(m.evidenceLabel, "0 verified records · 0 evidence types");
  assert.equal(m.entitiesLabel, "0 entities");
  assert.equal(m.relationshipsLabel, "0 case-scoped relationships");
  assert.equal(m.firstRecorded, "N/A");
  assert.equal(m.latestRecorded, "N/A");
});

test("a missing timeline bound reads N/A, never an empty string or placeholder", () => {
  const noDates = { ...EMPTY, timeline: { first_recorded: null, latest_recorded: "", event_count: 0 } };
  const m = LIB.deriveCaseIntelligenceMetrics(noDates);
  assert.equal(m.firstRecorded, "N/A");
  assert.equal(m.latestRecorded, "N/A");
});

// ---------------------------------------------------------------------------
// 4. Field mapping: synonyms are honoured, absence is never coerced to zero
// ---------------------------------------------------------------------------

test("legacy/synonym field names resolve to the same cells", () => {
  const legacy = {
    case_id: "c", case_number: "CR-2020", title: "t",
    stats: {
      document_count: 12,
      evidence_types: ["FIR", "CDR", "ANPR"],
      entity_count: 31,
      relationship_count: 65,
      entity_counts_by_type: { person: 10, phone: 8 },
    },
    timeline_summary: { first_recorded: "Jan 12, 2026", latest_recorded: "Mar 08, 2026" },
    suggested_questions: [],
  };
  const m = LIB.deriveCaseIntelligenceMetrics(legacy);
  assert.equal(m.evidenceLabel, "12 verified records · 3 evidence types");
  assert.equal(m.entitiesLabel, "10 people · 8 phones");
  assert.equal(m.relationshipsLabel, "65 case-scoped relationships");
  assert.equal(m.firstRecorded, "Jan 12, 2026");
  assert.equal(m.latestRecorded, "Mar 08, 2026");
});

test("a metric the payload does not state is shown as unknown, not as 0", () => {
  const partial = {
    case_id: "c", case_number: "CR-2020", title: "t",
    stats: { documents_indexed: 12 },
    suggested_questions: [],
  };
  const m = LIB.deriveCaseIntelligenceMetrics(partial);
  assert.equal(m.documents, 12);
  assert.equal(m.evidenceTypes, null);
  assert.equal(m.entities, null);
  assert.equal(m.relationships, null);
  assert.equal(m.evidenceLabel, `12 verified records · ${LIB.UNKNOWN_METRIC} evidence types`);
  assert.equal(m.entitiesLabel, `${LIB.UNKNOWN_METRIC} entities`);
  assert.equal(m.relationshipsLabel, `${LIB.UNKNOWN_METRIC} case-scoped relationships`);
  assert.doesNotMatch(m.evidenceLabel, /\b0 evidence types/);
  assert.notEqual(m.entitiesLabel, "0 entities");
  assert.notEqual(m.relationshipsLabel, "0 case-scoped relationships");
});

test("an explicit zero is kept as zero even when a synonym field is populated", () => {
  // `??`, not `||`: 0 relationships_mapped must not be replaced by a fallback.
  const m = LIB.deriveCaseIntelligenceMetrics({
    case_id: "c", case_number: "x", title: "t",
    stats: { documents_indexed: 0, evidence_count: 7, relationships_mapped: 0, relationship_count: 9 },
    suggested_questions: [],
  });
  assert.equal(m.documents, 0);
  assert.equal(m.relationships, 0);
});

test("entity units pluralise correctly for the backend's plural keys and for singletons", () => {
  assert.equal(LIB.formatEntityCounts({ people: 1 }, 1), "1 person");
  assert.equal(LIB.formatEntityCounts({ people: 10, accounts: 1 }, 11), "10 people · 1 account");
  assert.equal(LIB.formatEntityCounts({ organizations: 2, companies: 1 }, 3), "2 organizations · 1 company");
  assert.equal(LIB.formatEntityCounts({ events: 3 }, 3), "3 events");
  assert.equal(LIB.formatEntityCounts({}, 4), "4 entities");
  assert.equal(LIB.formatEntityCounts({}, 1), "1 entity");
  assert.equal(LIB.formatEntityCounts({ people: 0 }, 0), "0 entities");
  assert.doesNotMatch(LIB.formatEntityCounts({ people: 10, accounts: 3 }, 13), /peoples|accountss/);
});

// ---------------------------------------------------------------------------
// 5. The degraded context states no metric
// ---------------------------------------------------------------------------

test("when the authoritative endpoint fails the header marks metrics unavailable instead of fabricating zeros", () => {
  const degraded = LIB.degradedCaseContext("case-d2-019", { case_number: "CR-2020", title: "Tender manipulation" });
  assert.equal(degraded.case_number, "CR-2020");
  assert.equal(degraded.title, "Tender manipulation");
  assert.equal(degraded.metrics_available, false);
  assert.equal(degraded.stats, null);
  assert.equal(degraded.timeline, null);
  assert.ok(degraded.suggested_questions.length > 0, "the panel stays usable");

  const m = LIB.deriveCaseIntelligenceMetrics(degraded);
  assert.equal(m.available, false);
  assert.equal(m.documents, null);
  assert.equal(m.evidenceTypes, null);
  assert.equal(m.entities, null);
  assert.equal(m.relationships, null);
  for (const label of [m.evidenceLabel, m.entitiesLabel, m.relationshipsLabel]) {
    assert.doesNotMatch(label, /\b0\b/, "no fabricated zero may reach the header");
  }
});

test("no context at all is also 'unavailable', not zero", () => {
  assert.equal(LIB.deriveCaseIntelligenceMetrics(null).available, false);
  assert.equal(LIB.deriveCaseIntelligenceMetrics(undefined).available, false);
});

// ---------------------------------------------------------------------------
// 6. The component wires the header to the authoritative derivation
// ---------------------------------------------------------------------------

test("CaseRagChat derives the header from the authoritative case context", () => {
  assert.match(CHAT, /deriveCaseIntelligenceMetrics\(caseContext\)/);
  assert.match(CHAT, /\/ai\/cases\/\$\{encodeURIComponent\(caseId\)\}\/context/);
  assert.match(CHAT, /\{metrics\.evidenceLabel\}/);
  assert.match(CHAT, /\{metrics\.entitiesLabel\}/);
  assert.match(CHAT, /\{metrics\.relationshipsLabel\}/);
  assert.match(CHAT, /First recorded: \{metrics\.firstRecorded\}/);
  assert.match(CHAT, /Latest recorded: \{metrics\.latestRecorded\}/);
});

test("CaseRagChat no longer fabricates zero/N-A metrics in its fallback", () => {
  assert.match(CHAT, /degradedCaseContext\(caseId, caseData\)/);
  assert.doesNotMatch(CHAT, /evidence_types_count:\s*0/);
  assert.doesNotMatch(CHAT, /entities_extracted:\s*0/);
  assert.doesNotMatch(CHAT, /relationships_mapped:\s*0/);
  assert.doesNotMatch(CHAT, /canonical_entities_count:\s*0/);
  assert.doesNotMatch(CHAT, /first_recorded:\s*"N\/A"/);
  assert.doesNotMatch(CHAT, /latest_recorded:\s*"N\/A"/);
  assert.doesNotMatch(CHAT, /document_count \|\| 0/);
  assert.match(CHAT, /Metrics unavailable/);
});

test("the header never derives a metric from the assistant's answer", () => {
  const start = CHAT.indexOf('<span className="case-ai-kicker">CASE INTELLIGENCE</span>');
  const end = CHAT.indexOf("{/* Dynamic Case-Tailored Suggested Questions */}");
  assert.ok(start > 0 && end > start, "header block located");
  const header = CHAT.slice(start, end);
  assert.doesNotMatch(header, /\banswer\b/);
  assert.doesNotMatch(header, /\bfinding\b/);
  assert.doesNotMatch(header, /evidence_coverage|claims\.length|why_this_answer/);
});

test("the header re-requests the authoritative context once the assistant has answered", () => {
  assert.match(CHAT, /rehydrateMetricsIfDegraded\(\)/);
  assert.match(CHAT, /if \(!metricsAvailableRef\.current\) void loadCaseContext\(\)/);
});

test("the UI design is preserved: same card, kicker, grid and cell labels", () => {
  for (const cls of ["case-ai-context-card", "case-ai-context-top", "case-ai-kicker", "case-ai-title", "case-ai-stat-grid", "case-ai-stat-item", "case-ai-stat-label", "case-ai-stat-val"]) {
    assert.match(CHAT, new RegExp(`className="${cls}"`), `${cls} still rendered`);
  }
  assert.match(CHAT, />CASE INTELLIGENCE</);
  for (const label of ["Evidence", "Entities", "Relationships", "Timeline"]) {
    assert.match(CHAT, new RegExp(`<span className="case-ai-stat-label">${label}</span>`));
  }
  assert.match(LIB.deriveCaseIntelligenceMetrics(CR_2020).evidenceLabel, /^\d+ verified records · \d+ evidence types$/);
  assert.match(LIB.deriveCaseIntelligenceMetrics(CR_2020).relationshipsLabel, /^\d+ case-scoped relationships$/);
});
