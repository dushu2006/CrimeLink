/**
 * Regression tests for the PERSON → PERSON relationship graph on /investigate.
 *
 * The console must present *people* as the primary relationship graph, keep
 * supporting entities behind the edge, and keep the deeper entity graph on its
 * own tab.  These tests read the shipped sources the same way the existing
 * master-case-network suite does, so a regression in the wiring fails here
 * rather than in front of an investigator.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const CLIENT = read("../src/api/client.ts");
const HOOK = read("../src/lib/useGraphCanvas.ts");

const HOST = read("../src/components/investigator/MasterCaseNetwork.tsx");
const GRAPH = read("../src/components/investigator/PersonRelationshipNetwork.tsx");

// ---------------------------------------------------------------------------
// The client talks to the person-to-person endpoints
// ---------------------------------------------------------------------------

test("client exposes the person-to-person relationship endpoints", () => {
  assert.match(CLIENT, /\/graph\/master\/relationships/);
  assert.match(CLIENT, /\/graph\/master\/relationship-evidence/);
  assert.match(CLIENT, /\/graph\/cases\/\$\{encodeURIComponent\(query\.caseId\)\}\/relationships/);
  assert.match(CLIENT, /node_types: string\[\]/);
});

// ---------------------------------------------------------------------------
// The host panel defaults to PEOPLE and each tab renders different data
// ---------------------------------------------------------------------------

test("MASTER CASE NETWORK defaults to the PEOPLE NETWORK tab", () => {
  assert.match(
    HOST,
    /useState<NetworkLevel>\("people"\)/,
    "the default investigator view must be the person-to-person graph",
  );
  assert.match(HOST, /type NetworkLevel = "people" \| "case" \| "entity"/);
  assert.match(HOST, /PEOPLE NETWORK/);
  assert.match(HOST, /CASE NETWORK/);
  assert.match(HOST, /ENTITY NETWORK/);
  assert.match(
    HOST,
    /import PersonRelationshipNetwork from "\.\/PersonRelationshipNetwork"/,
  );
  assert.match(HOST, /<PersonRelationshipNetwork/);
});

test("each tab renders a different graph, not the same graph re-titled", () => {
  // PEOPLE: the person graph owns the canvas, and the shared canvas used by the
  // CASE and ENTITY tabs is unmounted (not merely hidden) while it is active.
  assert.match(HOST, /useGraphCanvas\(\{[\s\S]*?enabled: level !== "people"/);
  assert.match(HOOK, /if \(!el \|\| !enabled\) \{[\s\S]*?cyRef\.current\?\.destroy\(\)/);
  assert.match(HOST, /\{level === "people" && \(/);
  assert.match(HOST, /\{level !== "people" && \(/);

  // CASE and ENTITY keep their own fetchers.
  assert.match(HOST, /masterCaseNetwork\(\)/);
  assert.match(HOST, /masterGraph\(\{/);
});

test("the legend states how many stars are actually on screen", () => {
  assert.match(HOST, /entityCriminalCount/);
  assert.match(HOST, /Confirmed Criminal ONLY \(Star\)/);
});

// ---------------------------------------------------------------------------
// The person graph itself: people in, evidence behind the edge
// ---------------------------------------------------------------------------

test("the person graph fetches relationships, never the whole entity graph", () => {
  assert.match(GRAPH, /relationshipNetwork\(\{/);
  assert.match(GRAPH, /relationshipEvidence\(/);
  assert.doesNotMatch(
    GRAPH,
    /masterGraph\(/,
    "the person-to-person view must not load the 575-node entity graph",
  );
});

test("only PERSON nodes are pushed onto the person canvas", () => {
  const nodeLoop = GRAPH.match(/for \(const node of data\.nodes\) \{[\s\S]*?\n    \}/);
  assert.ok(nodeLoop, "node build loop must exist");
  assert.match(nodeLoop[0], /id: node\.provenance_key/);
  assert.match(nodeLoop[0], /is_criminal: Boolean\(node\.is_criminal\)/);
  // No entity-typed nodes, no case nodes, no document nodes.
  assert.doesNotMatch(GRAPH, /showCaseNodes/);
  assert.doesNotMatch(GRAPH, /label: String\(node\.label\)\.toUpperCase\(\)/);
});

test("the star is driven only by the authoritative criminal flag", () => {
  // The star is part of the label, gated only on the authoritative flag (and on
  // the zoom level, which hides *all* labels — never the star alone).
  assert.match(GRAPH, /ele\.data\("is_criminal"\) \? `★\\n\$\{name\}` : name/);
  assert.match(GRAPH, /"background-color": \(ele: any\) =>\s*\n?\s*ele\.data\("is_criminal"\) \? CRIMINAL_FILL : PERSON_FILL/);
  assert.match(GRAPH, /CRIMINAL_BORDER = "#F59E0B"/);
  // Person nodes stay circles so the star can't be confused with a shape change.
  assert.match(GRAPH, /shape: "ellipse"/);
  assert.match(GRAPH, /criminal_status/);
});

test("edges are labelled with the relationship, not with an identifier", () => {
  assert.match(GRAPH, /label: `\$\{edge\.label\}\$\{edge\.evidence_count > 1 \? ` · \$\{edge\.evidence_count\}` : ""\}`/);
  assert.doesNotMatch(GRAPH, /label: edge\.rel_type/);
});

test("selecting an edge reveals the aggregated supporting evidence", () => {
  assert.match(GRAPH, /SUPPORTING EVIDENCE/);
  assert.match(GRAPH, /aggregated behind this single relationship/);
  assert.match(GRAPH, /EvidencePointerLink/);
  assert.match(GRAPH, /SUPPORTING_KIND_LABEL/);
  assert.match(GRAPH, /setEdgeEvidence\(detail\.supporting_items\)/);
});

test("empty state is honest and offers the entity drill-down", () => {
  assert.match(
    GRAPH,
    /No verified person-to-person relationships found in the active dataset\./,
  );
  assert.match(GRAPH, /View supporting entities/);
  assert.match(GRAPH, /onOpenEntityNetwork/);
});

test("progressive disclosure controls exist and are bounded", () => {
  assert.match(GRAPH, /maxRelationships/);
  assert.match(GRAPH, /minEvidence/);
  assert.match(GRAPH, /relationships_total/);
  assert.match(GRAPH, /Top 25/);
});
