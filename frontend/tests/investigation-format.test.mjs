/**
 * Unit tests for the investigation workspace's presentation logic — run
 * against the REAL `src/lib/investigation.ts` with Node's type stripping,
 * using payloads in the exact shape the backend serves.
 *
 * The property under test is honesty: a value the backend did not send is
 * never rendered as if it existed.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";

const LIB_URL = new URL("../src/lib/investigation.ts", import.meta.url).href;
const { edgeSpecificRows, relLabel, stageSummary, typeSpecificRows } = await import(
  LIB_URL
);

// ---------------------------------------------------------------------------
// Fixtures in the exact wire shape of the backend's graph + stage endpoints
// ---------------------------------------------------------------------------

function personNode(overrides = {}) {
  return {
    provenance_key: "pk-person-1",
    label: "PERSON",
    name: "Asha Reddy",
    confidence: 0.95,
    case_ids: ["c1"],
    source_doc_ids: ["doc1"],
    aliases: ["Asha R"],
    staging: false,
    is_active: true,
    evidence: null,
    properties: { role: "SUSPECT", phone: "9812345670" },
    ...overrides,
  };
}

function transferEdge(overrides = {}) {
  return {
    key: "e1",
    source: "pk-acct-1",
    target: "pk-acct-2",
    rel_type: "TRANSFER_TO",
    confidence: 1,
    source_doc_ids: ["doc9"],
    source_doc_id: "doc9",
    staging: false,
    evidence: null,
    properties: {
      amount: 45000,
      ts: "2026-01-11T09:15:00+05:30",
      channel: "IMPS",
      reference: "IMPS-7788",
    },
    ...overrides,
  };
}

const completedStage = {
  stage: 2,
  key: "extract_entities",
  label: "Extract Entities",
  requires: [1],
  status: "COMPLETED",
  detail: {
    documents_parsed: 4,
    deterministic_entities: 21,
    nlp_entities: 2,
    candidate_relations: 6,
  },
  error: null,
  attempt_count: 1,
  finished_at: "2026-09-04T10:00:00Z",
  duration_ms: 4210,
  runnable: true,
  blocked_by: [],
};

// ---------------------------------------------------------------------------
// stageSummary — the stage row must report what actually happened
// ---------------------------------------------------------------------------

test("stageSummary reports the real counters of a completed stage", () => {
  const summary = stageSummary(completedStage);
  assert.match(summary, /21 deterministic entities/);
  assert.match(summary, /2 NLP entities/);
  assert.match(summary, /6 candidate relations/);
  // No invented counters:
  assert.doesNotMatch(summary, /findings/);
});

test("stageSummary surfaces the stage error verbatim on failure", () => {
  const summary = stageSummary({
    ...completedStage,
    status: "FAILED",
    error: "RuntimeError: object store exploded",
  });
  assert.equal(summary, "RuntimeError: object store exploded");
});

test("stageSummary states when the AI role is unavailable — no pretending", () => {
  const summary = stageSummary({
    ...completedStage,
    detail: { ai_available: false },
  });
  assert.match(summary, /AI unavailable/);
  assert.doesNotMatch(summary, /AI role available/);
});

test("stageSummary is empty for a pending stage — no fabricated progress", () => {
  assert.equal(
    stageSummary({ ...completedStage, status: "PENDING", detail: {} }),
    "",
  );
});

// ---------------------------------------------------------------------------
// type-specific node rows — only fields the node really carries
// ---------------------------------------------------------------------------

test("person rows show role and phone, and skip fields that are absent", () => {
  const rows = typeSpecificRows(personNode());
  assert.deepEqual(rows, [
    ["role", "SUSPECT"],
    ["phone", "9812345670"],
  ]);
});

test("bank account rows prefer the real account number and bank code", () => {
  const rows = typeSpecificRows(
    personNode({
      label: "BANK_ACCOUNT",
      name: "XX10000001",
      properties: { account: "XX10000001", bank_code: "HDFC" },
    }),
  );
  assert.deepEqual(rows, [
    ["account", "XX10000001"],
    ["bank", "HDFC"],
  ]);
});

// ---------------------------------------------------------------------------
// edge rows — money and calls carry their evidence-bearing attributes
// ---------------------------------------------------------------------------

test("transfer edges expose amount, timestamp, channel and reference", () => {
  const rows = edgeSpecificRows(transferEdge());
  assert.deepEqual(rows, [
    ["amount (INR)", "45000"],
    ["timestamp", "2026-01-11T09:15:00+05:30"],
    ["channel", "IMPS"],
    ["reference", "IMPS-7788"],
  ]);
});

test("call edges expose counts and durations when present", () => {
  const rows = edgeSpecificRows(
    transferEdge({
      rel_type: "CALLED",
      properties: { call_count: 4, total_duration_seconds: 872 },
    }),
  );
  assert.deepEqual(rows, [
    ["calls", "4"],
    ["total duration (s)", "872"],
  ]);
});

// ---------------------------------------------------------------------------
// relLabel
// ---------------------------------------------------------------------------

test("relationship labels are human readable", () => {
  assert.equal(relLabel("TRANSFER_TO"), "transfer to");
  assert.equal(relLabel("OWNS_ACCOUNT"), "owns account");
});
