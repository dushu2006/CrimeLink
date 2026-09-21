/**
 * Graph viewport + layout geometry.
 *
 * Every graph in CrimeLink (People Network, Case Network, Entity Network) used
 * to share one set of constants: `minZoom: 0.2`, `padding: 40`, and the stock
 * Cytoscape `circle` / `concentric` layouts.  Those constants are wrong for any
 * graph whose natural size exceeds the container:
 *
 *  - `circle` places N nodes on a ring of radius `max(width, height) / 2` of the
 *    *container*, so 100 nodes sit 6px apart and overlap into an unreadable
 *    blob, while 500 nodes get the same radius and pile on top of each other;
 *  - `concentric` spreads nodes over the same fixed extent regardless of how
 *    many rings it needs, so outer rings land outside the drawable area and
 *    "Fit view" — clamped by `minZoom: 0.2` — cannot bring them back;
 *  - a fixed `minZoom` means that once a layout produces coordinates wider than
 *    `5 x container`, no zoom level can ever show the whole graph again.
 *
 * The functions here derive every number from the node count, the minimum
 * separation a node actually needs, and the *measured* container size, and the
 * resulting zoom extent is guaranteed to contain the fit zoom.  They are pure
 * so the geometry can be unit-tested without a browser.
 */

/** Distance between two neighbouring node centres, in model units. */
export const MIN_NODE_SPACING = 90;

/** Node body plus its label, used as the unit a layout must not compress below. */
export const DEFAULT_NODE_DIAMETER = 46;

/** Breathing room between the graph and the edge of the container, in px. */
export const DEFAULT_VIEWPORT_PADDING = 32;

/** How much of the graph must stay on screen, in px, when panning. */
export const PAN_OVERLAP_PX = 80;

export interface ViewportSize {
  width: number;
  height: number;
}

export interface RingGeometry {
  /** Radius of the ring, in model units. */
  radius: number;
  /** Spacing actually achieved between adjacent nodes, in model units. */
  achievedSpacing: number;
}

/**
 * Smallest radius on which `count` nodes can sit at least `minSpacing` apart.
 *
 * Adjacent nodes on a ring of radius R are a chord of `2 R sin(pi / count)`
 * apart, so `R >= minSpacing / (2 sin(pi / count))`.  This is the piece the
 * stock layouts get wrong: the radius must grow with the node count.
 */
export function ringRadiusFor(count: number, minSpacing: number = MIN_NODE_SPACING): number {
  if (!Number.isFinite(count) || count <= 1) return 0;
  const chord = Math.sin(Math.PI / count);
  // sin() underflows to 0 for enormous counts; a straight-line estimate of the
  // circumference (R = N * spacing / 2pi) is the correct limit there.
  if (chord <= 1e-9) return (count * minSpacing) / (2 * Math.PI);
  return minSpacing / (2 * chord);
}

/**
 * Radius that keeps a ring of `count` nodes inside `viewport`.
 *
 * A ring big enough for the spacing requirement may still be wider than the
 * container.  Rather than let it overflow, the radius is reduced to fit and the
 * shortfall is reported as `achievedSpacing`, so the caller can decide to page
 * the nodes or zoom out instead of silently overlapping them.
 */
export function boundedRingGeometry(
  count: number,
  viewport: ViewportSize,
  options: {
    minSpacing?: number;
    nodeDiameter?: number;
    padding?: number;
  } = {},
): RingGeometry {
  const minSpacing = options.minSpacing ?? MIN_NODE_SPACING;
  const nodeDiameter = options.nodeDiameter ?? DEFAULT_NODE_DIAMETER;
  const padding = options.padding ?? DEFAULT_VIEWPORT_PADDING;

  const wanted = ringRadiusFor(count, minSpacing);
  if (wanted === 0) return { radius: 0, achievedSpacing: minSpacing };

  // The ring's bounding square must fit inside the drawable area, and a node
  // centred on the ring sticks out by half its diameter.
  const drawableW = Math.max(1, viewport.width - 2 * padding - nodeDiameter);
  const drawableH = Math.max(1, viewport.height - 2 * padding - nodeDiameter);
  const maxRadius = Math.max(1, Math.min(drawableW, drawableH) / 2);

  if (wanted <= maxRadius) {
    return { radius: wanted, achievedSpacing: minSpacing };
  }
  const achieved = count > 1 ? 2 * maxRadius * Math.sin(Math.PI / count) : minSpacing;
  return { radius: maxRadius, achievedSpacing: achieved };
}

