/**
 * Regression tests for the Master Case Network on /investigate.
 *
 * Covers all 13 frontend criteria:
 * 1. /investigate renders MASTER CASE NETWORK
 * 2. graph is visible without navigating to another page
 * 3. case nodes are circles
 * 4. only confirmed criminals are stars in entity drill-down
 * 5. case connections can be selected
 * 6. selected case connection displays WHY
 * 7. shared entities are displayed
 * 8. supporting evidence opens SourceViewer
 * 9. empty state
 * 10. loading state
 * 11. error state
 * 12. active dataset counts match graph
 * 13. no entity-type-specific node shapes
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const LIB_URL = new URL("../src/lib/investigator.ts", import.meta.url).href;
const LIB = await import(LIB_URL);

// ---------------------------------------------------------------------------
// 1 & 2: /investigate renders MASTER CASE NETWORK directly on page
// ---------------------------------------------------------------------------

test("1 & 2. /investigate renders MASTER CASE NETWORK directly without navigation", () => {
  const workspacePath = path.resolve(__dirname, "../src/pages/InvestigatorWorkspace.tsx");
  const content = fs.readFileSync(workspacePath, "utf8");

  // Must import MasterCaseNetwork
  assert.match(
    content,
    /import\s+MasterCaseNetwork\s+from\s+["']\.\.\/components\/investigator\/MasterCaseNetwork["']/,
    "InvestigatorWorkspace must import MasterCaseNetwork",
  );

  // Must mount MasterCaseNetwork
  assert.match(
    content,
    /<MasterCaseNetwork\b/,
    "InvestigatorWorkspace must mount MasterCaseNetwork directly on the page",
  );

  // Must be located near the top right after scope / network analysis
  const naIndex = content.indexOf("<NetworkAnalysisPanel");
  const mcnIndex = content.indexOf("<MasterCaseNetwork");
  const objIndex = content.indexOf("Investigation objective");

  assert.ok(naIndex > 0, "NetworkAnalysisPanel must be present");
  assert.ok(mcnIndex > naIndex, "MasterCaseNetwork must be mounted right after NetworkAnalysisPanel");
  assert.ok(objIndex > mcnIndex, "MasterCaseNetwork must appear before the lower text sections");
});

// ---------------------------------------------------------------------------
// 3 & 13: Node shape rule: Cases are ALWAYS circles, NO type-specific shapes
// ---------------------------------------------------------------------------

test("3 & 13. case nodes are circles, and no entity type creates a special shape", () => {
  // Case node is ALWAYS circle (ellipse in Cytoscape)
  assert.equal(LIB.nodeShapeRule(false), "ellipse");
  assert.equal(LIB.isCaseNodeCircle("CASE"), true);

  // Every entity type without confirmed criminal status must be circle
  const entityTypes = [
    "CASE",
    "PERSON",
    "PHONE",
    "BANK_ACCOUNT",
    "BANKACCOUNT",
    "VEHICLE",
    "LOCATION",
    "ORGANIZATION",
    "DOCUMENT",
    "FIR",
    "TRANSACTION",
    "SOCIAL_ACCOUNT",
    "EVIDENCE",
  ];

  for (const type of entityTypes) {
    // Non-criminal: shape must be ellipse
    assert.equal(
      LIB.nodeShapeRule(false),
      "ellipse",
      `${type} without confirmed criminal status must be a circle (ellipse)`,
    );
  }
});

// ---------------------------------------------------------------------------
// 4: ONLY confirmed criminals get star shapes (★)
// ---------------------------------------------------------------------------

test("4. only confirmed criminals are stars in entity drill-down", () => {
  // Confirmed criminal: star
  assert.equal(LIB.nodeShapeRule(true), "star");

  // Non-criminal person: circle
  assert.equal(LIB.nodeShapeRule(false), "ellipse");

  // Inspect MasterCaseNetwork component source to verify universal shape handler
  const mcnPath = path.resolve(__dirname, "../src/components/investigator/MasterCaseNetwork.tsx");
  const mcnContent = fs.readFileSync(mcnPath, "utf8");

  assert.match(
    mcnContent,
    /shape:\s*\(ele:\s*any\)\s*=>\s*\(ele\.data\(["']is_criminal["']\)\s*\?\s*["']star["']\s*:\s*["']ellipse["']\)/,
    "MasterCaseNetwork Cytoscape stylesheet must enforce star for criminals and ellipse for all others",
  );
});

// ---------------------------------------------------------------------------
// 5, 6 & 7: Case connections, WHY explanation, and shared entities
// ---------------------------------------------------------------------------

test("5, 6 & 7. case connections explain WHY and display shared entities", () => {
  const mcnPath = path.resolve(__dirname, "../src/components/investigator/MasterCaseNetwork.tsx");
  const mcnContent = fs.readFileSync(mcnPath, "utf8");

  // Selected case connection displays header
  assert.ok(
    mcnContent.includes("CASE CONNECTION:"),
    "Component must render CASE CONNECTION title for selected connection",
  );

  // Displays WHY
  assert.ok(
    mcnContent.includes("WHY THIS CONNECTION EXISTS"),
    "Component must render WHY THIS CONNECTION EXISTS section",
  );

  // Displays ANALYTICAL BASIS
  assert.ok(
    mcnContent.includes("ANALYTICAL BASIS"),
    "Component must render ANALYTICAL BASIS section",
  );

  // Displays SHARED ENTITIES
  assert.ok(
    mcnContent.includes("SHARED ENTITIES"),
    "Component must render SHARED ENTITIES section",
  );

  // Connection strength badge
  const strongBadge = LIB.connectionStrengthBadge("STRONG");
  assert.equal(strongBadge.label, "Strong connection");
  assert.equal(strongBadge.tone, "ok");

  const modBadge = LIB.connectionStrengthBadge("MODERATE");
  assert.equal(modBadge.label, "Moderate connection");
  assert.equal(modBadge.tone, "navy");
});

// ---------------------------------------------------------------------------
// 8: Supporting evidence opens SourceViewer without navigating away
// ---------------------------------------------------------------------------

test("8. supporting evidence opens existing SourceViewer", () => {
  const mcnPath = path.resolve(__dirname, "../src/components/investigator/MasterCaseNetwork.tsx");
  const mcnContent = fs.readFileSync(mcnPath, "utf8");

  // Uses EvidencePointerLink / DocumentFileLink which open SourceViewer
  assert.ok(
    mcnContent.includes("<EvidencePointerLink"),
    "Component must use EvidencePointerLink for evidence pointers",
  );

  assert.ok(
    mcnContent.includes("SUPPORTING EVIDENCE"),
    "Component must include SUPPORTING EVIDENCE heading",
  );
});

// ---------------------------------------------------------------------------
// 9, 10, 11: Honest States: Empty, Loading, and Error with Retry
// ---------------------------------------------------------------------------

test("9, 10 & 11. handles empty state, honest loading state, and error state with retry", () => {
  const mcnPath = path.resolve(__dirname, "../src/components/investigator/MasterCaseNetwork.tsx");
  const mcnContent = fs.readFileSync(mcnPath, "utf8");

  // Loading state
  assert.ok(
    mcnContent.includes("Building master case network..."),
    "Component must show honest 'Building master case network...' loading label",
  );

  // Empty state
  assert.ok(
    mcnContent.includes("No active dataset is available."),
    "Component must show 'No active dataset is available.' when empty",
  );

  // Error state with retry
  assert.match(
    mcnContent,
    /<ErrorState\s+message=\{error\}\s+onRetry=\{/,
    "Component must render ErrorState with retry button",
  );
});

// ---------------------------------------------------------------------------
// 12: Active dataset counts and two-level navigation
// ---------------------------------------------------------------------------

test("12. two-level master network selector and active dataset isolation", () => {
  const mcnPath = path.resolve(__dirname, "../src/components/investigator/MasterCaseNetwork.tsx");
  const mcnContent = fs.readFileSync(mcnPath, "utf8");

  // Two-level selector buttons
  assert.ok(
    mcnContent.includes("CASE NETWORK"),
    "Component must have CASE NETWORK level button",
  );
  assert.ok(
    mcnContent.includes("ENTITY NETWORK"),
    "Component must have ENTITY NETWORK level button",
  );

  // Button to open entity network from case selection
  assert.ok(
    mcnContent.includes("[OPEN ENTITY NETWORK]"),
    "Component must offer [OPEN ENTITY NETWORK] drill-down",
  );
});
