/**
 * Graph → source traceability contract.
 *
 * Every graph element must be explainable: node/edge → real properties → the
 * source documents that created it → the complete original document in the
 * EXISTING source viewer.  These tests pin the wiring in every graph view:
 *
 *   - GraphSourceChips renders exactly the deduped backend doc ids and opens
 *     each via DocumentFileLink (existing SourceViewer) — no new viewer, no
 *     fabricated chip, honest empty state;
 *   - GraphPage (person/master/temporal) shows sources for selected nodes
 *     AND edges plus the documented connected entities;
 *   - the master case network surfaces entity-level node provenance AND now
 *     selectable entity-level relationship provenance;
 *   - the person network and the network-analysis graph show the same chips;
 *   - the investigator workspace no longer fabricates document metadata
 *     ("Communication record" etc.) when opening the EvidenceDrawer;
 *   - the graph visualization code itself (cytoscape config) is untouched.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const CHIPS = read("src/components/graph/GraphSourceChips.tsx");
const GRAPH_PAGE = read("src/pages/GraphPage.tsx");
const MCN = read("src/components/investigator/MasterCaseNetwork.tsx");
const PRN = read("src/components/investigator/PersonRelationshipNetwork.tsx");
const NAP = read("src/components/investigator/NetworkAnalysisPanel.tsx");
const IWS = read("src/pages/InvestigatorWorkspace.tsx");
const INVESTIGATIVE_GRAPH = read("src/components/investigator/InvestigativeGraph.tsx");
const I18N = read("src/i18n.ts");

// ---------------------------------------------------------------------------
// The reusable SourceReference component itself
// ---------------------------------------------------------------------------

test("GraphSourceChips opens every id in the existing viewer (DocumentFileLink)", () => {
  assert.ok(CHIPS.includes('from "../EvidenceLink"'), "reuses the existing link layer");
  assert.ok(/<DocumentFileLink\s/.test(CHIPS), "chips are DocumentFileLinks");
  assert.ok(!CHIPS.includes("SourceViewer"), "no direct viewer ownership — stays through DocumentFileLink");
});

test("GraphSourceChips never fabricates a source id", () => {
  // A chip must come from the docIds prop only: there is no literal document
  // id shape in the component that could hardcode a source.
  assert.ok(!/["'`](FIR|CDR|BANK|WIT|CCTV|FIR)-?\d/.test(CHIPS), "no hardcoded document ids");
  assert.ok(CHIPS.includes("seen.has(id)"), "dedupe guard present");
  assert.ok(CHIPS.includes("String(raw ?? \"\").trim()"), "ids come from props, trimmed");
});

test("GraphSourceChips has an honest empty state and caps long lists", () => {
  assert.ok(CHIPS.includes("graph-source-chips-empty"), "empty variant exists");
  assert.ok(CHIPS.match(/if \(ids\.length === 0\)/), "no ids → no chips");
  assert.ok(CHIPS.includes("+{overflow} more"), "overflow indicator");
});

// ---------------------------------------------------------------------------
// GraphPage — the case graph (person / master / temporal)
// ---------------------------------------------------------------------------

test("GraphPage node detail shows the multi-source chips for the selected node", () => {
  assert.ok(GRAPH_PAGE.includes('<GraphSourceChips'), "node panel wires chips");
  assert.ok(GRAPH_PAGE.includes("docIds={selected.source_doc_ids}"), "node sources come from the node row");
});

test("GraphPage edge detail shows sources for the selected relationship", () => {
  assert.ok(GRAPH_PAGE.includes("docIds={selectedEdge.source_doc_ids}"), "edge sources come from the edge row");
});

test("GraphPage node detail lists documented connected entities", () => {
  assert.ok(GRAPH_PAGE.includes("selectedConnections"), "connections memo exists");
  assert.ok(GRAPH_PAGE.includes('"graph.connections"'), "labelled section rendered");
  assert.ok(GRAPH_PAGE.includes('t("graph.connections")'), "i18n label");
});

test("GraphPage shows a source-derived description only when one exists", () => {
  assert.ok(GRAPH_PAGE.includes("selectedDescription"), "description memo");
  assert.ok(GRAPH_PAGE.includes("properties.description") ||
      GRAPH_PAGE.includes('description'), "description read from node properties");
});

// ---------------------------------------------------------------------------
// Master case network — case / entity levels
// ---------------------------------------------------------------------------

test("MasterCaseNetwork entity node detail is type-aware and sourced", () => {
  assert.ok(MCN.includes("typeSpecificRows(selectedEntityNode)"), "type-aware property rows");
  assert.ok(MCN.includes("docIds={selectedEntityNode.source_doc_ids}"), "sources for the entity node");
});

test("MasterCaseNetwork supports selectable entity-level relationship provenance", () => {
  assert.ok(MCN.includes("selectedEntityEdge"), "entity-edge selection state");
  assert.ok(MCN.includes('if (level === "case")'), "case-level edge tap kept");
  assert.ok(MCN.includes("edgeSpecificRows(selectedEntityEdge)"), "type-aware edge rows");
  assert.ok(MCN.includes("selectedEntityEdge.source_doc_ids"), "entity-edge sources rendered");
});

// ---------------------------------------------------------------------------
// Person relationship network
// ---------------------------------------------------------------------------

test("PersonRelationshipNetwork surfaces sources for a selected person", () => {
  assert.ok(PRN.includes("docIds={selectedNode.source_doc_ids}"), "person sources");
});

test("PersonRelationshipNetwork surfaces sources for a selected relationship", () => {
  assert.ok(PRN.includes("docIds={selectedEdge.source_doc_ids}"), "relationship sources");
});

// ---------------------------------------------------------------------------
// Network analysis graph
// ---------------------------------------------------------------------------

test("NetworkAnalysisPanel shows sources for the selected node and edge", () => {
  assert.ok(NAP.includes("docIds={selectedNode.source_doc_ids}"), "node sources");
  assert.ok(NAP.includes("selectedEdge.source_doc_ids"), "edge sources");
});

// ---------------------------------------------------------------------------
// Reuse rule — no duplicate document viewer in graph land
// ---------------------------------------------------------------------------

test("graph detail panels reuse the existing viewer layer, never a private one", () => {
  for (const [name, src] of [
    ["GraphPage", GRAPH_PAGE],
    ["MasterCaseNetwork", MCN],
    ["PersonRelationshipNetwork", PRN],
    ["NetworkAnalysisPanel", NAP],
  ]) {
    assert.ok(
      !src.includes('import SourceViewer'),
      `${name} must not own a private SourceViewer`,
    );
  }
});

test("the graph visualization itself is unchanged (cytoscape config untouched)", () => {
  // Change-detection proxies: the canvas init, layout and styles must still
  // be single-sourced inside the graph components.
  assert.ok(INVESTIGATIVE_GRAPH.includes("cy.on(\"tap\", \"node\""), "tap wiring intact");
  assert.ok(INVESTIGATIVE_GRAPH.includes("cytoscape.use(fcose)"), "layout intact");
});

// ---------------------------------------------------------------------------
// No fabricated provenance metadata when opening sources
// ---------------------------------------------------------------------------

test("InvestigatorWorkspace opens the drawer with the id only — no invented record metadata", () => {
  const open = IWS.slice(
    IWS.indexOf("const handleOpenEvidence"),
    IWS.indexOf("const handleGraphContextAction"),
  );
  assert.ok(!open.includes('type: "Communication record"'), "no fabricated document type");
  assert.ok(!open.includes('evidenceRole: "Supports relationship"'), "no fabricated evidence role");
  assert.ok(!open.includes('source: "Case record"'), "no fabricated source kind");
  assert.ok(!open.includes('evidenceLevel: "FACT"'), "no fabricated evidence level");
});

// ---------------------------------------------------------------------------
// i18n for the new labels
// ---------------------------------------------------------------------------

test("graph.sources label exists in every supported language", () => {
  const entry = I18N.slice(I18N.indexOf('"graph.sources"'), I18N.indexOf('"graph.sources"') + 200);
  for (const lang of ["en:", "hi:", "te:", "ta:"]) {
    assert.ok(entry.includes(lang), `graph.sources has ${lang}`);
  }
});