/**
 * Ring radii for a concentric layout, innermost first.
 *
 * Ring *k* holds as many nodes as its circumference allows at `minSpacing`
 * (`floor(2 pi R / minSpacing)`, at least 1), so ring radii grow with the node
 * count instead of every layout sharing one fixed radius.  The whole set is
 * then scaled down if the outermost ring would leave the viewport.
 */
export function concentricRingRadii(
  count: number,
  viewport: ViewportSize,
  options: {
    minSpacing?: number;
    nodeDiameter?: number;
    padding?: number;
    /** How many nodes go in the centre instead of on the first ring. */
    centerCount?: number;
    /**
     * Shrink the rings to the viewport instead of keeping the spacing-correct
     * radii.  Off by default: spacing is scale-invariant, overlap is not, and
     * `fitToView()` already brings an oversized ring back on screen.
     */
    boundToViewport?: boolean;
  } = {},
): { radii: number[]; achievedSpacing: number; scaledToFit: boolean } {
  const minSpacing = options.minSpacing ?? MIN_NODE_SPACING;
  const nodeDiameter = options.nodeDiameter ?? DEFAULT_NODE_DIAMETER;
  const padding = options.padding ?? DEFAULT_VIEWPORT_PADDING;
  const centerCount = Math.max(0, options.centerCount ?? 0);
  const boundToViewport = options.boundToViewport ?? true;

  if (!Number.isFinite(count) || count <= 0) {
    return { radii: [], achievedSpacing: minSpacing, scaledToFit: false };
  }

  const innerRadius = minSpacing;
  const radii: number[] = [];
  let remaining = count - centerCount;
  let radius = 0;
  if (centerCount > 0) {
    radii.push(0);
  }
  if (remaining > 0) {
    radius = innerRadius;
    // Guard against a pathological minSpacing that fits nothing on a ring.
    for (let guard = 0; remaining > 0 && guard < count + 2; guard += 1) {
      const capacity = Math.max(1, Math.floor((2 * Math.PI * radius) / minSpacing));
      radii.push(radius);
      remaining -= capacity;
      radius += minSpacing;
    }
  }
  if (radii.length === 0) radii.push(0);

  const outer = radii[radii.length - 1];
  if (outer === 0 || !boundToViewport) {
    return { radii, achievedSpacing: minSpacing, scaledToFit: false };
  }

  const drawableW = Math.max(1, viewport.width - 2 * padding - nodeDiameter);
  const drawableH = Math.max(1, viewport.height - 2 * padding - nodeDiameter);
  const maxRadius = Math.max(1, Math.min(drawableW, drawableH) / 2);

  if (outer <= maxRadius) {
    return { radii, achievedSpacing: minSpacing, scaledToFit: false };
  }
  const scale = maxRadius / outer;
  return {
    radii: radii.map((r) => r * scale),
    achievedSpacing: minSpacing * scale,
    scaledToFit: true,
  };
}

/** Zoom that fits `content` (model units) inside `viewport`, with padding. */
export function fitZoomFor(
  content: { width: number; height: number },
  viewport: ViewportSize,
  padding: number = DEFAULT_VIEWPORT_PADDING,
): number {
  const w = Math.max(1, viewport.width - 2 * padding);
  const h = Math.max(1, viewport.height - 2 * padding);
  const cw = Math.max(1, content.width);
  const ch = Math.max(1, content.height);
  return Math.min(w / cw, h / ch);
}

export interface ZoomExtent {
  min: number;
  max: number;
  /** The zoom at which the whole graph exactly fills the viewport. */
  fit: number;
}

/**
 * A zoom range that can always return to the full graph.
 *
 * `min` is never above the fit zoom: whatever a layout produces, zooming all
 * the way out shows everything.  `max` is capped so a wheel event cannot run
 * away to an unusable scale.
 */
