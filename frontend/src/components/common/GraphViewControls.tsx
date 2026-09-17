/**
 * Zoom / pan / fit controls for every Cytoscape canvas.
 *
 * A graph that can be zoomed into but never zoomed back out of is worse than a
 * static picture: the investigator loses the object of the investigation and
 * has no way back.  These controls make the escape routes explicit — zoom out
 * to the point where the whole graph is on screen, zoom in to read a label,
 * fit, and a full reset of translation *and* scale.
 */

import type { GraphCanvasHandle } from "../../lib/useGraphCanvas";

interface Props {
  handle: GraphCanvasHandle;
  /** Extra controls rendered to the right of the zoom group. */
  children?: React.ReactNode;
}

function formatZoom(zoom: number): string {
  return `${Math.round(zoom * 100)}%`;
}

export function GraphViewControls({ handle, children }: Props) {
  return (
    <div
      className="graph-view-controls"
      style={{ display: "flex", gap: "var(--space-1)", alignItems: "center", flexWrap: "wrap" }}
    >
      <button
        type="button"
        className="btn btn-tertiary btn-small"
        onClick={() => handle.zoomBy(1 / 1.35)}
        title="Zoom out"
        aria-label="Zoom out"
      >
        −
      </button>
      <span
        style={{
          minWidth: 44,
          textAlign: "center",
          fontSize: "var(--text-xs)",
          fontFamily: "var(--font-mono)",
        }}
        aria-live="polite"
      >
        {formatZoom(handle.zoom)}
      </span>
      <button
        type="button"
        className="btn btn-tertiary btn-small"
        onClick={() => handle.zoomBy(1.35)}
        title="Zoom in"
        aria-label="Zoom in"
      >
        +
      </button>
      <button
        type="button"
        className="btn btn-tertiary btn-small"
        onClick={handle.fit}
        title="Bring every node back into view"
      >
        Fit view
      </button>
      <button
        type="button"
        className="btn btn-tertiary btn-small"
        onClick={handle.reset}
        title="Reset translation and scale"
      >
        Reset view
      </button>
      {!handle.labelsVisible && (
        <span className="muted" style={{ fontSize: "var(--text-xs)" }}>
          Labels hidden at this zoom — zoom in or select a node
        </span>
      )}
      {children}
    </div>
  );
}

export default GraphViewControls;
