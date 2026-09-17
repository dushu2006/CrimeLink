/**
 * MASTER CASE NETWORK
 *
 * Primary cross-case visualization across the active dataset on /investigate.
 *
 * Three genuinely different graphs, one per tab.  Each renders different data
 * from a different endpoint — the tab is never a title change over the same
 * picture:
 *
 * 1. PEOPLE NETWORK (default, the investigator's primary graph):
 *    - Nodes are PERSON and nothing else; edges are person-to-person
 *      relationships the dataset actually supports.
 *    - Phones, accounts, vehicles, addresses, organisations, call records and
 *      transfers are walked server-side and collapse into one aggregated edge
 *      that reports how many records back it.  Selecting the edge reveals them.
 *    - Rendered by <PersonRelationshipNetwork /> from GET /graph/master/relationships.
 *
 * 2. CASE NETWORK (macro overview):
 *    - Main nodes are CASES (always circles).
 *    - Edges connect cases with evidence-backed shared entities or relationships.
 *    - Edge thickness communicates connection strength.
 *    - Clicking an edge explains WHY (narrative, analytical basis, supporting
 *      evidence opening in SourceViewer, contradictory evidence, data gaps,
 *      next direction).
 *    - Clicking a case displays connected cases, shared entities, and [OPEN ENTITY NETWORK].
 *    - Data from GET /graph/master/case-network.
 *
 * 3. ENTITY NETWORK (micro drill-down):
 *    - The deeper multi-case evidence/entity graph: phones, accounts, vehicles,
 *      locations, organisations, people and the relationships between them.
 *    - Shape rule: ONLY confirmed criminals get ★ STAR; everything else is ○ CIRCLE.
 *    - Data from GET /graph/master.
 *
 * Universal rules:
 * - Criminal status is source-derived; network position never alters legal status.
 * - Every piece of evidence opens in the existing SourceViewer.
 * - Strict active dataset isolation: no legacy or NULL records.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  masterCaseNetwork,
  masterGraph,
  type GraphEdgeRow,
  type GraphNodeRow,
  type MasterCaseEdge,
  type MasterCaseNetworkResult,
  type MasterCaseNode,
  type MasterCaseSharedEntity,
} from "../../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../Status";
import { DocumentFileLink, EvidencePointerLink, ReferenceLink } from "../EvidenceLink";
import { TechnicalDetails } from "../TechnicalDetails";
import PersonRelationshipNetwork from "./PersonRelationshipNetwork";
import { isConfirmedCriminal, nodeShapeRule, getDisplayLabel } from "../../lib/displayLabels";

cytoscape.use(fcose);

type NetworkLevel = "people" | "case" | "entity";

const CRIMINAL_FILL = "#DC2626";
const CRIMINAL_BORDER = "#F59E0B";

const LABEL_COLOR: Record<string, string> = {
  CASE: "#0F172A",
  PERSON: "#1D4ED8",
  PHONE: "#059669",
  BANK_ACCOUNT: "#D97706",
  BANKACCOUNT: "#D97706",
  VEHICLE: "#7C3AED",
  LOCATION: "#EA580C",
  ORGANIZATION: "#2563EB",
  DOCUMENT: "#475569",
  FIR: "#0F172A",
  TRANSACTION: "#0F766E",
};

function entityColor(label: string): string {
  return LABEL_COLOR[label.toUpperCase().replace(/\s+/g, "_")] ?? "#1D4ED8";
}

interface MasterCaseNetworkProps {
  activeDatasetId?: string | null;
  onSelectCaseForEntityView?: (caseId: string) => void;
}

export default function MasterCaseNetwork({ activeDatasetId }: MasterCaseNetworkProps) {
  // People first: the default investigator view is the person-to-person graph.
  const [level, setLevel] = useState<NetworkLevel>("people");
  const [layoutName, setLayoutName] = useState<"fcose" | "circle" | "concentric">("fcose");

  // Master Case Network state
  const [caseNetwork, setCaseNetwork] = useState<MasterCaseNetworkResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Selected case or connection
  const [selectedCase, setSelectedCase] = useState<MasterCaseNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<MasterCaseEdge | null>(null);

  // Entity Network state
  const [entityNodes, setEntityNodes] = useState<GraphNodeRow[]>([]);
  const [entityEdges, setEntityEdges] = useState<GraphEdgeRow[]>([]);
  const [entityLoading, setEntityLoading] = useState(false);
  const [entityError, setEntityError] = useState<string | null>(null);
  const [selectedEntityNode, setSelectedEntityNode] = useState<GraphNodeRow | null>(null);
  const [filterCaseId, setFilterCaseId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  // ---- Fetch Master Case Network --------------------------------------------
  const loadCaseNetwork = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedCase(null);
    setSelectedEdge(null);
    try {
      const data = await masterCaseNetwork();
      setCaseNetwork(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCaseNetwork();
  }, [loadCaseNetwork, activeDatasetId]);

  // ---- Fetch Entity Master Network ------------------------------------------
  const loadEntityNetwork = useCallback(async () => {
    setEntityLoading(true);
    setEntityError(null);
    try {
      const data = await masterGraph();
      setEntityNodes(data.nodes);
      setEntityEdges(data.edges);
    } catch (err) {
      setEntityError(err instanceof Error ? err.message : String(err));
    } finally {
      setEntityLoading(false);
    }
  }, []);

  useEffect(() => {
    if (level === "entity" && entityNodes.length === 0 && !entityLoading) {
      void loadEntityNetwork();
    }
  }, [level, entityNodes.length, entityLoading, loadEntityNetwork]);

  // Filtered entity nodes if filterCaseId is set
  const visibleEntityNodes = useMemo(() => {
    if (!filterCaseId) return entityNodes;
    return entityNodes.filter((n) => ((n as any).case_ids ?? []).includes(filterCaseId));
  }, [entityNodes, filterCaseId]);

  const visibleEntityEdges = useMemo(() => {
    const keep = new Set(visibleEntityNodes.map((n) => n.provenance_key));
    return entityEdges.filter((e) => keep.has(e.source) && keep.has(e.target));
  }, [entityEdges, visibleEntityNodes]);

  /** How many stars the legend is actually promising — the legend must not lie. */
  const entityCriminalCount = useMemo(
    () => visibleEntityNodes.filter((n) => isConfirmedCriminal(n)).length,
    [visibleEntityNodes],
  );

  // ---- Build Cytoscape Elements for Case Network -----------------------------
  const caseElements = useMemo<ElementDefinition[]>(() => {
    if (!caseNetwork) return [];
    const elements: ElementDefinition[] = [];

    // Case nodes (CIRCLES)
    for (const node of caseNetwork.nodes) {
      elements.push({
        data: {
          id: node.id,
          name: node.case_number,
          title: node.title,
          label: "CASE",
          is_criminal: false,
          document_count: node.document_count,
          entity_count: node.entity_count,
          raw_node: node,
        },
      });
    }

    // Case connection edges
    for (const edge of caseNetwork.edges) {
      const edgeWeight = edge.strength === "STRONG" ? 5 : edge.strength === "MODERATE" ? 3.5 : 2;
      elements.push({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: `${edge.shared_entity_count} shared`,
          strength: edge.strength,
          weight: edgeWeight,
          raw_edge: edge,
        },
      });
    }

    return elements;
  }, [caseNetwork]);

  // ---- Build Cytoscape Elements for Entity Network ---------------------------
  const entityElements = useMemo<ElementDefinition[]>(() => {
    const elements: ElementDefinition[] = [];

    for (const node of visibleEntityNodes) {
      const isCriminal = isConfirmedCriminal(node);
      elements.push({
        data: {
          id: node.provenance_key,
          name: getDisplayLabel(node),
          label: String(node.label).toUpperCase(),
          is_criminal: isCriminal,
          raw_node: node,
        },
      });
    }

    for (const edge of visibleEntityEdges) {
      elements.push({
        data: {
          id: edge.key || `${edge.source}-${edge.rel_type}-${edge.target}`,
          source: edge.source,
          target: edge.target,
          label: edge.rel_type,
          raw_edge: edge,
        },
      });
    }

    return elements;
  }, [visibleEntityNodes, visibleEntityEdges]);

  // ---- Initialize & Update Cytoscape Canvas ----------------------------------
  useEffect(() => {
    if (!containerRef.current) return;
    if (level === "people") {
      // The person-to-person graph owns its own canvas; make sure no stale
      // entity/case instance is left mounted underneath it.
      cyRef.current?.destroy();
      cyRef.current = null;
      return;
    }

    const currentElements = level === "case" ? caseElements : entityElements;
    if (currentElements.length === 0) {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
      return;
    }

    // Stylesheet enforcing universal node shape rules:
    // ONLY confirmed criminals get "star"; EVERY OTHER ENTITY gets "ellipse" (circle).
    const cy = cytoscape({
      container: containerRef.current,
      elements: currentElements,
      style: [
        {
          selector: "node",
          style: {
            // Rule: ONLY confirmed criminals get star, everything else is a circle
            shape: (ele: any) => (ele.data("is_criminal") ? "star" : "ellipse"),
            "background-color": (ele: any) => {
              if (ele.data("is_criminal")) return CRIMINAL_FILL;
              if (ele.data("label") === "CASE") return "#0F172A";
              return entityColor(ele.data("label") || "PERSON");
            },
            "border-width": (ele: any) => (ele.data("is_criminal") ? 3 : 2),
            "border-color": (ele: any) => (ele.data("is_criminal") ? CRIMINAL_BORDER : "#CBD5E1"),
            label: "data(name)",
            color: "#FFFFFF",
            "font-family": "Inter, system-ui, sans-serif",
            "font-size": level === "case" ? "13px" : "11px",
            "font-weight": 600,
            "text-valign": "center",
            "text-halign": "center",
            width: level === "case" ? 56 : 38,
            height: level === "case" ? 56 : 38,
            "text-outline-color": "#0F172A",
            "text-outline-width": 1.5,
            "overlay-padding": 6,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": "#3B82F6",
            "border-width": 4,
            "overlay-color": "#3B82F6",
            "overlay-opacity": 0.25,
            "overlay-padding": 6,
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele: any) => ele.data("weight") || 2,
            "line-color": (ele: any) => {
              const strength = ele.data("strength");
              if (strength === "STRONG") return "#2563EB";
              if (strength === "MODERATE") return "#0D9488";
              return "#94A3B8";
            },
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": "10px",
            "font-weight": 500,
            color: "#475569",
            "text-rotation": "autorotate",
            "text-background-color": "#FFFFFF",
            "text-background-opacity": 0.85,
            "text-background-padding": "2px",
            "text-border-opacity": 0,
          },
        },
        {
          selector: "edge:selected",
          style: {
            "line-color": "#2563EB",
            width: 4,
            "font-weight": 700,
          },
        },
      ],
      layout: {
        name: layoutName,
        animate: true,
        animationDuration: 400,
        padding: 40,
      } as any,
      minZoom: 0.2,
      maxZoom: 3.5,
    });

    cy.on("tap", "node", (evt) => {
      const data = evt.target.data();
      if (level === "case") {
        setSelectedCase(data.raw_node as MasterCaseNode);
        setSelectedEdge(null);
      } else {
        setSelectedEntityNode(data.raw_node as GraphNodeRow);
      }
    });

    cy.on("tap", "edge", (evt) => {
      const data = evt.target.data();
      if (level === "case") {
        setSelectedEdge(data.raw_edge as MasterCaseEdge);
        setSelectedCase(null);
      }
    });

    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        setSelectedCase(null);
        setSelectedEdge(null);
        setSelectedEntityNode(null);
      }
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [level, caseElements, entityElements, layoutName]);

  const fitView = () => {
    cyRef.current?.fit(undefined, 30);
  };

  const switchToEntityView = (caseNumberOrId?: string) => {
    if (caseNumberOrId) {
      setFilterCaseId(caseNumberOrId);
    } else {
      setFilterCaseId(null);
    }
    setLevel("entity");
  };

  return (
    <section className="panel inv-master-case-network" aria-labelledby="mcn-title">
      <div className="inv-objective-head">
        <div>
          <h2 id="mcn-title">MASTER CASE NETWORK</h2>
          <p className="muted" style={{ margin: "var(--space-1) 0 0" }}>
            Cross-case relationships across the active dataset
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          {/* Three genuinely different graphs — each tab renders its own data. */}
          <div className="btn-group" role="tablist" aria-label="Master Network View Level">
            <button
              type="button"
              role="tab"
              aria-selected={level === "people"}
              className={`btn btn-small ${level === "people" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => {
                setLevel("people");
                setSelectedCase(null);
                setSelectedEdge(null);
                setSelectedEntityNode(null);
              }}
            >
              PEOPLE NETWORK
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={level === "case"}
              className={`btn btn-small ${level === "case" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => {
                setLevel("case");
                setSelectedEntityNode(null);
              }}
            >
              CASE NETWORK
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={level === "entity"}
              className={`btn btn-small ${level === "entity" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => {
                setLevel("entity");
                setSelectedCase(null);
                setSelectedEdge(null);
              }}
            >
              ENTITY NETWORK
            </button>
          </div>
          {level !== "people" && (
            <button
              type="button"
              className="btn btn-tertiary btn-small"
              onClick={() => {
                if (level === "case") void loadCaseNetwork();
                else void loadEntityNetwork();
              }}
            >
              Refresh
            </button>
          )}
        </div>
      </div>

      {/* ----------------- LEVEL 1: PEOPLE NETWORK (PERSON → PERSON) ----------------- */}
      {level === "people" && (
        <PersonRelationshipNetwork
          caseId={null}
          onOpenEntityNetwork={() => {
            setFilterCaseId(null);
            setLevel("entity");
          }}
        />
      )}

      {level !== "people" && (
      <>
      {/* Description & Legend Bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          margin: "var(--space-2) 0",
          fontSize: "var(--text-xs)",
        }}
      >
        <p className="muted" style={{ margin: 0 }}>
          {level === "case"
            ? `How ALL ${caseNetwork?.counts.cases ?? 0} cases in the active dataset connect through evidence-backed shared entities. Only real shared entities form connections.`
            : `Underlying multi-case entity graph — the deeper evidence layer: phones, accounts, vehicles, locations, organisations and the records that tie them together. Switch to PEOPLE NETWORK for person-to-person relationships.`}
        </p>

        {/* Legend */}
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <span style={{ fontSize: "14px", color: "#0F172A" }}>○</span>
            <span>{level === "case" ? "Case (Circle)" : "Entity (Circle)"}</span>
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <span style={{ fontSize: "14px", color: CRIMINAL_FILL }}>★</span>
            <span>
              Confirmed Criminal ONLY (Star)
              {level === "entity" && entityCriminalCount >= 0 ? ` — ${entityCriminalCount} in view` : ""}
            </span>
          </span>
          {level === "case" && (
            <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ height: "2px", width: "16px", background: "#94A3B8" }} />
              <span>1 shared</span>
              <span style={{ height: "3px", width: "16px", background: "#0D9488" }} />
              <span>2 shared</span>
              <span style={{ height: "5px", width: "16px", background: "#2563EB" }} />
              <span>3+ shared</span>
            </span>
          )}
        </div>
      </div>

      {/* Controls Bar */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-2)",
          alignItems: "center",
          marginBottom: "var(--space-2)",
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-xs)" }}>
          <span className="muted">Layout:</span>
          <select
            value={layoutName}
            onChange={(e) => setLayoutName(e.target.value as any)}
            style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
          >
            <option value="fcose">Force-directed (fcose)</option>
            <option value="circle">Circular</option>
            <option value="concentric">Concentric</option>
          </select>
        </label>
        <button type="button" className="btn btn-tertiary btn-small" onClick={fitView}>
          Fit view
        </button>
        {level === "entity" && filterCaseId && (
          <button
            type="button"
            className="btn btn-secondary btn-small"
            onClick={() => setFilterCaseId(null)}
          >
            Clear case filter ({filterCaseId})
          </button>
        )}
      </div>

      {/* States */}
      {loading && <Spinner label="Building master case network..." />}
      {error && <ErrorState message={error} onRetry={() => void loadCaseNetwork()} />}

      {!loading && !error && caseNetwork && caseNetwork.nodes.length === 0 && (
        <Empty message={caseNetwork.empty_reason || "No active dataset is available."} />
      )}

      {/* Graph Canvas */}
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: 520,
          background: "var(--bg-canvas, #F8FAFC)",
          border: "1px solid var(--border-color, #E2E8F0)",
          borderRadius: "var(--radius-md, 8px)",
          position: "relative",
        }}
      />

      {/* ----------------- DETAILS: CASE SELECTED ----------------- */}
      {selectedCase && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h3>
              CASE {selectedCase.case_number} — {selectedCase.title}
            </h3>
            <span className="badge badge-navy">Status: {selectedCase.status}</span>
          </div>

          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", margin: "var(--space-2) 0" }}>
            <span className="chip">
              <span className="chip-label">Documents:</span> <strong>{selectedCase.document_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Entities:</span> <strong>{selectedCase.entity_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Related cases:</span> <strong>{selectedCase.related_cases_count}</strong>
            </span>
          </div>

          {/* Connected Cases */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>CONNECTED CASES</h4>
            {selectedCase.connected_cases.length === 0 ? (
              <p className="muted">This case has no shared entities with other cases in the active dataset.</p>
            ) : (
              <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginTop: "var(--space-1)" }}>
                {selectedCase.connected_cases.map((cc) => (
                  <span key={cc} className="chip">
                    ○ <strong>{cc}</strong>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Shared / Connecting Entities */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>SHARED / CONNECTING ENTITIES</h4>
            {selectedCase.shared_entities.length === 0 ? (
              <p className="muted">No connecting entities.</p>
            ) : (
              <ul className="inv-relationship-list" style={{ marginTop: "var(--space-1)" }}>
                {selectedCase.shared_entities.map((ent) => (
                  <li key={ent.provenance_key} className="inv-relationship">
                    <div className="inv-relationship-head">
                      {ent.is_criminal ? (
                        <span style={{ color: CRIMINAL_FILL, fontSize: "16px" }}>★</span>
                      ) : (
                        <span style={{ fontSize: "16px", color: "#475569" }}>○</span>
                      )}
                      <strong>{ent.name}</strong>
                      <Badge value={ent.label} />
                      {ent.is_criminal && <Badge value="Confirmed criminal" />}
                    </div>
                    {ent.evidence && (
                      <div className="evidence-link-row" style={{ marginTop: "var(--space-1)" }}>
                        <EvidencePointerLink pointer={ent.evidence} />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div style={{ marginTop: "var(--space-3)" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => switchToEntityView(selectedCase.id)}
            >
              [OPEN ENTITY NETWORK]
            </button>
          </div>
        </div>
      )}

      {/* ----------------- DETAILS: CASE CONNECTION (EDGE) SELECTED ----------------- */}
      {selectedEdge && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h3>
              CASE CONNECTION: {selectedEdge.source_case_number} ↔ {selectedEdge.target_case_number}
            </h3>
            <Badge value={`Connection strength: ${selectedEdge.strength}`} />
          </div>

          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", margin: "var(--space-2) 0" }}>
            <span className="chip">
              <span className="chip-label">Shared entities:</span>{" "}
              <strong>{selectedEdge.shared_entity_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Evidence-backed relationships:</span>{" "}
              <strong>{selectedEdge.relationship_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Evidence count:</span>{" "}
              <strong>{selectedEdge.evidence_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Temporal overlap:</span>{" "}
              <strong>{selectedEdge.temporal_overlap}</strong>
            </span>
          </div>

          {/* Source Categories */}
          <div style={{ marginTop: "var(--space-2)" }}>
            <span className="muted" style={{ marginRight: "var(--space-2)" }}>
              Source categories:
            </span>
            {selectedEdge.source_categories.map((cat) => (
              <span key={cat} className="badge badge-navy" style={{ marginRight: "var(--space-1)" }}>
                {cat}
              </span>
            ))}
          </div>

          {/* Shared Entities List */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>SHARED ENTITIES</h4>
            <ul className="inv-relationship-list" style={{ marginTop: "var(--space-1)" }}>
              {selectedEdge.shared_entities.map((ent) => (
                <li key={ent.provenance_key} className="inv-relationship">
                  <div className="inv-relationship-head">
                    {ent.is_criminal ? (
                      <span style={{ color: CRIMINAL_FILL, fontSize: "16px" }}>★</span>
                    ) : (
                      <span style={{ fontSize: "16px", color: "#475569" }}>○</span>
                    )}
                    <strong>{ent.name}</strong>
                    <Badge value={ent.label} />
                    {ent.is_criminal && <Badge value="Confirmed criminal" />}
                  </div>
                  {ent.evidence && (
                    <div className="evidence-link-row" style={{ marginTop: "var(--space-1)" }}>
                      <EvidencePointerLink pointer={ent.evidence} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {/* WHY THIS CONNECTION EXISTS */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>WHY THIS CONNECTION EXISTS</h4>
            <p style={{ fontSize: "var(--text-sm)", lineHeight: 1.6, marginTop: "var(--space-1)" }}>
              {selectedEdge.why}
            </p>
          </div>

          {/* ANALYTICAL BASIS */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>ANALYTICAL BASIS</h4>
            <ul style={{ listStyle: "none", padding: 0, marginTop: "var(--space-1)" }}>
              {selectedEdge.analytical_basis.map((basis) => (
                <li key={basis} style={{ padding: "4px 0", display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ color: "#059669", fontWeight: 700 }}>✓</span>
                  <span>{basis}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* SUPPORTING EVIDENCE (opens in SourceViewer) */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>SUPPORTING EVIDENCE</h4>
            {selectedEdge.supporting_evidence.length === 0 ? (
              <p className="muted">No direct evidence pointers recorded.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)", marginTop: "var(--space-1)" }}>
                {selectedEdge.supporting_evidence.map((ev, idx) => (
                  <div key={`${ev.source_doc_id}-${idx}`} className="evidence-link-row">
                    <EvidencePointerLink pointer={ev.pointer} emptyMessage={ev.label} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* CONTRADICTORY EVIDENCE */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>CONTRADICTORY / QUALIFYING EVIDENCE</h4>
            <p className="muted" style={{ marginTop: "var(--space-1)" }}>
              {selectedEdge.contradictory_evidence}
            </p>
          </div>

          {/* DATA GAPS */}
          {selectedEdge.data_gaps.length > 0 && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <h4>DATA GAPS</h4>
              <ul>
                {selectedEdge.data_gaps.map((gap, i) => (
                  <li key={i}>{gap}</li>
                ))}
              </ul>
            </div>
          )}

          {/* NEXT INVESTIGATIVE DIRECTION */}
          <div style={{ marginTop: "var(--space-3)" }}>
            <h4>NEXT INVESTIGATIVE DIRECTION</h4>
            <p style={{ marginTop: "var(--space-1)", color: "#1E3A8A" }}>
              {selectedEdge.next_direction}
            </p>
          </div>

          <div style={{ marginTop: "var(--space-3)" }}>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => switchToEntityView()}
            >
              [DRILL INTO SHARED ENTITIES]
            </button>
          </div>
        </div>
      )}

      {/* ----------------- DETAILS: ENTITY NODE SELECTED (Entity Level) ----------------- */}
      {selectedEntityNode && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h3>
              {getDisplayLabel(selectedEntityNode)}
              {isConfirmedCriminal(selectedEntityNode) && <Badge value="Confirmed criminal" />}
            </h3>
            <Badge value={selectedEntityNode.label} />
          </div>
          <p className="muted">
            Criminal status is source-derived:{" "}
            {selectedEntityNode.criminal_status ? (
              <Badge value={selectedEntityNode.criminal_status} />
            ) : (
              <span className="muted">not recorded in source documents</span>
            )}
            . Graph layout or network position never establishes criminality.
          </p>
          {selectedEntityNode.evidence && (
            <div className="evidence-link-row">
              <EvidencePointerLink pointer={selectedEntityNode.evidence} />
            </div>
          )}
        </div>
      )}
      </>
      )}
    </section>
  );
}
