/**
 * Regression test for the Admin "documents" crash:
 *
 *   TypeError: Cannot read properties of null (reading 'slice')
 *   Admin.tsx → documents tab → `d.case_id.slice(0, 8)`
 *
 * Dataset-level documents legitimately carry `case_id: null` (the pipeline
 * attaches them to the dataset, never to the synthetic container case), and
 * the production "Upload of 577 files v1" dataset is 100% dataset-level —
 * every row of the admin documents table hit the null.
 *
 * The table now renders nullable identifiers through `src/lib/nullSafe.ts`;
 * this test runs the REAL module (Node type stripping, no build step) and
 * pins down that no nullable value can throw and that the display of an
 * existing value is unchanged.
 *
 *   node --experimental-strip-types --test tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const { DASH, shortId, fixed } = await import(
  new URL("../src/lib/nullSafe.ts", import.meta.url).href
);

// ---------------------------------------------------------------------------
// shortId — the exact expression the documents table used to crash on
// ---------------------------------------------------------------------------

test("shortId renders null as a dash instead of throwing (the production crash)", () => {
  // The documents tab renders `shortId(d.case_id, 8)`; d.case_id is null for
  // every dataset-level document.
  assert.doesNotThrow(() => shortId(null, 8));
  assert.equal(shortId(null, 8), DASH);
  assert.equal(shortId(undefined, 8), DASH);
  assert.equal(shortId("", 8), DASH);
});

test("shortId truncates long identifiers exactly as the old slice did", () => {
  const uuid = "a7393a81-48cb-4062-a02d-1ce527c982bb";
  // Old behaviour: uuid.slice(0, 8) + "…"
  assert.equal(shortId(uuid, 8), `${uuid.slice(0, 8)}…`);
  assert.equal(shortId(uuid, 16), `${uuid.slice(0, 16)}…`);
});

test("shortId leaves short identifiers untouched (no bogus ellipsis)", () => {
  assert.equal(shortId("abc", 8), "abc");
  assert.equal(shortId("12345678", 8), "12345678");
});

test("shortId tolerates unexpected non-string values instead of throwing", () => {
  // A future API change must not take the admin table down.
  assert.equal(shortId(123456789012, 8), "12345678…");
});

// ---------------------------------------------------------------------------
// fixed — numeric cells (confidence, sizes)
// ---------------------------------------------------------------------------

test("fixed formats numbers and renders null/undefined as a dash", () => {
  assert.equal(fixed(0.987, 2), "0.99");
  assert.equal(fixed(1, 2), "1.00");
  assert.equal(fixed(null, 2), DASH);
  assert.equal(fixed(undefined, 2), DASH);
  assert.equal(fixed(Number.NaN, 2), DASH);
});

// ---------------------------------------------------------------------------
// The full documents row shape from /admin/database/documents
// ---------------------------------------------------------------------------

test("an all-null-case document row renders without throwing", () => {
  // Shape returned by GET /api/v1/admin/database/documents for a dataset-level
  // document (case_id is null — the field the production error was thrown on).
  const rows = [
    {
      id: "doc-0001",
      case_id: null,
      document_type: "OTHER",
      filename: "ledger_0001.csv",
      size_bytes: 2048,
      ingestion_status: "COMPLETED",
      quarantined: false,
      created_at: null,
    },
    {
      id: "doc-0002",
      case_id: "a7393a81-48cb-4062-a02d-1ce527c982bb",
      document_type: "BANK",
      filename: "bank_stmt.pdf",
      size_bytes: 65536,
      ingestion_status: "COMPLETED",
      quarantined: false,
      created_at: "2026-09-01T10:00:00Z",
    },
  ];

  // The exact cell expressions of the Admin documents table.
  for (const d of rows) {
    const cells = [
      d.filename,
      d.document_type,
      shortId(d.case_id, 8), // was: d.case_id.slice(0, 8)  ← the crash
      d.ingestion_status,
      (d.size_bytes / 1024).toFixed(1),
    ];
    for (const cell of cells) {
      assert.notEqual(cell, undefined, "no cell may be undefined");
      assert.doesNotThrow(() => String(cell));
    }
  }
  // The null-case row shows the dash; the real-case row shows the short id.
  assert.equal(shortId(rows[0].case_id, 8), DASH);
  assert.equal(shortId(rows[1].case_id, 8), "a7393a81…");
});
