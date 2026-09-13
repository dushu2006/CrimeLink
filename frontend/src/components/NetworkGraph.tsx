/**
 * The reusable interactive network canvas.
 *
 * Renders any node/edge list (master, case, or person scope) with the same
 * Cytoscape renderer everywhere: pan, zoom, tap-to-inspect, label/relation
 * filtering, fit-to-view, and a visible legend.  Confirmed criminals — read
 * only from source-derived criminal_status — are drawn as RED star nodes with
 * a GOLD border, so a star never comes from a centrality score.
 *
 * This component is a *view*: it draws what the graph endpoints return and
 * reports selections back to the caller.  It is never the source of truth.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { GraphEdgeRow, GraphNodeRow } from "../api/client";
import { Empty, ErrorState, Spinner } from "./Status";
import { relLabel } from "../lib/investigation";
import { isConfirmedCriminal, nodeShapeRule, getDisplayLabel } from "../lib/displayLabels";

cytoscape.use(fcose);

const LABEL_COLOR: Record<string, string> = {
  PERSON: "#1D4ED8",
  PHONE: "#059669",
  BANK_ACCOUNT: "#D97706",
  VEHICLE: "#7C3AED",
  LOCATION: "#EA580C",
  EVENT: "#475569",
  ORGANIZATION: "#2563EB",
  CASE: "#0F172A",
  DOCUMENT: "#475569",
  FIR: "#0F172A",
  TRANSACTION: "#0F766E",
  SOCIAL_ACCOUNT: "#2563EB",
};

/** Confirmed criminal: star shape, red fill, gold border. */
const CRIMINAL_FILL = "#DC2626";
const CRIMINAL_BORDER = "#F59E0B";

/**
 * Node shape rule:
 *   ★ STAR  = source-derived confirmed criminal ONLY
 *   ○ CIRCLE (ellipse) = every other entity, regardless of type
 *
 * Entity type is distinguished through colour, label text, tooltips and the
 * selected-node details panel — never through shape.
 */
function nodeShape(isCriminal: boolean): string {
  return isCriminal ? "star" : "ellipse";
}

function colorFor(label: string): string {
  return LABEL_COLOR[String(label).toUpperCase()] ?? "#1D4ED8";
}

export interface NetworkGraphProps {
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onSelectNode?: (node: GraphNodeRow | null) => void;
  onSelectEdge?: (edge: GraphEdgeRow | null) => void;
  height?: number;
  emptyMessage?: string;
  /** Optional focus key (e.g. the person target). */
  targetKey?: string | null;
  /**
   * When true, synthesise CASE nodes from entity `case_ids` so the
   * cross-case structure becomes visible:  CASE ↔ entity ↔ CASE.
   */
  showCaseNodes?: boolean;
  /** Optional callback when a case-connection edge is selected. */
  onSelectCaseConnection?: (info: CaseConnectionInfo | null) => void;
}

/** Information about a case-to-case connection via shared entities. */
export interface CaseConnectionInfo {
  caseA: string;
  caseB: string;
  sharedEntities: { key: string; name: string; label: string }[];
}

