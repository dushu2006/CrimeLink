/**
 * Confirmed-criminal stars, evidence-grounded provenance, and live refresh.
 *
 * The reported defect was a legend promising "Confirmed Criminal (★ + amber
 * ring)" while the graph showed zero stars and the counter read
 * "0 confirmed criminals".  The rule these tests enforce is narrow and
 * deliberate:
 *
 *   ★ comes from the dataset's authoritative `criminal_status` and from
 *     nothing else — not degree, not centrality, not having a phone or a
 *     transaction — and the counter is computed from the same rows the graph
 *     draws, so the two can never disagree.
 *
 * Also covered:
 *   - the People Network renders PERSON nodes only (no phone/account/vehicle/
 *     location/organisation/event node ever reaches that canvas);
 *   - provenance ticks are server-computed, never defaulted to true;
 *   - the live-refresh hook is bounded and reports failures.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const GRAPH = read("../src/components/investigator/PersonRelationshipNetwork.tsx");
const HOST = read("../src/components/investigator/MasterCaseNetwork.tsx");
const LABELS = await import(new URL("../src/lib/displayLabels.ts", import.meta.url).href);
const LIVE = await import(new URL("../src/lib/useLiveRefresh.ts", import.meta.url).href);

// ---------------------------------------------------------------------------
// 1. The star is derived from criminal_status alone
// ---------------------------------------------------------------------------

const CRIMINAL = { label: "PERSON", name: "Priya Kumar", criminal_status: "CONFIRMED" };
const WITNESS = { label: "PERSON", name: "Sachin Bose", criminal_status: null, role: "WITNESS" };
const SUSPECT = { label: "PERSON", name: "Vikram Rao", criminal_status: null, role: "SUSPECT" };
const UNKNOWN = { label: "PERSON", name: "Unknown Person" };

test("a confirmed criminal earns the star", () => {
  assert.equal(LABELS.isConfirmedCriminal(CRIMINAL), true);
});

test("a non-criminal role never earns the star, however senior it looks", () => {
  for (const person of [WITNESS, SUSPECT, UNKNOWN]) {
    assert.equal(
      LABELS.isConfirmedCriminal(person),
      false,
      `${person.name} (${person.role ?? "no role"}) must not be starred`,
    );
  }
});

test("missing or unknown criminal status is never treated as confirmed", () => {
  for (const status of [null, undefined, "", "  ", "unknown", "none", "null", "UNKNOWN", "NONE"]) {
    assert.equal(
      LABELS.isConfirmedCriminal({ label: "PERSON", name: "X", criminal_status: status }),
      false,
      `criminal_status=${JSON.stringify(status)} must not be confirmed`,
    );
  }
});

test("roles that are never a criminal status are rejected even when present", () => {
  for (const role of ["SUSPECT", "WITNESS", "VICTIM", "ASSOCIATE", "INFORMANT", "PERSON_OF_INTEREST"]) {
    assert.equal(
      LABELS.isConfirmedCriminal({ label: "PERSON", name: "X", criminal_status: role }),
      false,
      `criminal_status=${role} must not be treated as confirmed`,
    );
  }
});

test("network position never earns the star", () => {
  // A maximally connected, central person with no criminal_status stays a circle.
  const hub = {
    label: "PERSON",
    name: "Hub",
    criminal_status: null,
    connections: 999,
    relationship_count: 999,
    degree: 999,
    centrality: 1,
    betweenness: 1,
  };
  assert.equal(LABELS.isConfirmedCriminal(hub), false);
});

test("the criminal visual treatment and API flag use the same source", () => {
  assert.match(
    GRAPH,
    /"background-color": \(ele: any\) =>\s*\n?\s*ele\.data\("is_criminal"\) \? CRIMINAL_FILL : PERSON_FILL/,
  );
  assert.match(GRAPH, /ele\.data\("is_criminal"\) \? CRIMINAL_BORDER : "#BFDBFE"/);
  assert.match(GRAPH, /CRIMINAL_FILL = "#DC2626"/);
  assert.match(GRAPH, /CRIMINAL_BORDER = "#F59E0B"/);
  // The node data is populated from the API row's flag, never computed locally.
  assert.match(GRAPH, /is_criminal: Boolean\(node\.is_criminal\)/);
  assert.match(GRAPH, /criminal_status: node\.criminal_status \?\? null/);
});

// ---------------------------------------------------------------------------
// 2. The counter and the stars come from the same data
// ---------------------------------------------------------------------------

test("the legend count is the API's count, not a locally recomputed one", () => {
  // The backend derives `confirmed_criminals` over the same node set it sends,
  // so reading anything else can drift from the stars actually on screen.
  assert.match(GRAPH, /\$\{counts\.confirmed_criminals\} confirmed criminal/);
  assert.doesNotMatch(
    GRAPH,
    /confirmedCriminals\s*=\s*nodes\.filter/,
    "the count must not be recomputed from a possibly filtered node list",
  );
});

test("the entity-network legend counts the stars it actually renders", () => {
  assert.match(HOST, /entityCriminalCount/);
  assert.match(HOST, /visibleEntityNodes\.filter\(\(n\) => isConfirmedCriminal\(n\)\)\.length/);
  assert.match(HOST, /entityCriminalCount >= 0 \? ` — \$\{entityCriminalCount\} in view`/);
  // ...from the same filtered set the canvas is built from.
  assert.match(HOST, /for \(const node of visibleEntityNodes\) \{[\s\S]*?isConfirmedCriminal\(node\)/);
});

// ---------------------------------------------------------------------------
// 3. People Network is PERSON → PERSON only
// ---------------------------------------------------------------------------

const SUPPORTING = ["PHONE", "BANK_ACCOUNT", "VEHICLE", "LOCATION", "ORGANIZATION", "EVENT", "DOCUMENT"];

test("the People Network canvas receives person nodes and nothing else", () => {
  const nodeLoop = GRAPH.match(/for \(const node of data\.nodes\) \{[\s\S]*?\n    \}/);
  assert.ok(nodeLoop, "the node build loop must exist");
  for (const label of SUPPORTING) {
    assert.doesNotMatch(
      nodeLoop[0],
      new RegExp(label),
      `${label} must never be pushed onto the People Network canvas`,
    );
  }
  // The endpoint the view calls is the person-to-person one.
  assert.match(GRAPH, /relationshipNetwork\(\{/);
  assert.doesNotMatch(GRAPH, /masterGraph\(/, "must not load the entity graph");
});

test("supporting entities appear only as edge evidence, explained in words", () => {
  assert.match(GRAPH, /SUPPORTING_KIND_LABEL/);
  assert.match(GRAPH, /BANK_ACCOUNT: "Bank account"/);
  assert.match(GRAPH, /COMMUNICATION: "Communication record"/);
  assert.match(GRAPH, /TRANSACTION: "Financial transaction"/);
});

// ---------------------------------------------------------------------------
// 4. Provenance is computed, never defaulted
// ---------------------------------------------------------------------------

test("provenance ticks come from the server-computed checks", () => {
  const drawer = read("../src/components/investigator/EvidenceDrawer.tsx");
  assert.match(drawer, /checks: Record<string, ProvenanceCheck>/);
  assert.match(drawer, /ok: boolean \| null/, "a check must be able to be unassessed");
  assert.doesNotMatch(drawer, /ok: true,\s*\n\s*detail: "Source verified"/, "no hardcoded green tick");
  assert.match(drawer, /payload\?\.checks/, "the panel reads the server's checks");
});

test("the backend computes each provenance check from stored data", () => {
  const svc = read("../../backend/app/services/documents.py");
  assert.match(svc, /"source_verified": \{[\s\S]*?document\.source_confidence\.value == "VERIFIED"/);
  assert.match(svc, /"record_available": \{[\s\S]*?bool\(file_row\["available"\]\)/);
  assert.match(svc, /"hash_matches": \{[\s\S]*?file_row\.get\("hash_matches"\)/);
  assert.match(svc, /raw = container\.object_store\.get\(/, "the bytes are actually read");
  assert.doesNotMatch(svc, /"source_verified": \{\s*"ok": True/);
});

// ---------------------------------------------------------------------------
// 5. Live refresh is bounded and reports failures
// ---------------------------------------------------------------------------

test("a refresh failure is reported, never swallowed", async () => {
  const calls = [];
  const hook = LIVE.useLiveRefresh;
  assert.equal(typeof hook, "function");

  // The hook itself is React; assert the contract it is built on instead: the
  // pages must surface `live.error` rather than clearing their data.
  for (const page of [
    "../src/pages/CaseWorkspace.tsx",
    "../src/pages/Cases.tsx",
    "../src/pages/EvidencePage.tsx",
    "../src/pages/TimelinePage.tsx",
    "../src/pages/PeoplePage.tsx",
  ]) {
    const src = read(page);
    assert.match(src, /useLiveRefresh\(/, `${page} must use the live-refresh hook`);
    assert.match(src, /<StaleDataNotice/, `${page} must surface a failed refresh`);
    assert.match(src, /error=\{live\.error\}/, `${page} must pass the refresh error through`);
    calls.push(page);
  }
  assert.equal(calls.length, 5);
});

test("no page converts a failed load into an empty dataset", () => {
  const workspace = read("../src/pages/InvestigatorWorkspace.tsx");
  assert.doesNotMatch(
    workspace,
    /catch \{\s*\n\s*setEnhancedTimelineEvents\(\[\]\)/,
    "a failed timeline request must not read as 'no timeline events'",
  );
  assert.match(workspace, /setTimelineError\(/);
  assert.match(workspace, /The timeline could not be loaded/);
});

test("the live-refresh hook never polls a hidden tab", () => {
  const src = read("../src/lib/useLiveRefresh.ts");
  assert.match(src, /document\.visibilityState === "visible"/);
  assert.match(src, /minIntervalMs/, "refreshes must be throttled");
  assert.match(src, /DATASET_CHANGED_EVENT = "crimelink:dataset-changed"/);
  assert.match(src, /if \(inFlightRef\.current\) return/, "no overlapping refreshes");
});

test("a failed edge-evidence refresh is announced, not silently substituted", () => {
  assert.match(GRAPH, /Could not refresh the supporting records/);
  assert.doesNotMatch(
    GRAPH,
    /\} catch \{\s*\n\s*\/\/ The aggregated edge already carries/,
    "the bare catch that hid the failure must be gone",
  );
});

test("a failed image fetch says so instead of spinning forever", () => {
  const viewer = read("../src/components/SourceViewer.tsx");
  assert.match(viewer, /The image could not be loaded/);
  assert.doesNotMatch(viewer, /fetchBlob\(rawEndpoint\)[\s\S]{0,400}\.catch\(\(\) => undefined\)/);
});

// ---------------------------------------------------------------------------
// 6. The startTime / reportAllChanges report is not CrimeLink's
// ---------------------------------------------------------------------------

test("CrimeLink ships no web-vitals instrumentation of its own", () => {
  const sources = [
    "../src/main.tsx",
    "../src/App.tsx",
    "../index.html",
    "../package.json",
  ];
  for (const rel of sources) {
    const src = read(rel);
    for (const needle of ["reportAllChanges", "web-vitals", "getLCP", "getCLS", "onLCP"]) {
      assert.doesNotMatch(src, new RegExp(needle), `${rel} must not contain ${needle}`);
    }
  }
});
