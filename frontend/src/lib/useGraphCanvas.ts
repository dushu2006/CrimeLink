/**
 * Viewport plumbing shared by every Cytoscape canvas in the console.
 *
 * The three network views each used to configure their own zoom limits, layout
 * padding and "Fit view" button, and each got it differently wrong: a fixed
 * `minZoom` that could not reach the whole graph, a fit that reset scale but
 * not translation, and stock `circle` / `concentric` layouts that sized
 * themselves from the container instead of the node count.
 *
 * This hook is the single place that:
 *   - builds the layout options from the measured container and node count;
 *   - attaches the viewport policy (zoom extent, clamped pan, fit/reset);
 *   - tracks the current zoom so labels can be dropped when they would
 *     overlap, and restored when the node is selected;
 *   - re-fits when the container is resized, so a graph never ends up outside
 *     a viewport it was laid out for.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import {
  attachViewportPolicy,
  forceLayoutOptions,
  labelZoomThreshold,
  sizedLayoutOptions,
  shouldShowLabel,
  type ViewportSize,
} from "./graphViewport";

export interface GraphCanvasOptions {
  elements: ElementDefinition[];
  /** Cytoscape stylesheet; style callbacks may read `labelsRef` for zoom-aware labels. */
  style: any[];
  /** Mutable flag a style callback can read without re-creating the stylesheet. */
  labelsRef?: React.MutableRefObject<boolean>;
  layoutName: string;
  /** Skip mounting (e.g. this tab is not the active one). */
  enabled?: boolean;
  /** Re-fit when the element set changes, rather than keeping the old view. */
  fitOnElementsChange?: boolean;
  onTapNode?: (node: any) => void;
  onTapEdge?: (edge: any) => void;
  onTapBackground?: () => void;
}

export interface GraphCanvasHandle {
  fit: () => void;
  reset: () => void;
  zoomBy: (factor: number) => void;
  /** Whether labels are readable at the current zoom. */
  labelsVisible: boolean;
  zoom: number;
}

/** Measure a container, keeping the last good size before the first paint. */
function measure(el: HTMLElement | null, fallback: ViewportSize): ViewportSize {
  if (!el) return fallback;
  const width = el.clientWidth || fallback.width;
  const height = el.clientHeight || fallback.height;
  return { width: Math.max(1, width), height: Math.max(1, height) };
}

export function useGraphCanvas(options: GraphCanvasOptions): {
  containerRef: React.RefObject<HTMLDivElement>;
  cy: Core | null;
  handle: GraphCanvasHandle;
  /** Read from inside a style callback to decide whether to draw a label. */
  labelsRef: React.MutableRefObject<boolean>;
} {
  const {
    elements,
    style,
    layoutName,
    enabled = true,
    fitOnElementsChange = true,
    onTapNode,
    onTapEdge,
    onTapBackground,
  } = options;

  const containerRef = useRef<HTMLDivElement>(null);
  const internalLabelsRef = useRef(true);
  const labelsRef = options.labelsRef ?? internalLabelsRef;
  const cyRef = useRef<Core | null>(null);
  const policyRef = useRef<ReturnType<typeof attachViewportPolicy> | null>(null);

  const [cy, setCy] = useState<Core | null>(null);
  const [zoom, setZoom] = useState(1);
  const [labelsVisible, setLabelsVisible] = useState(true);
  const [viewport, setViewport] = useState<ViewportSize>({ width: 900, height: 520 });

  const nodeCount = useMemo(
    () => elements.filter((el) => !("source" in (el.data ?? {}))).length,
    [elements],
  );
  const labelThreshold = labelZoomThreshold(nodeCount);

  const syncLabels = useCallback(
    (nextZoom: number) => {
      labelsRef.current = shouldShowLabel(nextZoom, nodeCount, {
        labelZoomThreshold: labelThreshold,
      });
      setLabelsVisible(labelsRef.current);
      // Cytoscape caches style values; force the label selector to re-run.
      cyRef.current?.style().update();
    },
    [nodeCount, labelThreshold],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !enabled) {
      cyRef.current?.destroy();
      cyRef.current = null;
      policyRef.current = null;
      setCy(null);
      return;
    }
    if (elements.length === 0) {
      cyRef.current?.destroy();
      cyRef.current = null;
      policyRef.current = null;
      setCy(null);
      return;
    }

    const size = measure(el, viewport);
    setViewport(size);

    const layout =
      sizedLayoutOptions(layoutName, nodeCount, size) ??
      forceLayoutOptions(nodeCount, size);

    const instance = cytoscape({
      container: el,
      elements,
      style,
      layout: layout as any,
      wheelSensitivity: 0.22,
      minZoom: 0.05,
      maxZoom: 3,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });

    const policy = attachViewportPolicy(instance as any, {
      nodeCount,
      onZoomChange: (z) => {
        setZoom(z);
        syncLabels(z);
      },
    });
    policy.fitToView();
    syncLabels(instance.zoom());

    instance.on("tap", "node", (evt: any) => onTapNode?.(evt.target));
    instance.on("tap", "edge", (evt: any) => onTapEdge?.(evt.target));
    instance.on("tap", (evt: any) => {
      if (evt.target === instance) onTapBackground?.();
    });

    cyRef.current = instance;
    policyRef.current = policy;
    setCy(instance);

    // A container that changes size (panel collapse, window resize) invalidates
    // the zoom extent the policy computed; recompute and re-fit.
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => {
        const next = measure(el, viewport);
        setViewport(next);
        policy.fitToView();
        syncLabels(instance.zoom());
      });
      observer.observe(el);
    }

    return () => {
      observer?.disconnect();
      policy.detach();
      instance.destroy();
      cyRef.current = null;
      policyRef.current = null;
      setCy(null);
    };
    // `elements` / `layoutName` / `viewport` are the inputs that change the
    // layout; the callbacks are read through refs by the handlers below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, layoutName, enabled, style, syncLabels]);

  // Re-fit when the element count changes without a full remount (filters).
  useEffect(() => {
    if (!fitOnElementsChange) return;
    policyRef.current?.fitToView();
    if (cyRef.current) syncLabels(cyRef.current.zoom());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeCount]);

  const handle: GraphCanvasHandle = {
    fit: () => policyRef.current?.fitToView(),
    reset: () => {
      const instance = cyRef.current;
      if (!instance) return;
      instance.zoom(1);
      instance.pan({ x: 0, y: 0 });
      policyRef.current?.fitToView();
    },
    zoomBy: (factor: number) => {
      const instance = cyRef.current;
      if (!instance) return;
      const next = Math.max(
        instance.minZoom(),
        Math.min(instance.maxZoom(), instance.zoom() * factor),
      );
      instance.zoom({ level: next, position: { x: instance.width() / 2, y: instance.height() / 2 } });
    },
    labelsVisible,
    zoom,
  };

  return { containerRef, cy, handle, labelsRef };
}
