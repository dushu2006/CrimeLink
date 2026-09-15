/**
 * Investigative Graph — makes graph an investigative tool, not decoration.
 * 
 * Requirements:
 * - Click node → highlight connections
 * - Right-click → Investigate / Expand 1-hop / Find shortest path / View evidence / Add to investigation
 * - Select two entities → Find connections
 * - Professional loading states
 */

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import cytoscape, { type Core, type ElementDefinition, type EventObject } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { GraphEdgeRow, GraphNodeRow } from "../../api/client";
import { relLabel } from "../../lib/investigation";
import { isConfirmedCriminal, nodeShapeRule, getDisplayLabel } from "../../lib/displayLabels";

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
};

const CRIMINAL_FILL = "#DC2626";
const CRIMINAL_BORDER = "#F59E0B";

function colorFor(label: string): string {
  return LABEL_COLOR[String(label).toUpperCase()] ?? "#1D4ED8";
}

export interface GraphContextAction {
  id: "investigate" | "expand_1hop" | "expand_2hop" | "shortest_path" | "common_connections" | "view_evidence" | "pin" | "focus";
  label: string;
  node?: GraphNodeRow;
  nodes?: GraphNodeRow[];
}

interface InvestigativeGraphProps {
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  targetKey?: string | null;
  showCaseNodes?: boolean;
  onSelectNode?: (node: GraphNodeRow | null) => void;
  onSelectEdge?: (edge: GraphEdgeRow | null) => void;
  onContextAction?: (action: GraphContextAction) => void;
  onSelectionChange?: (selected: GraphNodeRow[]) => void;
  height?: number;
  emptyMessage?: string;
  selectedNodeIds?: string[];
  pinnedNodeIds?: string[];
}

interface ContextMenuState {
  x: number;
  y: number;
  node: GraphNodeRow | null;
  nodes: GraphNodeRow[];
}

