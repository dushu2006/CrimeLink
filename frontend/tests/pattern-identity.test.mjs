import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const LIB_URL = new URL("../src/lib/pattern-identity.ts", import.meta.url).href;
const { patternIdentity } = await import(LIB_URL);

function pointer(overrides = {}) {
  return {
    kind: "document",
    ref: "doc-1",
    label: "05_cctv_log.csv",
    detail: "CCTV",
    doc_id: "doc-1",
    origin_file: "05_cctv_log.csv",
    row_number: null,
    line_start: null,
    line_end: null,
    content_hash: null,
    ...overrides,
  };
}

function pattern(overrides = {}) {
  return {
    kind: "CROSS_CASE_ENTITY",
    title: "05_cctv_log.csv appears in 5 cases",
    explanation: "Same source record appears across cases.",
    entities: ["05_cctv_log.csv"],
    entity_keys: ["ds:1:DOCUMENT:file-a"],
    cases: ["case-1", "case-2", "case-3", "case-4", "case-5"],
    time_range: { start: null, end: null },
    evidence: [{ provenance: [pointer()] }],
    inference_label: "LEAD",
    strength: "WEAK",
    strength_factors: {},
    contradictions_considered: [],
    innocent_alternatives: [],
    excluded: false,
    exclusion_reason: null,
    provenance: [pointer()],
    ...overrides,
  };
}

test("same filename/title in different canonical entities receives different stable keys", () => {
  const first = pattern({ entity_keys: ["ds:1:DOCUMENT:file-a"] });
  const second = pattern({ entity_keys: ["ds:1:DOCUMENT:file-b"] });
  assert.notEqual(patternIdentity(first), patternIdentity(second));
});

test("case and source references are included, so identical display titles do not collide", () => {
  const first = pattern({
    entity_keys: ["ds:1:DOCUMENT:file-a"],
    cases: ["case-1", "case-2"],
    provenance: [pointer({ ref: "doc-a", doc_id: "doc-a" })],
    evidence: [{ provenance: [pointer({ ref: "doc-a", doc_id: "doc-a" })] }],
  });
  const second = pattern({
    entity_keys: ["ds:1:DOCUMENT:file-a"],
    cases: ["case-3", "case-4"],
    provenance: [pointer({ ref: "doc-b", doc_id: "doc-b" })],
    evidence: [{ provenance: [pointer({ ref: "doc-b", doc_id: "doc-b" })] }],
  });
  assert.notEqual(patternIdentity(first), patternIdentity(second));
});

test("equivalent payloads keep the same key regardless of ordering", () => {
  const first = pattern({
    entity_keys: ["entity-b", "entity-a"],
    cases: ["case-2", "case-1"],
    provenance: [
      pointer({ ref: "doc-b", doc_id: "doc-b" }),
      pointer({ ref: "doc-a", doc_id: "doc-a" }),
    ],
    evidence: [
      { provenance: [pointer({ ref: "doc-b", doc_id: "doc-b" })] },
      { provenance: [pointer({ ref: "doc-a", doc_id: "doc-a" })] },
    ],
  });
  const sameLogicalPattern = pattern({
    entity_keys: ["entity-a", "entity-b"],
    cases: ["case-1", "case-2"],
    provenance: [
      pointer({ ref: "doc-a", doc_id: "doc-a" }),
      pointer({ ref: "doc-b", doc_id: "doc-b" }),
    ],
    evidence: [
      { provenance: [pointer({ ref: "doc-a", doc_id: "doc-a" })] },
      { provenance: [pointer({ ref: "doc-b", doc_id: "doc-b" })] },
    ],
  });
  assert.equal(patternIdentity(first), patternIdentity(sameLogicalPattern));
});

test("PatternList no longer uses kind-title as its React key", () => {
  const source = readFileSync(
    new URL("../src/components/investigator/PatternCard.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /patternIdentity\(pattern\)/);
  assert.doesNotMatch(source, /key=\{`\$\{pattern\.kind\}-\$\{pattern\.title\}`\}/);
});