export function NetworkGraph({
  nodes,
  edges,
  loading = false,
  error = null,
  onRetry,
  onSelectNode,
  onSelectEdge,
  height = 520,
  emptyMessage = "No nodes to draw — the graph for this scope is empty.",
  targetKey = null,
  showCaseNodes = false,
  onSelectCaseConnection,
}: NetworkGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [legendOpen, setLegendOpen] = useState(true);
  const [filterLabels, setFilterLabels] = useState<string[]>([]);
  const [filterRels, setFilterRels] = useState<string[]>([]);

  const labelOptions = useMemo(
    () => Array.from(new Set(nodes.map((n) => String(n.label).toUpperCase()))).sort(),
    [nodes],
  );
  const relOptions = useMemo(
    () => Array.from(new Set(edges.map((e) => e.rel_type))).sort(),
    [edges],
  );

  // Filter the view; the caller still holds the complete graph.  Filtering a
  // view must never drop the underlying data.
  const visibleNodes = useMemo(() => {
    if (filterLabels.length === 0) return nodes;
    const wanted = new Set(filterLabels);
    return nodes.filter((n) => wanted.has(String(n.label).toUpperCase()));
  }, [nodes, filterLabels]);

  const visibleEdges = useMemo(() => {
    const keep = new Set(visibleNodes.map((n) => n.provenance_key));
    let out = edges.filter((e) => keep.has(e.source) && keep.has(e.target));
    if (filterRels.length > 0) {
      const wanted = new Set(filterRels);
      out = out.filter((e) => wanted.has(e.rel_type));
    }
    return out;
  }, [edges, visibleNodes, filterRels]);

  const elements = useMemo<ElementDefinition[]>(() => {
    const definitions: ElementDefinition[] = visibleNodes.map((node) => ({
      data: {
        id: node.provenance_key,
        name: getDisplayLabel(node),
        label: String(node.label).toUpperCase(),
        confidence: node.confidence,
        is_target: node.provenance_key === targetKey,
        is_criminal: isConfirmedCriminal(node),
        criminal_status: (node as any).criminal_status || null,
        is_case_node: false,
      },
    }));
    const nameOf = (key: string) =>
      visibleNodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 10);
    visibleEdges.forEach((edge) => {
      definitions.push({
        data: {
          id: edge.key || `${edge.source}-${edge.rel_type}-${edge.target}`,
          source: edge.source,
          target: edge.target,
          rel: relLabel(edge.rel_type),
          raw_rel: edge.rel_type,
          confidence: edge.confidence,
          title: `${nameOf(edge.source)} —${relLabel(edge.rel_type)}→ ${nameOf(edge.target)}`,
        },
      });
    });

    // Synthesise CASE nodes and CASE↔entity edges from case_ids metadata.
    if (showCaseNodes) {
      const existingIds = new Set(definitions.filter((d) => !d.data.source).map((d) => d.data.id));
      const caseNodeIds = new Set<string>();
      for (const node of visibleNodes) {
        const caseIds: string[] = (node as any).case_ids ?? [];
        for (const caseId of caseIds) {
          const caseNodeId = `__case__${caseId}`;
          if (!caseNodeIds.has(caseNodeId) && !existingIds.has(caseNodeId)) {
            caseNodeIds.add(caseNodeId);
            definitions.push({
              data: {
                id: caseNodeId,
                name: caseId,
                label: "CASE",
                confidence: 1.0,
                is_target: false,
                is_criminal: false,
                criminal_status: null,
                is_case_node: true,
              },
            });
          }
          // Add edge from entity to case
          const edgeId = `${node.provenance_key}--belongs_to--${caseNodeId}`;
          definitions.push({
            data: {
              id: edgeId,
              source: node.provenance_key,
              target: caseNodeId,
              rel: "belongs to case",
              raw_rel: "BELONGS_TO_CASE",
              confidence: 1.0,
              title: `${node.name} belongs to ${caseId}`,
              is_case_edge: true,
            },
          });
        }
      }
    }

    return definitions;
  }, [visibleNodes, visibleEdges, targetKey, showCaseNodes]);

  useEffect(() => {
    if (!containerRef.current || elements.length === 0) {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
      return;
    }
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: {
        name: "fcose",
        animate: false,
        nodeRepulsion: 12000,
        idealEdgeLength: 130,
        padding: 32,
      } as never,
      style: [
        {
          selector: "node",
          style: {
            shape: (ele: cytoscape.NodeSingular) =>
              nodeShapeRule(!!ele.data("is_criminal")),
            "background-color": (ele: cytoscape.NodeSingular) =>
              ele.data("is_criminal")
                ? CRIMINAL_FILL
                : colorFor(String(ele.data("label"))),
            label: (ele: cytoscape.NodeSingular) => {
              const name = String(ele.data("name") ?? "");
              const label = ele.data("is_case_node")
                ? `${name}\n[CASE]`
                : name.length > 22
                  ? `${name.slice(0, 21)}…`
                  : name;
              return label;
            },
            color: "#0F172A",
            "font-size": 11,
            "font-weight": (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") || ele.data("is_case_node") ? 700 : 500,
            "text-valign": "bottom",
            "text-margin-y": 5,
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
            "text-wrap": "wrap" as any,
            width: (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") ? 58
              : ele.data("is_case_node") ? 36
              : ele.data("is_criminal") ? 30 : 22,
            height: (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") ? 58
              : ele.data("is_case_node") ? 36
              : ele.data("is_criminal") ? 30 : 22,
            "border-width": (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") ? 4
              : ele.data("is_case_node") ? 3
              : ele.data("is_criminal") ? 3 : 1,
            "border-style": "solid",
            "border-color": (ele: cytoscape.NodeSingular) =>
              ele.data("is_criminal")
                ? CRIMINAL_BORDER
                : ele.data("is_case_node")
                  ? "#1E293B"
                  : ele.data("is_target")
                    ? "#B45309"
                    : "#E2E8F0",
            "overlay-padding": 4,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#94A3B8",
            "target-arrow-color": "#94A3B8",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.9,
            "curve-style": "bezier",
            "line-style": (ele: cytoscape.EdgeSingular) =>
              ele.data("staging") ? "dashed" : "solid",
            label: "data(rel)",
            "font-size": 8.5,
            "font-weight": 600,
            color: "#334155",
            "text-rotation": "autorotate",
            "text-background-opacity": 0.95,
            "text-background-color": "#FFFFFF",
            "text-background-padding": "2px",
            "text-background-shape": "roundrectangle",
            "text-border-opacity": 0.8,
            "text-border-width": 1,
            "text-border-color": "#CBD5E1",
          },
        },
        {
          selector: "node:selected",
          style: { "border-width": 4, "border-color": "#1D4ED8" },
        },
        {
          selector: "edge:selected",
          style: {
            width: 4,
            "line-color": "#1D4ED8",
            "target-arrow-color": "#1D4ED8",
          },
        },
      ] as never,
    });
    cy.on("tap", "node", (event) => {
      const key = String(event.target.id());
      onSelectNode?.(visibleNodes.find((n) => n.provenance_key === key) ?? null);
    });
    cy.on("tap", "edge", (event) => {
      const id = String(event.target.id());
      onSelectEdge?.(
        visibleEdges.find((e) => (e.key || `${e.source}-${e.rel_type}-${e.target}`) === id) ?? null,
      );
    });
    cy.on("tap", (event) => {
      if (event.target === cy) {
        onSelectNode?.(null);
        onSelectEdge?.(null);
      }
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements]);

  if (loading) return <Spinner label="Loading graph…" />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (nodes.length === 0) return <Empty message={emptyMessage} />;

  const toggle = (list: string[], set: (v: string[]) => void, value: string) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  return (
    <div className="inv-graph">
      <div className="inv-graph-meta">
        <span className="muted">
          {visibleNodes.length} node(s) · {visibleEdges.length} relationship(s)
          {targetKey ? " · person target marked" : ""}
        </span>
        <button
          type="button"
          className="btn btn-tertiary btn-small"
          onClick={() => setLegendOpen((v) => !v)}
        >
          {legendOpen ? "Hide legend" : "Show legend"}
        </button>
      </div>

      {(labelOptions.length > 1 || relOptions.length > 0) && (
        <div className="inv-graph-filters">
          {labelOptions.length > 1 && (
            <div className="chip-group">
              <span className="muted">Types:</span>
              {labelOptions.map((label) => (
                <button
                  key={label}
                  type="button"
                  className={`chip ${filterLabels.includes(label) ? "active" : ""}`}
                  onClick={() => toggle(filterLabels, setFilterLabels, label)}
                >
                  {label.replaceAll("_", " ").toLowerCase()}
                </button>
              ))}
            </div>
          )}
          {relOptions.length > 1 && (
            <div className="chip-group">
              <span className="muted">Relations:</span>
              {relOptions.map((rel) => (
                <button
                  key={rel}
                  type="button"
                  className={`chip chip-rel ${filterRels.includes(rel) ? "active" : ""}`}
                  onClick={() => toggle(filterRels, setFilterRels, rel)}
                >
                  {relLabel(rel)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="graph-canvas-wrap" style={{ position: "relative", height: `${height}px` }}>
        <div className="graph-canvas-hud">
          <div className="hud-controls">
            <button
              type="button"
              className="hud-btn"
              title="Zoom in"
              onClick={() => cyRef.current && cyRef.current.zoom(cyRef.current.zoom() * 1.25)}
            >
              <span className="material-symbols-outlined">zoom_in</span>
            </button>
            <button
              type="button"
              className="hud-btn"
              title="Zoom out"
              onClick={() => cyRef.current && cyRef.current.zoom(cyRef.current.zoom() * 0.8)}
            >
              <span className="material-symbols-outlined">zoom_out</span>
            </button>
            <button
              type="button"
              className="hud-btn"
              title="Fit to view"
              onClick={() => cyRef.current?.fit(undefined, 30)}
            >
              <span className="material-symbols-outlined">fit_screen</span>
            </button>
            <button
              type="button"
              className="hud-btn"
              title="Re-center"
              onClick={() => {
                cyRef.current?.center();
                cyRef.current?.fit(undefined, 30);
              }}
            >
              <span className="material-symbols-outlined">refresh</span>
            </button>
          </div>
        </div>
        <div
          ref={containerRef}
          className="graph-canvas"
          role="img"
          aria-label="Interactive network graph — pan, zoom and tap a node or edge to inspect"
        />
      </div>

      {legendOpen && (
        <div className="inv-graph-legend">
          <span className="legend-item" title="All entities and cases are circles">
            <span
              className="dot"
              style={{ background: "#94A3B8", borderRadius: "50%" }}
            />
            entity / case (circle)
          </span>
          <span className="legend-item" title="Source-derived confirmed criminal">
            <span
              className="dot"
              style={{
                background: CRIMINAL_FILL,
                border: `2px solid ${CRIMINAL_BORDER}`,
                clipPath:
                  "polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)",
              }}
            />
            confirmed criminal (source-derived, star shape)
          </span>
          <span className="legend-item-divider" style={{ borderTop: "1px solid #E2E8F0", width: "100%", margin: "4px 0" }} />
          {labelOptions.map((label) => (
            <span key={label} className="legend-item" title={`${label} (circle)`}>
              <span className="dot" style={{ background: colorFor(label), borderRadius: "50%" }} />
              {label.replaceAll("_", " ").toLowerCase()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