export function InvestigativeGraph({
  nodes,
  edges,
  loading = false,
  error = null,
  onRetry,
  targetKey = null,
  showCaseNodes = false,
  onSelectNode,
  onSelectEdge,
  onContextAction,
  onSelectionChange,
  height = 600,
  emptyMessage = "No nodes to draw — the graph for this scope is empty.",
  selectedNodeIds = [],
  pinnedNodeIds = [],
}: InvestigativeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>(selectedNodeIds);
  const [filterLabels, setFilterLabels] = useState<string[]>([]);
  const [filterRels, setFilterRels] = useState<string[]>([]);

  const labelOptions = useMemo(
    () => Array.from(new Set(nodes.map((n) => String(n.label).toUpperCase()))).sort(),
    [nodes]
  );
  const relOptions = useMemo(
    () => Array.from(new Set(edges.map((e) => e.rel_type))).sort(),
    [edges]
  );

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
    const defs: ElementDefinition[] = visibleNodes.map((node) => {
      const isPinned = pinnedNodeIds.includes(node.provenance_key);
      return {
        data: {
          id: node.provenance_key,
          name: getDisplayLabel(node),
          label: String(node.label).toUpperCase(),
          confidence: node.confidence,
          is_target: node.provenance_key === targetKey,
          is_criminal: isConfirmedCriminal(node),
          is_pinned: isPinned,
          is_selected: selectedKeys.includes(node.provenance_key),
        },
      };
    });

    const nameOf = (key: string) =>
      visibleNodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 10);

    visibleEdges.forEach((edge) => {
      defs.push({
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

    if (showCaseNodes) {
      const existingIds = new Set(defs.filter((d) => !d.data.source).map((d) => d.data.id));
      const caseNodeIds = new Set<string>();
      for (const node of visibleNodes) {
        const caseIds: string[] = (node as any).case_ids ?? [];
        for (const caseId of caseIds) {
          const caseNodeId = `__case__${caseId}`;
          if (!caseNodeIds.has(caseNodeId) && !existingIds.has(caseNodeId)) {
            caseNodeIds.add(caseNodeId);
            defs.push({
              data: {
                id: caseNodeId,
                name: caseId,
                label: "CASE",
                confidence: 1.0,
                is_target: false,
                is_criminal: false,
                is_case_node: true,
                is_pinned: false,
              },
            });
          }
          const edgeId = `${node.provenance_key}--belongs_to--${caseNodeId}`;
          defs.push({
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

    return defs;
  }, [visibleNodes, visibleEdges, targetKey, showCaseNodes, selectedKeys, pinnedNodeIds]);

  // Sync external selected ids
  useEffect(() => {
    setSelectedKeys(selectedNodeIds);
  }, [selectedNodeIds]);

  const highlightConnections = useCallback((nodeId: string | null) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("highlighted dimmed focused");
    if (!nodeId) return;
    const node = cy.getElementById(nodeId);
    if (node.empty()) return;
    const neighborhood = node.closedNeighborhood();
    cy.elements().addClass("dimmed");
    neighborhood.removeClass("dimmed").addClass("highlighted");
    node.addClass("focused");
  }, []);

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
        nodeRepulsion: 15000,
        idealEdgeLength: 140,
        padding: 40,
      } as never,
      style: [
        {
          selector: "node",
          style: {
            shape: (ele: any) => nodeShapeRule(!!ele.data("is_criminal")),
            "background-color": (ele: any) => {
              if (ele.data("is_criminal")) return CRIMINAL_FILL;
              if (ele.data("is_case_node")) return "#0F172A";
              return colorFor(String(ele.data("label")));
            },
            label: (ele: any) => {
              const name = String(ele.data("name") ?? "");
              if (ele.data("is_case_node")) return `${name}\n[CASE]`;
              return name.length > 24 ? `${name.slice(0, 23)}…` : name;
            },
            color: "#0F172A",
            "font-size": 11,
            "font-weight": (ele: any) => (ele.data("is_target") || ele.data("is_pinned") ? 700 : 500),
            "text-valign": "bottom",
            "text-margin-y": 6,
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
            "text-wrap": "wrap" as any,
            width: (ele: any) => {
              if (ele.data("is_target")) return 60;
              if (ele.data("is_pinned")) return 32;
              if (ele.data("is_criminal")) return 32;
              if (ele.data("is_case_node")) return 38;
              return 24;
            },
            height: (ele: any) => {
              if (ele.data("is_target")) return 60;
              if (ele.data("is_pinned")) return 32;
              if (ele.data("is_criminal")) return 32;
              if (ele.data("is_case_node")) return 38;
              return 24;
            },
            "border-width": (ele: any) => {
              if (ele.data("is_selected")) return 4;
              if (ele.data("is_pinned")) return 3;
              if (ele.data("is_target")) return 4;
              if (ele.data("is_criminal")) return 3;
              return 1;
            },
            "border-color": (ele: any) => {
              if (ele.data("is_selected")) return "#1D4ED8";
              if (ele.data("is_pinned")) return "#F59E0B";
              if (ele.data("is_criminal")) return CRIMINAL_BORDER;
              if (ele.data("is_target")) return "#B45309";
              return "#E2E8F0";
            },
            "overlay-padding": 4,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "border-width": 3,
            "border-color": "#1D4ED8",
          },
        },
        {
          selector: "node.focused",
          style: {
            "border-width": 4,
            "border-color": "#1D4ED8",
            "background-color": (ele: any) => {
              if (ele.data("is_criminal")) return CRIMINAL_FILL;
              return colorFor(String(ele.data("label")));
            },
          },
        },
        {
          selector: "node.dimmed",
          style: {
            opacity: 0.25,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.8,
            "line-color": "#94A3B8",
            "target-arrow-color": "#94A3B8",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.9,
            "curve-style": "bezier",
            label: "data(rel)",
            "font-size": 9,
            "font-weight": 600,
            color: "#334155",
            "text-rotation": "autorotate",
            "text-background-opacity": 0.9,
            "text-background-color": "#FFFFFF",
            "text-background-padding": "2px",
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            width: 3,
            "line-color": "#1D4ED8",
            "target-arrow-color": "#1D4ED8",
            opacity: 1,
          },
        },
        {
          selector: "edge.dimmed",
          style: {
            opacity: 0.15,
          },
        },
      ] as never,
    });

    // Single click → select + highlight connections
    cy.on("tap", "node", (event: EventObject) => {
      const id = String(event.target.id());
      if (id.startsWith("__case__")) return;
      const node = visibleNodes.find((n) => n.provenance_key === id) ?? null;
      
      // Multi-select with ctrl/meta
      const originalEvent = event.originalEvent as MouseEvent;
      const isMulti = originalEvent?.ctrlKey || originalEvent?.metaKey;
      
      let newSelection: string[];
      if (isMulti) {
        newSelection = selectedKeys.includes(id)
          ? selectedKeys.filter((k) => k !== id)
          : [...selectedKeys, id].slice(-2); // max 2 for path finding
      } else {
        newSelection = [id];
      }
      
      setSelectedKeys(newSelection);
      const selectedNodes = newSelection
        .map((k) => visibleNodes.find((n) => n.provenance_key === k))
        .filter(Boolean) as GraphNodeRow[];
      
      onSelectionChange?.(selectedNodes);
      onSelectNode?.(node);
      
      if (newSelection.length === 1) {
        highlightConnections(id);
      } else if (newSelection.length === 2) {
        // Highlight both neighborhoods
        cy.elements().removeClass("highlighted dimmed focused");
        const n1 = cy.getElementById(newSelection[0]);
        const n2 = cy.getElementById(newSelection[1]);
        const combined = n1.closedNeighborhood().union(n2.closedNeighborhood());
        cy.elements().addClass("dimmed");
        combined.removeClass("dimmed").addClass("highlighted");
        n1.addClass("focused");
        n2.addClass("focused");
      } else {
        cy.elements().removeClass("highlighted dimmed focused");
      }
      
      setContextMenu(null);
    });

    cy.on("tap", "edge", (event: EventObject) => {
      const id = String(event.target.id());
      const edge = visibleEdges.find((e) => (e.key || `${e.source}-${e.rel_type}-${e.target}`) === id) ?? null;
      onSelectEdge?.(edge);
      setContextMenu(null);
    });

    cy.on("tap", (event: EventObject) => {
      if (event.target === cy) {
        cy.elements().removeClass("highlighted dimmed focused");
        setSelectedKeys([]);
        onSelectNode?.(null);
        onSelectEdge?.(null);
        onSelectionChange?.([]);
        setContextMenu(null);
      }
    });

    // Right-click → context menu
    cy.on("cxttap", "node", (event: EventObject) => {
      const id = String(event.target.id());
      if (id.startsWith("__case__")) return;
      const node = visibleNodes.find((n) => n.provenance_key === id) ?? null;
      if (!node) return;
      
      const originalEvent = event.originalEvent as MouseEvent;
      const containerRect = containerRef.current?.getBoundingClientRect();
      if (!containerRect) return;
      
      const x = originalEvent.clientX - containerRect.left;
      const y = originalEvent.clientY - containerRect.top;
      
      // If we have 2 selected, include both
      const currentSelected = selectedKeys.includes(id)
        ? selectedKeys.map((k) => visibleNodes.find((n) => n.provenance_key === k)).filter(Boolean) as GraphNodeRow[]
        : [node];
      
      setContextMenu({
        x,
        y,
        node,
        nodes: currentSelected,
      });
      
      // Also select
      if (!selectedKeys.includes(id)) {
        setSelectedKeys([id]);
        onSelectNode?.(node);
        onSelectionChange?.([node]);
        highlightConnections(id);
      }
      
      event.preventDefault();
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements]);

  if (loading) {
    return (
      <div className="investigative-graph-loading" style={{ height }}>
        <div className="professional-loading">
          <div className="loading-spinner" />
          <div className="loading-text">
            <span className="loading-title">Analyzing network structure</span>
            <span className="loading-subtitle">Retrieving investigation context · Computing relationships</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="investigative-graph-error" style={{ height }}>
        <p className="error-message">{error}</p>
        {onRetry && (
          <button className="btn btn-secondary btn-small" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="investigative-graph-empty" style={{ height }}>
        <p className="muted">{emptyMessage}</p>
      </div>
    );
  }

  const toggle = (list: string[], set: (v: string[]) => void, value: string) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  return (
    <div className="investigative-graph" style={{ position: "relative", height }}>
      <div className="investigative-graph-toolbar">
        <div className="graph-stats">
          <span className="graph-stat">{visibleNodes.length} entities</span>
          <span className="graph-stat-dot">·</span>
          <span className="graph-stat">{visibleEdges.length} relationships</span>
          {selectedKeys.length > 0 && (
            <>
              <span className="graph-stat-dot">·</span>
              <span className="graph-stat graph-stat-selected">{selectedKeys.length} selected</span>
            </>
          )}
          {selectedKeys.length === 2 && (
            <button
              className="btn btn-primary btn-small"
              onClick={() => {
                const selected = selectedKeys
                  .map((k) => visibleNodes.find((n) => n.provenance_key === k))
                  .filter(Boolean) as GraphNodeRow[];
                onContextAction?.({
                  id: "shortest_path",
                  label: "Find shortest path",
                  nodes: selected,
                });
              }}
            >
              Find connections
            </button>
          )}
        </div>
        <div className="graph-toolbar-actions">
          <button
            className="btn btn-tertiary btn-small"
            onClick={() => cyRef.current?.fit(undefined, 30)}
            title="Fit to view"
          >
            Fit
          </button>
          <button
            className="btn btn-tertiary btn-small"
            onClick={() => {
              cyRef.current?.elements().removeClass("highlighted dimmed focused");
              setSelectedKeys([]);
              onSelectNode?.(null);
              onSelectionChange?.([]);
            }}
            title="Clear selection"
          >
            Clear
          </button>
        </div>
      </div>

      {(labelOptions.length > 1 || relOptions.length > 0) && (
        <div className="investigative-graph-filters">
          {labelOptions.length > 1 && (
            <div className="filter-group">
              <span className="filter-label">Type</span>
              {labelOptions.map((label) => (
                <button
                  key={label}
                  className={`filter-chip ${filterLabels.includes(label) ? "active" : ""}`}
                  onClick={() => toggle(filterLabels, setFilterLabels, label)}
                >
                  {label.toLowerCase()}
                </button>
              ))}
            </div>
          )}
          {relOptions.length > 1 && (
            <div className="filter-group">
              <span className="filter-label">Relation</span>
              {relOptions.slice(0, 8).map((rel) => (
                <button
                  key={rel}
                  className={`filter-chip filter-chip-rel ${filterRels.includes(rel) ? "active" : ""}`}
                  onClick={() => toggle(filterRels, setFilterRels, rel)}
                >
                  {relLabel(rel)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="graph-canvas-wrap" style={{ height: height - 80 }}>
        <div className="graph-canvas-hud">
          <div className="hud-controls">
            <button
              className="hud-btn"
              title="Zoom in"
              onClick={() => cyRef.current && cyRef.current.zoom(cyRef.current.zoom() * 1.25)}
            >
              +
            </button>
            <button
              className="hud-btn"
              title="Zoom out"
              onClick={() => cyRef.current && cyRef.current.zoom(cyRef.current.zoom() * 0.8)}
            >
              −
            </button>
            <button className="hud-btn" title="Fit" onClick={() => cyRef.current?.fit(undefined, 30)}>
              ⊡
            </button>
          </div>
        </div>
        <div
          ref={containerRef}
          className="graph-canvas investigative-graph-canvas"
          role="img"
          aria-label="Investigative network graph — click to highlight connections, right-click for actions, Ctrl+click to select two entities"
        />
        
        {contextMenu && (
          <div
            className="graph-context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="context-menu-header">
              <span className="context-menu-title">
                {contextMenu.nodes.length > 1
                  ? `${contextMenu.nodes.length} entities selected`
                  : contextMenu.node?.name.slice(0, 30)}
              </span>
              <span className="context-menu-type">{contextMenu.node?.label}</span>
            </div>
            <div className="context-menu-items">
              <button
                className="context-menu-item"
                onClick={() => {
                  if (contextMenu.node) {
                    onContextAction?.({ id: "investigate", label: "Investigate", node: contextMenu.node });
                  }
                  setContextMenu(null);
                }}
              >
                <span className="context-menu-icon">🔍</span> Investigate
              </button>
              <button
                className="context-menu-item"
                onClick={() => {
                  if (contextMenu.node) {
                    onContextAction?.({ id: "expand_1hop", label: "Expand 1-hop", node: contextMenu.node });
                  }
                  setContextMenu(null);
                }}
              >
                <span className="context-menu-icon">↗</span> Expand 1-hop
              </button>
              <button
                className="context-menu-item"
                onClick={() => {
                  if (contextMenu.node) {
                    onContextAction?.({ id: "expand_2hop", label: "Expand 2-hop", node: contextMenu.node });
                  }
                  setContextMenu(null);
                }}
              >
                <span className="context-menu-icon">↗↗</span> Expand 2-hop
              </button>
              {contextMenu.nodes.length === 2 && (
                <>
                  <div className="context-menu-divider" />
                  <button
                    className="context-menu-item context-menu-item-primary"
                    onClick={() => {
                      onContextAction?.({
                        id: "shortest_path",
                        label: "Find shortest path",
                        nodes: contextMenu.nodes,
                      });
                      setContextMenu(null);
                    }}
                  >
                    <span className="context-menu-icon">🔗</span> Find shortest path
                  </button>
                  <button
                    className="context-menu-item"
                    onClick={() => {
                      onContextAction?.({
                        id: "common_connections",
                        label: "Find common connections",
                        nodes: contextMenu.nodes,
                      });
                      setContextMenu(null);
                    }}
                  >
                    <span className="context-menu-icon">◍</span> Find common connections
                  </button>
                </>
              )}
              <div className="context-menu-divider" />
              <button
                className="context-menu-item"
                onClick={() => {
                  if (contextMenu.node) {
                    onContextAction?.({ id: "view_evidence", label: "View evidence", node: contextMenu.node });
                  }
                  setContextMenu(null);
                }}
              >
                <span className="context-menu-icon">📄</span> View evidence
              </button>
              <button
                className="context-menu-item"
                onClick={() => {
                  if (contextMenu.node) {
                    onContextAction?.({ id: "pin", label: "Add to investigation", node: contextMenu.node });
                  }
                  setContextMenu(null);
                }}
              >
                <span className="context-menu-icon">📌</span> Add to investigation
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="investigative-graph-legend">
        <span className="legend-item">
          <span className="dot" style={{ background: "#94A3B8", borderRadius: "50%" }} /> entity
        </span>
        <span className="legend-item">
          <span
            className="dot"
            style={{ background: CRIMINAL_FILL, border: `2px solid ${CRIMINAL_BORDER}` }}
          />{" "}
          confirmed criminal
        </span>
        <span className="legend-item">
          <span className="dot" style={{ background: "#F59E0B", borderRadius: "50%" }} /> pinned
        </span>
        <span className="legend-item muted">Click to highlight · Ctrl+click to select two · Right-click for actions</span>
      </div>
    </div>
  );
}