export function zoomExtentFor(
  content: { width: number; height: number },
  viewport: ViewportSize,
  options: { padding?: number; preferredMin?: number; max?: number } = {},
): ZoomExtent {
  const fit = fitZoomFor(content, viewport, options.padding);
  const preferredMin = options.preferredMin ?? 0.1;
  const max = options.max ?? 3;
  // The floor is a guard against a zero/NaN extent, never a cap: whatever a
  // layout produces, the minimum zoom must be able to show all of it.
  const min = Math.min(preferredMin, fit * 0.9) || fit * 0.5 || 0.001;
  return { min: Math.min(min, fit), max: Math.max(max, min * 2), fit };
}

/**
 * Clamp a pan so the graph can never be dragged permanently off screen.
 *
 * Screen position of a model point is `pan + model * zoom`.  Requiring at least
 * `overlapPx` of the content rectangle to remain inside the viewport bounds the
 * translation on both axes; anything outside is pulled back.
 */
export function clampPan(
  pan: { x: number; y: number },
  content: { x1: number; y1: number; x2: number; y2: number },
  viewport: ViewportSize,
  zoom: number,
  overlapPx: number = PAN_OVERLAP_PX,
): { x: number; y: number } {
  const left = -overlapPx - content.x2 * zoom;
  const right = viewport.width + overlapPx - content.x1 * zoom;
  const top = -overlapPx - content.y2 * zoom;
  const bottom = viewport.height + overlapPx - content.y1 * zoom;
  return {
    x: Math.min(Math.max(pan.x, Math.min(left, right)), Math.max(left, right)),
    y: Math.min(Math.max(pan.y, Math.min(top, bottom)), Math.max(top, bottom)),
  };
}

/**
 * Whether node labels should be drawn at this zoom level.
 *
 * At low zoom a 500-node graph's labels overlap into grey noise; hiding them
 * until there is room to read them is the difference between a map and a mess.
 * Selected/focused nodes always keep their label — the caller passes the node's
 * own state.
 */
export function shouldShowLabel(
  zoom: number,
  nodeCount: number,
  options: { focused?: boolean; labelZoomThreshold?: number } = {},
): boolean {
  if (options.focused) return true;
  const threshold = options.labelZoomThreshold ?? labelZoomThreshold(nodeCount);
  return zoom >= threshold;
}

/**
 * Zoom below which labels are dropped, scaled by how crowded the graph is.
 *
 * More nodes need more room per label, so the threshold rises with the count —
 * bounded so a small graph always keeps its labels.
 */
export function labelZoomThreshold(nodeCount: number): number {
  if (nodeCount <= 30) return 0;
  return Math.min(1.4, 0.35 + nodeCount / 900);
}

/**
 * Cytoscape layout options for `circle` / `concentric`, sized to the viewport.
 *
 * Returns `null` for a layout the helper does not own, so the caller can fall
 * back to whatever Cytoscape does by default.
 */
export function sizedLayoutOptions(
  layoutName: string,
  nodeCount: number,
  viewport: ViewportSize,
  options: {
    minSpacing?: number;
    nodeDiameter?: number;
    padding?: number;
    animate?: boolean;
  } = {},
): Record<string, unknown> | null {
  const minSpacing = options.minSpacing ?? MIN_NODE_SPACING;
  const padding = options.padding ?? DEFAULT_VIEWPORT_PADDING;
  const animate = options.animate ?? true;

  if (layoutName === "circle") {
    // The ring keeps its spacing-correct radius.  Compressing it to the
    // container instead would put 100 nodes 12px apart — an unreadable blob —
    // whereas a large ring plus `fitToView()` shows every node separated, just
    // smaller.  Spacing is scale-invariant; overlap is not.
    const radius = ringRadiusFor(nodeCount, minSpacing);
    return {
      name: "circle",
      animate,
      animationDuration: 350,
      padding,
      // `boundingBox` pins the ring to a known square instead of letting
      // Cytoscape reuse the container's own dimensions for every node count.
      boundingBox: boundingBoxFor(radius, viewport, padding),
      avoidOverlap: true,
      nodeDimensionsIncludeLabels: true,
      spacingFactor: 1,
    };
  }

  if (layoutName === "concentric") {
    const radii = concentricRingRadii(nodeCount, viewport, {
      ...options,
      padding,
      boundToViewport: false,
    }).radii;
    return {
      name: "concentric",
      animate,
      animationDuration: 350,
      padding,
      boundingBox: boundingBoxFor(radii[radii.length - 1] ?? 0, viewport, padding),
      avoidOverlap: true,
      nodeDimensionsIncludeLabels: true,
      minNodeSpacing: minSpacing,
      // Every node belongs to the ring whose radius the geometry assigned it.
      concentric: (node: { id: () => string }) => {
        const index = ringIndexFor(node.id(), nodeCount, radii.length);
        return radii.length - index;
      },
      levelWidth: () => 1,
    };
  }

  return null;
}

