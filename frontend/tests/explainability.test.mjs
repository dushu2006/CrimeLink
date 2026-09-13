/**
 * Targeted tests for the final explainability step: why was this surfaced?
 *
 * Runs against the REAL `src/lib/investigator.ts` with Node's type stripping,
 * pinning the honesty rules the new surfaces rely on:
 *
 *   * the analytical basis renders only the metrics the backend computed;
 *   * the interpretation boundary shows only the caveats that apply;
 *   * relationship strength and evidence confidence stay two dimensions;
 *   * evidence strength is never phrased as a criminal probability.
 *
 *   npm test
 */

import assert from "node:assert/strict";
import { test } from "node:test";

const LIB_URL = new URL("../src/lib/investigator.ts", import.meta.url).href;
const LIB = await import(LIB_URL);

const betweennessOnly = {
  betweenness_centrality: 0.47,
  explanations: { betweenness_centrality: "Betweenness measures bridge position, not guilt." },
};

test("analytical basis renders only metrics that were actually computed", () => {
  const lines = LIB.analyticalBasisLines(betweennessOnly);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].metric, "Betweenness centrality");
  assert.equal(lines[0].value, "0.47");
  assert.match(lines[0].why, /bridge position/i);
});

test("analytical basis never invents a metric the backend did not send", () => {
  const lines = LIB.analyticalBasisLines({ degree_centrality: null, pagerank: null });
  assert.deepEqual(lines, []);
});

test("an empty or missing basis renders no metric rows", () => {
  assert.deepEqual(LIB.analyticalBasisLines(null), []);
  assert.deepEqual(LIB.analyticalBasisLines(undefined), []);
});

test("cross-case and bridging show only when present", () => {
  const lines = LIB.analyticalBasisLines({
    cross_case_count: 4,
    bridge_info: { bridge_count: 3, bridges_to: [1, 2, 3] },
  });
  const metrics = lines.map((line) => line.metric);
  assert.ok(metrics.includes("Cases connected"));
  assert.ok(!metrics.includes("Betweenness centrality"));
});

test("the interpretation boundary only shows the caveats that apply", () => {
  const bridge = LIB.interpretationBoundaries({
    kind: "NETWORK_BRIDGE",
    analytical_basis: { betweenness_centrality: 0.7 },
  });
  assert.deepEqual(bridge, ["Network metrics indicate structural importance, not criminality."]);

  const colocation = LIB.interpretationBoundaries({
    kind: "COLOCATION",
    analytical_basis: null,
  });
  assert.deepEqual(colocation, ["Co-location does not establish association."]);

  const noBasis = LIB.interpretationBoundaries({ kind: "ER_SIGNAL", analytical_basis: null });
  assert.deepEqual(noBasis, []);
});

test("relationship strength and evidence confidence are separate sentences", () => {
  const strength = LIB.relationshipStrengthSentence("WEAK");
  const confidence = LIB.evidenceConfidenceSentence("INSUFFICIENT");
  assert.match(strength, /Observed relationship/i);
  assert.match(confidence, /Evidence confidence/i);
  assert.notEqual(strength, confidence);
});

test("relationship why falls back honestly when the backend sent none", () => {
  const withWhy = LIB.relationshipWhy({ why: "Surfaced because X", kind: "direct" });
  assert.equal(withWhy, "Surfaced because X");
  const withoutWhy = LIB.relationshipWhy({ why: null, kind: "temporal" });
  assert.match(withoutWhy, /temporal/);
});

test("evidence strength wording never leaks a criminal probability", () => {
  for (const strength of ["STRONG", "MODERATE", "WEAK", "INSUFFICIENT"]) {
    const sentence = LIB.strengthSentence(strength);
    assert.ok(!/%/.test(sentence), `${strength} leaked a percentage: ${sentence}`);
    assert.ok(!/criminal|guilt|probability/i.test(sentence), `${strength} leaked guilt language`);
  }
});

test("analytical basis values are structural, never criminal", () => {
  const lines = LIB.analyticalBasisLines({
    pagerank: 0.082,
    betweenness_centrality: 0.47,
    weighted_degree: 18,
  });
  const text = lines.map((line) => `${line.metric} ${line.value} ${line.why}`).join(" ");
  assert.ok(!/criminal|guilt/i.test(text));
});
