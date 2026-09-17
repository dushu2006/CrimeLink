/**
 * Evidence / source-opening integration: the console must open real stored
 * files and must not render decorative provenance.
 *
 * The reported defect chain was:
 *   Source evidence  evidence/CR-2007/CR-2007_INTELLIGENCE_REPORT_01.pdf
 *   GET /api/v1/sources/preview?path=...  →  404
 *   modal → "Something went wrong. Active dataset workspace is unavailable."
 *
 * and, behind it, an Evidence panel whose three "✓" provenance ticks were
 * hardcoded literals and whose "Open Original Record" / "Pin Evidence" buttons
 * called optional callbacks no caller ever supplied.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const DRAWER = read("../src/components/investigator/EvidenceDrawer.tsx");
const VIEWER = read("../src/components/SourceViewer.tsx");
const PEOPLE = read("../src/pages/PeoplePage.tsx");
const CASES = read("../src/pages/Cases.tsx");
const WORKSPACE = read("../src/pages/InvestigatorWorkspace.tsx");
const RELATIONSHIPS = read("../src/pages/RelationshipsPage.tsx");

// ---------------------------------------------------------------------------
// The drawer is data-driven, not decorative
// ---------------------------------------------------------------------------

test("evidence drawer fetches the real provenance payload", () => {
  assert.match(DRAWER, /\/evidence\/\$\{encodeURIComponent\(docId\)\}\/provenance/);
  assert.match(DRAWER, /ProvenancePayload/);
  assert.match(DRAWER, /setPayload/);
});

test("provenance ticks are computed, never hardcoded literals", () => {
  // The old version rendered three permanent green ticks.
  assert.doesNotMatch(DRAWER, /className="provenance-check verified">✓ Source verified</);
  assert.doesNotMatch(DRAWER, /className="provenance-check verified">✓ Record available</);
  assert.doesNotMatch(DRAWER, /className="provenance-check verified">✓ Traceable to original</);
  // They come from the server-computed checks instead, with a failure state.
  assert.match(DRAWER, /checks\[key\]/);
  assert.match(DRAWER, /check\.ok \? "✓" : "✗"/);
  assert.match(DRAWER, /const cls = check\.ok \? "verified" : "failed"/);
});

test("an unresolved provenance link says so instead of pretending", () => {
  assert.match(DRAWER, /Provenance unavailable/);
  assert.match(DRAWER, /step\.resolved \? "resolved" : "unresolved"/);
});

test("no field falls back to an invented placeholder", () => {
  // Ignore the header comment, which names the old placeholders on purpose.
  const code = DRAWER.replace(/^\/\*[\s\S]*?\*\//m, "");
  for (const fake of [
    '"Communication record"',
    '"Case record"',
    '"Supports relationship"',
    '"Person relationship"',
    '"Supports relationship"',
  ]) {
    assert.ok(!code.includes(fake), `drawer still hardcodes ${fake}`);
  }
  // A genuinely missing timestamp still says so — that is honest, not invented.
  assert.match(DRAWER, /Timestamp unavailable/);
});

// ---------------------------------------------------------------------------
// "Open Original Record" actually opens the stored file
// ---------------------------------------------------------------------------

test("Open Original Record opens the real file and is disabled when it cannot", () => {
  assert.match(DRAWER, /function openOriginalRecord\(\)/);
  assert.match(DRAWER, /canOpenOriginal = Boolean\(file\?\.available/);
  assert.match(DRAWER, /disabled=\{!canOpenOriginal\}/);
  assert.match(DRAWER, /Original record unavailable/);
  // It mounts the real SourceViewer against the resolved storage path.
  assert.match(DRAWER, /setViewerTarget\(\{\s*kind: "file",\s*path,/);
  assert.match(DRAWER, /<SourceViewer/);
});

test("integrity verification calls the real endpoint", () => {
  assert.match(DRAWER, /\/evidence\/\$\{encodeURIComponent\(docId\)\}\/verify/);
  assert.match(DRAWER, /Verify integrity/);
  assert.match(DRAWER, /HASH MATCHES|HASH MISMATCH/);
});

test("no decorative button survives in the drawer", () => {
  // Pin and View Graph render only when a caller actually supplies a handler.
  assert.match(DRAWER, /\{onPin && \(/);
  assert.match(DRAWER, /onViewGraph && \(/);
});

// ---------------------------------------------------------------------------
// The source viewer hits the real endpoints
// ---------------------------------------------------------------------------

test("source viewer calls the preview and raw endpoints", () => {
  assert.match(VIEWER, /\/sources\/preview\?/);
  assert.match(VIEWER, /\/sources\/raw\?/);
  assert.match(VIEWER, /STATUS_TONE/);
  assert.match(VIEWER, /NOT_FOUND/);
});

// ---------------------------------------------------------------------------
// Pages read the active dataset instead of hardcoding or over-fetching
// ---------------------------------------------------------------------------

test("people page no longer pulls the whole entity graph to count edges", () => {
  assert.doesNotMatch(PEOPLE, /masterGraph\(/);
  assert.match(PEOPLE, /masterPersons\(\)/);
  // A failure is surfaced, not swallowed.
  assert.match(PEOPLE, /setError\(err instanceof Error \? err\.message : String\(err\)\)/);
  assert.match(PEOPLE, /People could not be loaded/);
  assert.doesNotMatch(PEOPLE, /\} catch \{\}/);
});

test("cases page shows the counts the API actually returns", () => {
  assert.match(CASES, /person_count/);
  assert.match(CASES, /relationship_count/);
  assert.match(CASES, /source_count/);
  assert.match(CASES, /finding_count/);
  // Nothing is hardcoded — the row count comes from the response.
  assert.doesNotMatch(CASES, /25 cases/);
});

test("investigate and relationships pages read person-to-person from the API", () => {
  assert.match(WORKSPACE, /relationshipNetwork\(\{ caseId: caseParam/);
  assert.match(RELATIONSHIPS, /relationshipNetwork\(\{/);
  // Neither rebuilds the person graph by filtering the entity graph client-side.
  assert.doesNotMatch(RELATIONSHIPS, /masterGraph\(/);
});