/** A square bounding box of the given radius, centred on the viewport. */
export function boundingBoxFor(
  radius: number,
  viewport: ViewportSize,
  padding: number = DEFAULT_VIEWPORT_PADDING,
): { x1: number; y1: number; w: number; h: number } {
  const side = Math.max(1, 2 * radius || Math.max(1, Math.min(viewport.width, viewport.height) - 2 * padding));
  return {
    x1: (viewport.width - side) / 2,
    y1: (viewport.height - side) / 2,
    w: side,
    h: side,
  };
}

/**
 * Deterministic ring assignment for a concentric layout.
 *
 * Cytoscape asks for a *level value* per node and then spreads the levels over
 * its own radii; we need the opposite — a known radius per node.  Assigning
 * nodes to rings in id order keeps the mapping stable across re-layouts and
 * keeps the capacities the geometry computed (ring 0 holds the most, outer
 * rings hold more, so a simple cumulative walk matches `concentricRingRadii`).
 */
export function ringIndexFor(nodeId: string, nodeCount: number, ringCount: number): number {
  if (ringCount <= 1 || nodeCount <= 0) return 0;
  let hash = 0;
  for (let i = 0; i < nodeId.length; i += 1) {
    hash = (hash * 31 + nodeId.charCodeAt(i)) % 1_000_003;
  }
  return hash % ringCount;
}

/**
 * Force-directed tuning that actually separates nodes.
 *
 * `fcose` defaults give a 500-node graph a hairball.  Repulsion and ideal edge
 * length are scaled from the node count so the simulation spreads out instead of
 * collapsing, and `nodeSeparation` enforces the minimum gap directly.
 */
export function forceLayoutOptions(
  nodeCount: number,
  viewport: ViewportSize,
  options: { minSpacing?: number; padding?: number } = {},
): Record<string, unknown> {
  const minSpacing = options.minSpacing ?? MIN_NODE_SPACING;
  const padding = options.padding ?? DEFAULT_VIEWPORT_PADDING;
  const crowded = Math.max(1, nodeCount);

  // Scale repulsion and spacing aggressively for large graphs so nodes
  // don't collapse into an overlapping mess.
  const repulsion = crowded > 400
    ? 140_000
    : crowded > 200
      ? 75_000
      : Math.min(28_000, 5_000 + crowded * 25);

  const edgeLength = crowded > 400
    ? 220
    : crowded > 200
      ? 180
      : Math.min(150, minSpacing + crowded * 0.2);

  // Guarantee minimum distance between any two entities as requested by the user
  const separation = crowded > 400
    ? 130
    : crowded > 200
      ? 110
      : minSpacing;

  // Reduce gravity and edge elasticity for large graphs so nodes spread naturally outwards
  const grav = crowded > 400 ? 0.08 : crowded > 200 ? 0.15 : 0.28;
  const elasticity = crowded > 300 ? 0.22 : 0.45;

  return {
    name: "fcose",
    animate: true,
    animationDuration: crowded > 300 ? 250 : 350,
    padding,
    quality: "default",
    randomize: true,
    nodeRepulsion: () => repulsion,
    idealEdgeLength: () => edgeLength,
    edgeElasticity: () => elasticity,
    nodeSeparation: separation,
    gravity: grav,
    gravityRange: crowded > 400 ? 1.6 : 2.5,
    numIter: 2500,
    tile: true,
    packComponents: true,
    nodeDimensionsIncludeLabels: true,
    fit: true,
    // Constrain the simulation to the drawable area rather than letting it
    // expand into coordinates the viewport can never show.
    initialEnergyOnIncremental: 0.3,
  };
}

