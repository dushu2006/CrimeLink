/**
 * Targeted tests for the three-scope network analysis surface.
 *
 * Runs against the REAL `src/lib/investigator.ts` with Node's type stripping,
 * pinning the presentation rules the three scopes rely on:
 *
 *   * each scope renders its own label (master / case / person),
 *   * a "document" provenance pointer stays a document (it opens in the
 *     reusable source viewer — the mapping to a document route must never be
 *     silently replaced with a file that would dead-end),
 *   * provenance text never invents a position the pointer does not carry.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";

const LIB_URL = new URL("../src/lib/investigator.ts", import.meta.url).href;
const LIB = await import(LIB_URL);

function pointer(overrides = {}) {
  return {
    kind: "document",
    ref: "doc-7",
    label: "cdr.csv",
    detail: null,
    doc_id: "doc-7",
    origin_file: null,
    row_number: null,
    line_start: null,
    line_end: null,
    content_hash: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// scope labels: three distinct, non-interchangeable scopes
// ---------------------------------------------------------------------------

test("person scope renders its own label, distinct from master and case", () => {
  assert.equal(
    LIB.scopeLabel({ mode: "person", label: "", case_number: null, case_id: null }),
    "Person Network",
  );
  assert.equal(
    LIB.scopeLabel({ mode: "person", label: "Person Sana Iyer", case_number: null, case_id: null }),
    "Person Sana Iyer",
  );
  assert.equal(LIB.scopeLabel({ mode: "master", label: "" }), "Master Network");
  assert.equal(
    LIB.scopeLabel({ mode: "case", label: "", case_number: "C106", case_id: "id" }),
    "Case C106",
  );
});

// ---------------------------------------------------------------------------
// provenance: document pointers stay documents (openable in the shared viewer)
// ---------------------------------------------------------------------------

test("a document pointer maps to the document surface, never a dead-end", () => {
  assert.deepEqual(LIB.provenanceTarget(pointer({ kind: "document", doc_id: "doc-7" })), {
    kind: "document",
    to: "/documents/doc-7",
  });
});

test("a document pointer without a doc id is not dressed up as a link", () => {
  assert.deepEqual(
    LIB.provenanceTarget(pointer({ kind: "document", doc_id: null })),
    { kind: "reference", to: null },
  );
});

test("provenance text reports only the position the pointer actually carries", () => {
  assert.equal(
    LIB.provenanceText(pointer({ origin_file: "raw/cdr.csv", row_number: 18342 })),
    "cdr.csv · row 18342",
  );
  // No fabricated row:
  assert.equal(LIB.provenanceText(pointer({ origin_file: "raw/cdr.csv" })), "cdr.csv");
});
