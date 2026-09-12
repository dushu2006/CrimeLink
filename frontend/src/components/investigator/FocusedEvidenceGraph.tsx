/**
 * The focused evidence graph for one finding.
 *
 * This is deliberately *not* the master crime graph. It draws only the nodes
 * and relationships the backend associated with the current question or
 * selected finding — seeds plus one hop — so an investigator sees the
 * evidence around the finding instead of the whole dataset.
 *
 * The graph is a view over the evidence: selecting a node or edge reports it
 * back so the detail panel can show the same evidence the rest of the
 * workspace shows. It is never the source of truth.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { FocusedGraph } from "../../api/client";

cytoscape.use(fcose);

const LABEL_COLOR: Record<string, string> = {
  PERSON: "#1D4ED8",
  PHONE: "#0F766E",
  BANK_ACCOUNT: "#B45309",
  VEHICLE: "#7C3AED",
  LOCATION: "#15803D",
  ORGANIZATION: "#BE123C",
  CASE: "#334155",
  EVENT: "#0369A1",
  DOCUMENT: "#475569",
};

function colorFor(label: string): string {
  return LABEL_COLOR[String(label).toUpperCase()] ?? "#475569";
}

export interface GraphSelection {
  kind: "node" | "edge";
  id: string;
  label: string;
  name?: string;
}

export function FocusedEvidenceGraph({
  graph,
  height = 340,
  onSelect,
  highlightKeys,
}: {
  graph: FocusedGraph | null | undefined;
  height?: number;
  onSelect?: (selection: GraphSelection | null) => void;
  highlightKeys?: string[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [legendOpen, setLegendOpen] = useState(false);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  const elements = useMemo<ElementDefinition[]>(() => {
    const known = new Set(nodes.map((node) => node.key));
    const definitions: ElementDefinition[] = nodes.map((node) => ({
      data: {
        id: node.key,
        label: node.name || node.key,
        kind: String(node.label).toUpperCase(),
        focus: node.focus ? 1 : 0,
      },
    }));
    edges
      .filter((edge) => known.has(edge.source) && known.has(edge.target))
      .forEach((edge, index) => {
        definitions.push({
          data: {
            id: `${edge.source}->${edge.target}:${edge.rel_type}:${index}`,
            source: edge.source,
            target: edge.target,
            rel: String(edge.rel_type).replaceAll("_", " ").toLowerCase(),
            focus: known.has(edge.source) && known.has(edge.target) ? 1 : 0,
          },
        });
      });
    return definitions;
  }, [nodes, edges]);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: { name: "fcose", padding: 24, nodeRepulsion: 6000 } as never,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele: cytoscape.NodeSingular) => colorFor(String(ele.data("kind"))),
            label: "data(label)",
            color: "#0F172A",
            "font-size": 10,
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: (ele: cytoscape.NodeSingular) => (ele.data("focus") ? 26 : 18),
            height: (ele: cytoscape.NodeSingular) => (ele.data("focus") ? 26 : 18),
            "border-width": (ele: cytoscape.NodeSingular) => (ele.data("focus") ? 3 : 1),
            "border-color": "#0F172A",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#94A3B8",
            "target-arrow-color": "#94A3B8",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: "data(rel)",
            "font-size": 8,
            color: "#475569",
            "text-rotation": "autorotate",
          },
        },
        {
          selector: "node.hl",
          style: { "border-color": "#DC2626", "border-width": 4 },
        },
      ] as never,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event) => {
      const node = event.target;
      onSelect?.({
        kind: "node",
        id: String(node.id()),
        label: String(node.data("kind")),
        name: String(node.data("label")),
      });
    });
    cy.on("tap", "edge", (event) => {
      const edge = event.target;
      onSelect?.({
        kind: "edge",
        id: String(edge.id()),
        label: String(edge.data("rel")),
        name: `${edge.source().data("label")} → ${edge.target().data("label")}`,
      });
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelect?.(null);
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().removeClass("hl");
    const wanted = new Set(highlightKeys ?? []);
    if (wanted.size === 0) return;
    cy.nodes().forEach((node) => {
      if (wanted.has(String(node.id()))) node.addClass("hl");
    });
  }, [highlightKeys, elements]);

  if (nodes.length === 0) {
    return (
      <p className="muted">
        No focused evidence graph is available for this finding — the answer did not identify
        entities to draw.
      </p>
    );
  }

  const labels = Array.from(new Set(nodes.map((node) => String(node.label).toUpperCase()))).sort();
  const focusCount = nodes.filter((node) => node.focus).length;

  return (
    <div className="inv-graph">
      <div className="inv-graph-meta">
        <span className="muted">
          {nodes.length} node(s) · {edges.length} relationship(s) · {focusCount} in focus
          {graph?.truncated ? " · truncated to keep the view readable" : ""}
        </span>
        <button
          type="button"
          className="btn btn-tertiary btn-small"
          onClick={() => setLegendOpen((value) => !value)}
        >
          {legendOpen ? "Hide types" : "Show types"}
        </button>
      </div>
      <div
        className="cy inv-graph-canvas"
        ref={containerRef}
        style={{ height: `${height}px` }}
        role="img"
        aria-label="Focused evidence graph for the current finding"
      />
      {legendOpen && (
        <div className="inv-graph-legend">
          {labels.map((label) => (
            <span key={label} className="legend-item">
              <span className="dot" style={{ background: colorFor(label) }} />
              {label.replaceAll("_", " ").toLowerCase()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
