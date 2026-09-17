/**
 * Graph viewport + layout geometry.
 *
 * The ENTITY NETWORK used to be unusable: 500+ nodes laid out with Cytoscape's
 * stock `circle` / `concentric`, a fixed `minZoom: 0.2` that could not reach the
 * whole graph, and a "Fit view" that reset scale but not translation.  These
 * tests pin the geometry that replaces it:
 *
 *   A. a ring's radius grows with the node count (no fixed radius);
 *   B. concentric rings are sized from the node count and the viewport;
 *   C. the zoom extent always contains the fit zoom, so the full graph is
 *      always reachable;
 *   D. pan is clamped, so the graph can be moved but never lost;
 *   E. labels are dropped at a zoom where they would overlap, and kept for a
 *      selected node;
 *   F. the canvas wiring fits *and* resets translation, and recomputes on
 *      resize.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.resolve(__dirname, rel), "utf8");

const VP = await import(new URL("../src/lib/graphViewport.ts", import.meta.url).href);

const VIEWPORT = { width: 900, height: 520 };

// ---------------------------------------------------------------------------
// A. ring radius is derived from the node count
// ---------------------------------------------------------------------------

test("A. a ring's radius grows with the node count — never one fixed radius", () => {
  const r10 = VP.ringRadiusFor(10);
  const r100 = VP.ringRadiusFor(100);
  const r500 = VP.ringRadiusFor(500);
  assert.ok(r100 > r10, "100 nodes need a bigger ring than 10");
  assert.ok(r500 > r100, "500 nodes need a bigger ring than 100");

  // The defining property: adjacent nodes are at least MIN_NODE_SPACING apart.
  for (const count of [2, 3, 8, 25, 100, 250, 500]) {
    const radius = VP.ringRadiusFor(count, 90);
    const chord = 2 * radius * Math.sin(Math.PI / count);
    assert.ok(
      chord >= 90 - 1e-6,
      `${count} nodes on radius ${radius.toFixed(1)} are ${chord.toFixed(1)}px apart, below 90`,
    );
  }
});

test("A. one or zero nodes collapse to the centre instead of a huge ring", () => {
  assert.equal(VP.ringRadiusFor(0), 0);
  assert.equal(VP.ringRadiusFor(1), 0);
});

test("A. the circular layout carries a bounding box derived from the node count", () => {
  const small = VP.sizedLayoutOptions("circle", 12, VIEWPORT);
  const large = VP.sizedLayoutOptions("circle", 400, VIEWPORT);
  assert.ok(small && large);
  assert.equal(small.name, "circle");
  assert.ok(
    large.boundingBox.w > small.boundingBox.w,
    "a 400-node ring must occupy more space than a 12-node ring",
  );
  assert.equal(small.avoidOverlap, true);
  assert.equal(small.nodeDimensionsIncludeLabels, true);
});

// ---------------------------------------------------------------------------
// B. concentric layout is sized from node count + viewport
// ---------------------------------------------------------------------------

test("B. concentric ring radii grow with the node count", () => {
  const small = VP.concentricRingRadii(12, VIEWPORT, { boundToViewport: false });
  const large = VP.concentricRingRadii(300, VIEWPORT, { boundToViewport: false });
  assert.ok(large.radii.length > small.radii.length, "more nodes need more rings");
  assert.ok(
    large.radii[large.radii.length - 1] > small.radii[small.radii.length - 1],
    "the outer ring must be further out for more nodes",
  );
  // Radii are strictly increasing so no two rings share a radius.
  for (let i = 1; i < large.radii.length; i += 1) {
    assert.ok(large.radii[i] > large.radii[i - 1], "rings must not overlap");
  }
});

test("B. concentric layout stays inside the viewport when bound to it", () => {
  const { radii, scaledToFit } = VP.concentricRingRadii(500, VIEWPORT, {
    boundToViewport: true,
  });
  const outer = radii[radii.length - 1];
  const maxRadius = Math.min(VIEWPORT.width, VIEWPORT.height) / 2;
  assert.ok(scaledToFit, "a 500-node concentric layout must be scaled to fit");
  assert.ok(
    outer <= maxRadius + 1e-6,
    `outer ring ${outer.toFixed(1)} exceeds the drawable radius ${maxRadius}`,
  );
});

test("B. an empty graph produces no rings rather than a degenerate one", () => {
  assert.deepEqual(VP.concentricRingRadii(0, VIEWPORT).radii, []);
});

// ---------------------------------------------------------------------------
// C. the zoom extent always contains the fit zoom
// ---------------------------------------------------------------------------

test("C. minZoom is at or below the fit zoom, whatever a layout produces", () => {
  for (const size of [
    { width: 1000, height: 800 },
    { width: 40_000, height: 30_000 },
    { width: 400_000, height: 300_000 },
  ]) {
    const extent = VP.zoomExtentFor(size, VIEWPORT);
    assert.ok(
      extent.min <= extent.fit,
      `minZoom ${extent.min} cannot show a graph whose fit zoom is ${extent.fit}`,
    );
    assert.ok(extent.max > extent.fit, "there must be room to zoom in past fit");
    assert.ok(extent.max <= 3 + 1e-9, "maximum zoom must not run away");
  }
});

test("C. fit zoom accounts for the padding, so nodes are not clipped at the edge", () => {
  const padded = VP.fitZoomFor({ width: 1000, height: 1000 }, VIEWPORT, 32);
  const unpadded = VP.fitZoomFor({ width: 1000, height: 1000 }, VIEWPORT, 0);
  assert.ok(padded < unpadded, "padding must shrink the fit zoom");
  // 520 - 64 = 456 drawable height over 1000 model units.
  assert.ok(Math.abs(padded - 0.456) < 1e-6, `expected 0.456, got ${padded}`);
});

test("C. a tiny graph is not zoomed past the maximum", () => {
  const extent = VP.zoomExtentFor({ width: 20, height: 20 }, VIEWPORT);
  assert.ok(extent.fit > 1, "a 20x20 graph fits at more than 100%");
  assert.ok(extent.max >= extent.min * 2, "the zoom range must stay usable");
});

// ---------------------------------------------------------------------------
// D. panning is clamped
// ---------------------------------------------------------------------------

test("D. panning cannot push the graph permanently outside the viewport", () => {
  const content = { x1: -500, y1: -500, x2: 500, y2: 500 };
  const zoom = 1;

  // Far to the right: the content's left edge must stay within `overlap` of
  // the viewport's right edge.
  const right = VP.clampPan({ x: 100_000, y: 0 }, content, VIEWPORT, zoom);
  assert.ok(right.x <= VIEWPORT.width + VP.PAN_OVERLAP_PX - content.x1 * zoom);
  assert.ok(
    right.x + content.x2 * zoom >= -VP.PAN_OVERLAP_PX,
    "some of the graph must still be on screen",
  );

  const left = VP.clampPan({ x: -100_000, y: 0 }, content, VIEWPORT, zoom);
  assert.ok(left.x >= -VP.PAN_OVERLAP_PX - content.x2 * zoom);

  const down = VP.clampPan({ x: 0, y: 100_000 }, content, VIEWPORT, zoom);
  assert.ok(down.y <= VIEWPORT.height + VP.PAN_OVERLAP_PX - content.y1 * zoom);
});

test("D. a pan that is already inside the bounds is left alone", () => {
  const content = { x1: -200, y1: -150, x2: 200, y2: 150 };
  const pan = { x: 450, y: 260 };
  assert.deepEqual(VP.clampPan(pan, content, VIEWPORT, 1), pan);
});

// ---------------------------------------------------------------------------
// E. label visibility
// ---------------------------------------------------------------------------

test("E. labels are hidden at a zoom where they would overlap", () => {
  assert.equal(VP.shouldShowLabel(0.05, 500), false);
  assert.equal(VP.shouldShowLabel(1.2, 500), true);
  // A small graph always keeps its labels.
  assert.equal(VP.shouldShowLabel(0.05, 12), true);
  assert.equal(VP.labelZoomThreshold(12), 0);
});

test("E. a selected or focused node keeps its label at any zoom", () => {
  assert.equal(VP.shouldShowLabel(0.02, 500, { focused: true }), true);
});

test("E. the label threshold rises with the node count, but is bounded", () => {
  assert.ok(VP.labelZoomThreshold(200) > VP.labelZoomThreshold(50));
  assert.ok(VP.labelZoomThreshold(100_000) <= 1.4, "must stay reachable by zooming in");
});

// ---------------------------------------------------------------------------
// F. the canvas wiring
// ---------------------------------------------------------------------------

test("F. the canvas policy resets translation *and* scale when fitting", () => {
  const HOOK = read("../src/lib/useGraphCanvas.ts");
  assert.match(HOOK, /policy\.fitToView\(\)/);

  const calls = [];
  const fake = makeFakeCanvas({ width: 2000, height: 2000 });
  fake.width = () => VIEWPORT.width;
  fake.height = () => VIEWPORT.height;
  const policy = VP.attachViewportPolicy(fake, { nodeCount: 200 });
  fake.panCalls = calls;
  policy.fitToView();

  assert.ok(calls.some((p) => p.x === 0 && p.y === 0), "fit must reset the pan");
  const zoomed = calls.length ? fake.zoomLevel : null;
  assert.ok(zoomed !== null && zoomed > 0, "fit must set a zoom level");
});

test("F. attaching the policy lowers minZoom below the fit zoom for a huge graph", () => {
  const fake = makeFakeCanvas({ width: 60_000, height: 40_000 });
  fake.width = () => VIEWPORT.width;
  fake.height = () => VIEWPORT.height;
  const policy = VP.attachViewportPolicy(fake, { nodeCount: 800 });
  const extent = policy.extent();
  assert.ok(fake.minZoomValue <= extent.fit + 1e-9);
  assert.ok(fake.maxZoomValue >= 3);
});

test("F. panning is clamped on every pan event once the policy is attached", () => {
  const fake = makeFakeCanvas({ width: 20_000, height: 20_000 });
  fake.width = () => VIEWPORT.width;
  fake.height = () => VIEWPORT.height;
  VP.attachViewportPolicy(fake, { nodeCount: 300 });

  fake.currentPan = { x: 500_000, y: 500_000 };
  fake.emit("pan");
  assert.ok(
    fake.currentPan.x < 500_000,
    `pan was not clamped: ${JSON.stringify(fake.currentPan)}`,
  );
});

test("F. the hook re-fits when the container is resized", () => {
  const HOOK = read("../src/lib/useGraphCanvas.ts");
  assert.match(HOOK, /ResizeObserver/);
  assert.match(HOOK, /policy\.fitToView\(\);\s*\n\s*syncLabels/);
});

test("F. every graph view shares one set of viewport controls", () => {
  const person = read("../src/components/investigator/PersonRelationshipNetwork.tsx");
  const host = read("../src/components/investigator/MasterCaseNetwork.tsx");
  for (const [name, src] of [["people", person], ["case/entity", host]]) {
    assert.match(src, /<GraphViewControls handle=\{graphHandle\}/, `${name} view lacks controls`);
  }
  const controls = read("../src/components/common/GraphViewControls.tsx");
  assert.match(controls, /Fit view/);
  assert.match(controls, /Reset view/);
  assert.match(controls, /Zoom out/);
  assert.match(controls, /Zoom in/);
});

test("F. no graph view hardcodes a minZoom that can strand the graph", () => {
  const person = read("../src/components/investigator/PersonRelationshipNetwork.tsx");
  const host = read("../src/components/investigator/MasterCaseNetwork.tsx");
  for (const [name, src] of [["people", person], ["case/entity", host]]) {
    assert.doesNotMatch(src, /minZoom:\s*0\.2/, `${name} must not pin minZoom at 0.2`);
    assert.doesNotMatch(src, /cytoscape\(\{/, `${name} must build its canvas through the hook`);
  }
});

test("F. the entity network is bounded by default and the full graph is a mode", () => {
  const host = read("../src/components/investigator/MasterCaseNetwork.tsx");
  assert.match(host, /DEFAULT_ENTITY_NODE_BUDGET = 80/);
  assert.match(host, /limit: entityBudget/);
  assert.match(host, /labels: entityLabels\.length \? entityLabels : undefined/);
  assert.match(host, /full graph/i, "the complete graph must stay reachable");
  assert.match(host, /ENTITY_LABELS/, "node-type filters must exist");
  assert.match(host, /ENTITY_REL_TYPES/, "relationship-type filters must exist");
});

// ---------------------------------------------------------------------------
// helper
// ---------------------------------------------------------------------------

function makeFakeCanvas(content) {
  const handlers = {};
  const fake = {
    currentPan: { x: 0, y: 0 },
    zoomLevel: 1,
    minZoomValue: 0.2,
    maxZoomValue: 3.5,
    width: () => 900,
    height: () => 520,
    zoom(value) {
      if (value === undefined) return fake.zoomLevel;
      fake.zoomLevel = typeof value === "number" ? value : value.level;
      return undefined;
    },
    pan(value) {
      if (value === undefined) return fake.currentPan;
      fake.currentPan = { ...value };
      (fake.panCalls ||= []).push(fake.currentPan);
      return undefined;
    },
    fit() {},
    center() {},
    elements: () => ({ boundingBox: () => box(content), length: 1 }),
    nodes: () => ({ boundingBox: () => box(content), length: 1 }),
    minZoom(value) {
      if (value === undefined) return fake.minZoomValue;
      fake.minZoomValue = value;
      return undefined;
    },
    maxZoom(value) {
      if (value === undefined) return fake.maxZoomValue;
      fake.maxZoomValue = value;
      return undefined;
    },
    on(event, handler) {
      (handlers[event] ||= []).push(handler);
    },
    off(event, handler) {
      handlers[event] = (handlers[event] || []).filter((h) => h !== handler);
    },
    style: () => ({ update() {} }),
    emit(event) {
      (handlers[event] || []).forEach((h) => h({ target: fake }));
    },
  };
  return fake;
}

function box(content) {
  return {
    x1: -content.width / 2,
    y1: -content.height / 2,
    x2: content.width / 2,
    y2: content.height / 2,
    w: content.width,
    h: content.height,
  };
}
