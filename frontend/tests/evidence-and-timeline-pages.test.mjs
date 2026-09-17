/**
 * The Evidence and Timeline pages must be built from stored records.
 *
 * Both pages used to pull the 575-node master graph and then manufacture the
 * fields a list needs:
 *
 *   id:        `E-${String(i + 42).padStart(3, "0")}`   — an id that exists
 *                                                         nowhere in the data
 *   confidence: e.confidence || 0.8                      — invented
 *   timestamp:  "Timestamp unavailable"                  — for every row
 *   plus two permanent "✓ Source verified" / "✓ Record available" ticks.
 *
 * Clicking a card opened the drawer with the *invented* id, so provenance
 * could never resolve.  Both pages also swallowed their request failure with
 * `catch {}` and rendered an empty grid — "there is no evidence" instead of
 * "the request failed".
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const EVIDENCE = read("../src/pages/EvidencePage.tsx");
const TIMELINE = read("../src/pages/TimelinePage.tsx");
const RELATIONSHIPS = read("../src/pages/RelationshipsPage.tsx");
const CASE_DASHBOARD = read("../src/pages/CaseDashboardPage.tsx");
const GRAPH = read("../src/pages/GraphPage.tsx");
const CLIENT = read("../src/api/client.ts");

const NO_FABRICATED_ID = /E-\$\{String\(i \+ 42\)/;

// ---------------------------------------------------------------------------
// Evidence page
// ---------------------------------------------------------------------------

test("evidence page lists real stored documents, not graph edges", () => {
  assert.match(EVIDENCE, /evidenceDocuments\(/);
  assert.match(CLIENT, /\/explore\/documents\?/);
  assert.doesNotMatch(EVIDENCE, /masterGraph\(\)/, "must not scrape the entity graph");
});

test("evidence page fabricates no id, confidence or tick", () => {
  assert.doesNotMatch(EVIDENCE, NO_FABRICATED_ID);
  assert.doesNotMatch(EVIDENCE, /doc-\$\{i\}/);
  assert.doesNotMatch(EVIDENCE, /\|\| 0\.8/);
  assert.doesNotMatch(EVIDENCE, /✓ Source verified/);
  assert.doesNotMatch(EVIDENCE, /✓ Record available/);
});

test("evidence page surfaces a failed request instead of an empty grid", () => {
  assert.doesNotMatch(EVIDENCE, /catch\s*\{\s*\}/);
  assert.match(EVIDENCE, /setError\(err\.message\)/);
  assert.match(EVIDENCE, /Could not load evidence/);
  assert.match(EVIDENCE, /Retry/);
});

test("evidence page paginates and filters through the API", () => {
  assert.match(EVIDENCE, /offset/);
  assert.match(EVIDENCE, /update\(\{ q: event\.target\.value \}\)/);
  assert.match(EVIDENCE, /total\.toLocaleString\(\)/);
});

// ---------------------------------------------------------------------------
// Timeline page
// ---------------------------------------------------------------------------

test("timeline page reads the real per-case timeline endpoint", () => {
  assert.match(TIMELINE, /enhancedTimeline\(/);
  assert.match(CLIENT, /\/cases\/\$\{encodeURIComponent\(caseId\)\}\/timeline/);
  assert.doesNotMatch(TIMELINE, /masterGraph\(\)/);
});

test("timeline page fabricates no event id or date", () => {
  assert.doesNotMatch(TIMELINE, NO_FABRICATED_ID);
  assert.doesNotMatch(TIMELINE, /const date = "Unknown date"/);
  // "Timestamp unavailable" is allowed only as a fallback for a record that
  // genuinely has none — never as the value for every row.
  assert.match(TIMELINE, /if \(!timestamp\) return "Timestamp unavailable"/);
  assert.match(TIMELINE, /parsed\.toLocaleTimeString/);
});

test("timeline page opens the real document behind an event", () => {
  assert.match(TIMELINE, /evidence_doc_ids/);
  assert.match(TIMELINE, /setDrawerDocId\(ev\.docIds\[0\]\)/);
  assert.match(TIMELINE, /No source document recorded/);
});

test("timeline page states that it needs a case rather than inventing one", () => {
  assert.match(TIMELINE, /Choose a case/);
  assert.match(TIMELINE, /The timeline is built from a case's stored records/);
});

test("timeline page surfaces a failed request", () => {
  assert.doesNotMatch(TIMELINE, /catch\s*\{\s*\}/);
  assert.match(TIMELINE, /setError\(err\.message\)/);
  assert.match(TIMELINE, /Could not load the timeline/);
});

// ---------------------------------------------------------------------------
// No page hides a failure behind empty data
// ---------------------------------------------------------------------------

test("no audited page swallows a request failure", () => {
  for (const [name, source] of Object.entries({
    EVIDENCE,
    TIMELINE,
    RELATIONSHIPS,
    CASE_DASHBOARD,
    GRAPH,
  })) {
    assert.doesNotMatch(source, /catch\s*\{\s*\}/, `${name} swallows an error`);
    assert.doesNotMatch(source, /\.catch\(\(\) => set\w+\(\[\]\)\)/, `${name} fakes empty data`);
  }
});

test("relationships and dashboard pages report the failure they got", () => {
  assert.match(RELATIONSHIPS, /Could not load the relationship network/);
  assert.match(CASE_DASHBOARD, /Could not load the timeline/);
  assert.match(CASE_DASHBOARD, /setTimelineError\(err\.message\)/);
});

test("graph path search distinguishes 'no path' from 'request failed'", () => {
  assert.match(GRAPH, /setPathError\(err\.message\)/);
  assert.match(GRAPH, /pathError &&/);
});

// ---------------------------------------------------------------------------
// Global search: a deep link must actually search
// ---------------------------------------------------------------------------

const SEARCH_PAGE = read("../src/pages/GlobalSearchPage.tsx");
const SEARCH_BOX = read("../src/components/investigator/GlobalSearch.tsx");

test("global search runs the linked query through the real search path", () => {
  // The page used to fetch the result into state it never rendered, and to
  // swallow the failure with .catch(() => {}).  The box now owns it, so the
  // result and any error are both visible.
  assert.match(SEARCH_PAGE, /initialQuery=\{initialQuery\}/);
  assert.doesNotMatch(SEARCH_PAGE, /\.catch\(\(\) => \{\}\)/);
  assert.match(SEARCH_BOX, /initialQuery/);
  assert.match(SEARCH_BOX, /void doSearch\(trimmed\)/);
});

test("the global search box reports a failed search", () => {
  assert.match(SEARCH_BOX, /setError\(err instanceof Error \? err\.message : String\(err\)\)/);
  assert.match(SEARCH_BOX, /\{error && <div className="cl-error">\{error\}<\/div>\}/);
});

// ---------------------------------------------------------------------------
// Case workspace: scoped to its case, nothing invented
// ---------------------------------------------------------------------------

const WORKSPACE = read("../src/pages/CaseWorkspace.tsx");

test("case workspace scopes every section to its own case", () => {
  assert.match(WORKSPACE, /caseDashboard\(caseId\)/);
  assert.match(WORKSPACE, /relationshipNetwork\(\{ caseId, limit: 40, minEvidence: 1 \}\)/);
  assert.match(WORKSPACE, /evidenceDocuments\(\{ caseId, limit: 6 \}\)/);
  // It used to fetch the global 575-node graph and ignore the case entirely.
  assert.doesNotMatch(WORKSPACE, /masterGraph\(\)/);
});

test("case workspace invents no confidence, strength, id or timestamp", () => {
  assert.doesNotMatch(WORKSPACE, NO_FABRICATED_ID);
  assert.doesNotMatch(WORKSPACE, /"E-042"/);
  assert.doesNotMatch(WORKSPACE, /"doc-001"/);
  assert.doesNotMatch(WORKSPACE, /confidence: e\.confidence \|\| 0\.75/);
  assert.doesNotMatch(WORKSPACE, /confidence_label: "Medium" as const/);
  assert.doesNotMatch(WORKSPACE, /evidence_strength: "MODERATE" as const/);
  // Confidence, label and strength all come from the edge the server derived.
  assert.match(WORKSPACE, /confidence: e\.confidence,/);
  assert.match(WORKSPACE, /evidence_strength: e\.strength/);
});

test("case workspace header shows stored values, not literals", () => {
  assert.doesNotMatch(WORKSPACE, /caseId \|\| "CR-1024"/);
  assert.doesNotMatch(WORKSPACE, /lastActivity="12 min ago"/);
  assert.doesNotMatch(WORKSPACE, /status="Active"/);
  assert.match(WORKSPACE, /caseId=\{header\.case_number\}/);
  assert.match(WORKSPACE, /status=\{header\.status\}/);
});

test("case workspace reports a failed load instead of empty sections", () => {
  assert.doesNotMatch(WORKSPACE, /console\.error\(err\)/);
  assert.match(WORKSPACE, /setError\(err\.message\)/);
  assert.match(WORKSPACE, /Could not load this case/);
});

test("case workspace opens the real document behind a card", () => {
  assert.match(WORKSPACE, /setEvidenceDocId\(doc\.id\)/);
  assert.match(WORKSPACE, /data=\{evidenceDocId \? \{ id: evidenceDocId \} : null\}/);
});

// ---------------------------------------------------------------------------
// Activity feed: provenance ticks must be computed, not decorative
// ---------------------------------------------------------------------------

const ACTIVITY = read("../src/components/investigator/InvestigatorActivity.tsx");

test("the activity feed renders the backend's computed provenance checks", () => {
  // It used to print two literal ticks next to every finding, whatever that
  // finding actually cited.
  assert.doesNotMatch(
    ACTIVITY,
    /\{activity\.provenance\} ✓ Evidence verified ✓ Source traceable/,
    "provenance ticks must not be literals",
  );
  assert.match(ACTIVITY, /provenanceChecks/);
  assert.match(ACTIVITY, /provenanceChecks\.evidence_verified \? "✓" : "✗"/);
  assert.match(ACTIVITY, /provenanceChecks\.source_traceable \? "✓" : "✗"/);
});

test("the activity feed explains a failed check instead of only ticking", () => {
  assert.match(ACTIVITY, /provenanceChecks\.detail/);
  assert.match(ACTIVITY, /verified" : "unverified"/);
});

// ---------------------------------------------------------------------------
// Relationship panel: derived ticks, not printed ones
// ---------------------------------------------------------------------------

const REL_PANEL = read("../src/components/investigator/RelationshipPanel.tsx");

test("the relationship panel derives its checks from the records", () => {
  assert.doesNotMatch(REL_PANEL, /<div>Temporal consistency: ✓<\/div>/);
  assert.doesNotMatch(REL_PANEL, /<div>Provenance: ✓<\/div>/);
  assert.match(REL_PANEL, /timestampsRecorded/);
  assert.match(REL_PANEL, /provenanceRefs/);
  // A placeholder string must not count as a recorded time.
  assert.match(REL_PANEL, /value\.toLowerCase\(\) !== NO_TIMESTAMP/);
});

test("the relationship panel explains why a check failed", () => {
  assert.match(REL_PANEL, /temporalDetail/);
  assert.match(REL_PANEL, /provenanceDetail/);
  assert.match(REL_PANEL, /No supporting record carries a recorded time/);
  assert.match(REL_PANEL, /No provenance entry names a source document/);
});

// ---------------------------------------------------------------------------
// Trust badge: a check with no input must not render as a pass
// ---------------------------------------------------------------------------

const BADGE = read("../src/components/investigator/ProvenanceBadge.tsx");
const REL_PANEL2 = read("../src/components/investigator/RelationshipPanel.tsx");
const WORKSPACE2 = read("../src/pages/InvestigatorWorkspace.tsx");
const REL_PAGE2 = read("../src/pages/RelationshipsPage.tsx");

test("the trust badge no longer defaults every check to true", () => {
  assert.doesNotMatch(
    BADGE,
    /evidenceVerified = true, sourceTraceable = true, provenanceAvailable = true, noUnsupported = true/,
    "a trust indicator must never default to trusted",
  );
  // An unassessed check renders as unknown, not as a tick.
  assert.match(BADGE, /ok === undefined/);
  assert.match(BADGE, /\? \{label\}/);
});

test("every trust badge call site passes real derived values", () => {
  assert.doesNotMatch(REL_PANEL2, /<ProvenanceBadge \/>/);
  assert.doesNotMatch(WORKSPACE2, /<ProvenanceBadge \/>/);
  assert.doesNotMatch(REL_PAGE2, /<ProvenanceBadge \/>/);
  assert.match(REL_PANEL2, /provenanceChecksFor\(relationship\)/);
  assert.match(WORKSPACE2, /selectedNodeSummary\.checks/);
  assert.match(REL_PAGE2, /provenanceChecksFor\(selected\)/);
});

test("the selected-node badges are derived, not literal", () => {
  assert.doesNotMatch(WORKSPACE2, /<EvidenceStrength strength="STRONG" count=\{2\} \/>/);
  assert.match(WORKSPACE2, /strength=\{selectedNodeSummary\.strength\}/);
  assert.match(WORKSPACE2, /count=\{selectedNodeSummary\.docCount\}/);
  assert.match(WORKSPACE2, /No relationship records for this entity/);
});

test("the workspace's selected-edge badges and trust panel are derived", () => {
  // The edge sidebar hardcoded FACT / STRONG / count 1 and handed the
  // contradiction alert an empty list, so it could never fire.
  assert.doesNotMatch(WORKSPACE2, /<EvidenceStrength strength="STRONG" count=\{1\} \/>/);
  assert.doesNotMatch(WORKSPACE2, /<ContradictionAlert details=\{\[\]\} \/>/);
  assert.match(WORKSPACE2, /strength=\{selectedEdgeSummary\.strength\}/);
  assert.match(WORKSPACE2, /count=\{selectedEdgeSummary\.docCount\}/);
  assert.match(WORKSPACE2, /selectedEdgeSummary\.contradictions\.length > 0/);
});

test("the workspace trust panel reports on a selection, not on nothing", () => {
  assert.doesNotMatch(WORKSPACE2, /<div>✓ Evidence verified<\/div>/);
  assert.doesNotMatch(WORKSPACE2, /<div>✓ No unsupported claims<\/div>/);
  assert.match(WORKSPACE2, /trustTarget \?/);
  assert.match(WORKSPACE2, /Select a person or a relationship to assess/);
});

test("relationship classification is derived from the evidence, not stamped", async () => {
  // Both pages stamped every relationship `classification: "FACT"` while
  // reading confidence and strength off the same edge.  A weak relationship is
  // not a verified fact, so the value is now computed by a shared helper.
  assert.doesNotMatch(WORKSPACE, /classification: "FACT" as const,/);
  assert.doesNotMatch(RELATIONSHIPS, /classification: "FACT" as const,/);
  assert.match(WORKSPACE, /classifyRelationship\(\{/);
  assert.match(RELATIONSHIPS, /classifyRelationship\(\{/);

  // Exercise the real helper, compiled from source, not a restatement of it.
  const { execFileSync } = await import("node:child_process");
  const { pathToFileURL } = await import("node:url");
  const { mkdtempSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = mkdtempSync(join(tmpdir(), "cl-class-"));
  const out = join(dir, "classification.mjs");
  execFileSync(
    join(process.cwd(), "node_modules", ".bin", "esbuild"),
    ["src/lib/classification.ts", "--format=esm", `--outfile=${out}`, "--log-level=error"],
  );
  const { classifyRelationship } = await import(pathToFileURL(out).href);

  assert.equal(classifyRelationship({ strength: "STRONG", confidence: 0.96, supportingCount: 12 }), "FACT");
  assert.equal(classifyRelationship({ strength: "WEAK", confidence: 0.95, supportingCount: 1 }), "INFERENCE");
  assert.equal(classifyRelationship({ strength: "WEAK", confidence: 0.3, supportingCount: 1 }), "HYPOTHESIS");
  assert.equal(classifyRelationship({ strength: "INSUFFICIENT", confidence: 0, supportingCount: 0 }), "UNKNOWN");
  // Strength alone never promotes a low-confidence edge to a fact.
  assert.equal(classifyRelationship({ strength: "STRONG", confidence: 0.4, supportingCount: 3 }), "INFERENCE");
  // Confidence alone never promotes an unstrengthed edge to a fact either.
  assert.equal(classifyRelationship({ confidence: 0.5, supportingCount: 1 }), "HYPOTHESIS");
});