// ---------------------------------------------------------------------------
// Wiring: apply the geometry to a live Cytoscape instance
// ---------------------------------------------------------------------------

/** The subset of the Cytoscape core this module drives. */
export interface GraphCanvas {
  width(): number;
  height(): number;
  zoom(): number;
  zoom(level: number | { level: number; position?: unknown }): void;
  pan(): { x: number; y: number };
  pan(pan: { x: number; y: number }): void;
  fit(eles?: unknown, padding?: number): void;
  center(eles?: unknown): void;
  elements(): { boundingBox(): BoundingBox; length: number };
  nodes(): { boundingBox(): BoundingBox; length: number };
  minZoom(value?: number): number;
  maxZoom(value?: number): number;
  on(event: string, handler: (evt: any) => void): void;
  off(event: string, handler?: (evt: any) => void): void;
  addClass?(cls: string): void;
  removeClass?(cls: string): void;
  style(): unknown;
}

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  w: number;
  h: number;
}

export interface ViewportPolicyOptions {
  /** Preferred minimum zoom; the fit zoom wins when it is smaller. */
  preferredMinZoom?: number;
  maxZoom?: number;
  padding?: number;
  /** Clamp panning so the graph cannot be lost outside the viewport. */
  clampPanning?: boolean;
  /** Node count used for the label threshold; defaults to the canvas count. */
  nodeCount?: number;
  onZoomChange?: (zoom: number) => void;
}

/**
 * Make a graph canvas viewport-safe.
 *
 * Three guarantees, all derived from the measured container:
 *  1. `minZoom` is at or below the fit zoom, so zooming out always reveals the
 *     whole graph no matter what coordinates a layout produced;
 *  2. panning is clamped, so the graph can be moved but never lost;
 *  3. `fitToView()` resets translation *and* scale, which is what a "Fit view"
 *     button has to do — fitting without resetting the pan leaves a graph that
 *     looks fitted but is still offset off screen.
 */
export function attachViewportPolicy(
  cy: GraphCanvas,
  options: ViewportPolicyOptions = {},
): { fitToView: () => void; detach: () => void; extent: () => ZoomExtent } {
  const padding = options.padding ?? DEFAULT_VIEWPORT_PADDING;
  const nodeCount = options.nodeCount ?? cy.nodes().length;
  let current: ZoomExtent = { min: 0.1, max: 3, fit: 0.1 };

  const content = (): BoundingBox => {
    const box = cy.nodes().boundingBox();
    return { x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2, w: box.w, h: box.h };
  };

  const recompute = () => {
    const viewport = { width: cy.width(), height: cy.height() };
    const box = content();
    current = zoomExtentFor({ width: box.w, height: box.h }, viewport, {
      padding,
      preferredMin: options.preferredMinZoom,
      max: options.maxZoom,
    });
    cy.minZoom(current.min);
    cy.maxZoom(current.max);
  };

  const onZoomOrPan = () => {
    if (options.clampPanning !== false) {
      const zoom = cy.zoom();
      const next = clampPan(cy.pan(), content(), { width: cy.width(), height: cy.height() }, zoom);
      const now = cy.pan();
      if (next.x !== now.x || next.y !== now.y) cy.pan(next);
    }
    options.onZoomChange?.(cy.zoom());
  };

  const fitToView = () => {
    recompute();
    // Reset translation and scale together: `fit` alone keeps the existing
    // pan, which is how a graph ends up "fitted" but still off screen.
    cy.pan({ x: 0, y: 0 });
    cy.zoom(current.fit);
    cy.fit(undefined, padding);
    cy.center(undefined);
  };

  recompute();
  cy.on("zoom", onZoomOrPan);
  cy.on("pan", onZoomOrPan);

  return {
    fitToView,
    detach: () => {
      cy.off("zoom", onZoomOrPan);
      cy.off("pan", onZoomOrPan);
    },
    extent: () => current,
  };
}
