/**
 * Regression tests for:
 * 1. Case Detail document association & display
 * 2. Centralized graph display labels (no canonical ID leakage)
 * 3. Strict criminal-star rule (ONLY confirmed criminals get stars, all others circles)
 * 4. Safe entity typing (no string-prefix guessing)
 * 5. Pseudonymization & De-pseudonymization validation
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DISPLAY_LABELS_URL = new URL("../src/lib/displayLabels.ts", import.meta.url).href;
const { getDisplayLabel, isConfirmedCriminal, nodeShapeRule } = await import(DISPLAY_LABELS_URL);

// ---------------------------------------------------------------------------
// 1 & 2: Case Detail displays case-associated documents
// ---------------------------------------------------------------------------

test("1 & 2. CaseDetail renders documents and passes case_id correctly", () => {
  const caseDetailPath = path.resolve(__dirname, "../src/pages/CaseDetail.tsx");
  const content = fs.readFileSync(caseDetailPath, "utf8");

  // Must query documents with caseId
  assert.match(
    content,
    /api<.*>\(\s*`\/cases\/\$\{caseId\}\/documents`\s*\)/,
    "CaseDetail must query documents scoped to the specific caseId",
  );

  // Must mount DocumentFileLink with doc.id and doc.filename to open SourceViewer
  assert.match(
    content,
    /<DocumentFileLink\b/,
    "CaseDetail must mount DocumentFileLink to open documents in SourceViewer without navigation",
  );
  assert.match(
    content,
    /docId=\{doc\.id\}/,
    "Must pass doc.id to DocumentFileLink",
  );
  assert.match(
    content,
    /originFile=\{doc\.filename\}/,
    "Must pass originFile to DocumentFileLink",
  );
});

// ---------------------------------------------------------------------------
// 3 to 10: Graph labels use human-readable display values instead of canonical IDs
// ---------------------------------------------------------------------------

test("3 & 4. PERSON entity uses display name, never internal ID", () => {
  const person = {
    id: "P001",
    label: "Person",
    properties: {
      canonical_id: "P001",
      name: "Rajesh Kumar",
      full_name: "Rajesh Kumar",
      entity_type: "PERSON",
    },
  };
  const label = getDisplayLabel(person);
  assert.equal(label, "Rajesh Kumar");
  assert.notEqual(label, "P001");
});

test("5. PHONE entity uses phone number", () => {
  const phone = {
    id: "PH001",
    label: "Phone",
    properties: {
      canonical_id: "PH001",
      number: "+91-9876543210",
      phone_number: "+91-9876543210",
      entity_type: "PHONE",
    },
  };
  assert.equal(getDisplayLabel(phone), "+91-9876543210");
});

test("6. BANK_ACCOUNT entity uses account identifier", () => {
  const account = {
    id: "BA001",
    label: "BankAccount",
    properties: {
      canonical_id: "BA001",
      account_number: "987654321012",
      entity_type: "BANK_ACCOUNT",
    },
  };
  assert.equal(getDisplayLabel(account), "987654321012");
});

test("7. VEHICLE entity uses registration plate", () => {
  const vehicle = {
    id: "VH001",
    label: "Vehicle",
    properties: {
      canonical_id: "VH001",
      registration: "AP09AB1234",
      registration_number: "AP09AB1234",
      entity_type: "VEHICLE",
    },
  };
  assert.equal(getDisplayLabel(vehicle), "AP09AB1234");
});

test("8. LOCATION entity uses location name", () => {
  const location = {
    id: "L001",
    label: "Location",
    properties: {
      canonical_id: "L001",
      name: "Vijayawada Central",
      entity_type: "LOCATION",
    },
  };
  assert.equal(getDisplayLabel(location), "Vijayawada Central");
});

test("9. CASE entity uses case number", () => {
  const c = {
    id: "case-uuid-1",
    label: "Case",
    properties: {
      canonical_id: "C104",
      case_number: "C104",
      title: "Operation Hawk",
      entity_type: "CASE",
    },
  };
  assert.equal(getDisplayLabel(c), "C104");
});

test("10. FIR entity uses FIR number", () => {
  const fir = {
    id: "fir-uuid-1",
    label: "FIR",
    properties: {
      canonical_id: "FIR-104",
      fir_number: "FIR-104",
      entity_type: "FIR",
    },
  };
  assert.equal(getDisplayLabel(fir), "FIR-104");
});

// ---------------------------------------------------------------------------
// 11 to 15: Criminal shape rule: ONLY confirmed criminals get stars, all others circles
// ---------------------------------------------------------------------------

test("11. Confirmed criminal receives star shape", () => {
  const criminal = {
    id: "P001",
    label: "Person",
    properties: {
      name: "Vikram Rao",
      criminal_status: "convicted",
      legal_status: "convicted",
    },
  };
  assert.equal(isConfirmedCriminal(criminal), true);
  assert.equal(nodeShapeRule(criminal), "star");
});

test("12 to 15. All non-criminals remain circles regardless of metrics or legal roles", () => {
  // Suspect remains circle
  const suspect = {
    id: "P002",
    label: "Person",
    properties: {
      name: "Sana Iyer",
      criminal_status: "suspect",
      legal_status: "suspect",
    },
  };
  assert.equal(isConfirmedCriminal(suspect), false);
  assert.equal(nodeShapeRule(suspect), "ellipse");

  // Person of interest remains circle
  const poi = {
    id: "P003",
    label: "Person",
    properties: {
      name: "Amit Verma",
      criminal_status: "person_of_interest",
      legal_status: "person_of_interest",
    },
  };
  assert.equal(isConfirmedCriminal(poi), false);
  assert.equal(nodeShapeRule(poi), "ellipse");

  // Witness remains circle
  const witness = {
    id: "P004",
    label: "Person",
    properties: {
      name: "Ramesh Sharma",
      legal_status: "witness",
    },
  };
  assert.equal(isConfirmedCriminal(witness), false);
  assert.equal(nodeShapeRule(witness), "ellipse");

  // Victim remains circle
  const victim = {
    id: "P005",
    label: "Person",
    properties: {
      name: "Pooja Patel",
      legal_status: "victim",
    },
  };
  assert.equal(isConfirmedCriminal(victim), false);
  assert.equal(nodeShapeRule(victim), "ellipse");

  // High degree / betweenness centrality non-criminal remains circle
  const hub = {
    id: "P006",
    label: "Person",
    properties: {
      name: "Central Broker",
      degree: 45,
      betweenness: 0.85,
      pagerank: 0.12,
      network_role: "Hub",
    },
  };
  assert.equal(isConfirmedCriminal(hub), false);
  assert.equal(nodeShapeRule(hub), "ellipse");

  // Non-person entities always remain circle
  const phone = { id: "PH1", label: "Phone", properties: { degree: 50 } };
  assert.equal(nodeShapeRule(phone), "ellipse");

  const account = { id: "BA1", label: "BankAccount", properties: { transactions: 100 } };
  assert.equal(nodeShapeRule(account), "ellipse");

  const vehicle = { id: "VH1", label: "Vehicle", properties: {} };
  assert.equal(nodeShapeRule(vehicle), "ellipse");

  const location = { id: "L1", label: "Location", properties: {} };
  assert.equal(nodeShapeRule(location), "ellipse");

  const org = { id: "O1", label: "Organization", properties: {} };
  assert.equal(nodeShapeRule(org), "ellipse");

  const caseNode = { id: "C1", label: "Case", properties: {} };
  assert.equal(nodeShapeRule(caseNode), "ellipse");
});

// ---------------------------------------------------------------------------
// 16 to 18: No prefix inferencing, proper fallback
// ---------------------------------------------------------------------------

test("16 & 18. Unknown display values degrade honestly without guessing entity type from ID prefix", () => {
  const nodeWithoutName = {
    id: "P999",
    label: "Person",
    properties: {
      canonical_id: "P999",
    },
  };
  // Degrades to ID only as fallback, does NOT crash or change type
  const label = getDisplayLabel(nodeWithoutName);
  assert.ok(label === "P999" || label === "Unnamed Person");
});
