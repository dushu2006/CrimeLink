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
import { type ElementDefinition } from "cytoscape";
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
import { GraphSourceChips } from "../graph/GraphSourceChips";
import { TechnicalDetails } from "../TechnicalDetails";
import PersonRelationshipNetwork from "./PersonRelationshipNetwork";
import GraphViewControls from "../common/GraphViewControls";
import { useGraphCanvas } from "../../lib/useGraphCanvas";
import { isConfirmedCriminal, nodeShapeRule, getDisplayLabel } from "../../lib/displayLabels";
import { typeSpecificRows, edgeSpecificRows, relLabel } from "../../lib/investigation";

/**
 * Entity types the ENTITY NETWORK can show.  A supporting entity (phone,
 * account, vehicle, address, organisation, event) is *evidence* for a
 * relationship between people; the ENTITY NETWORK is the one view where it is
 * allowed to appear as a node, and even there it is behind a filter so the
 * graph stays readable.
 */
const ENTITY_LABELS = [
  "PERSON",
  "PHONE",
  "BANK_ACCOUNT",
  "VEHICLE",
  "LOCATION",
  "ORGANIZATION",
  "EVENT",
] as const;

const ENTITY_REL_TYPES = [
  "ASSOCIATE_OF",
  "RELATIVE_OF",
  "USES_PHONE",
  "OWNS_ACCOUNT",
  "OWNS_VEHICLE",
  "LOCATED_AT",
  "MEMBER_OF",
  "CALLED",
  "TRANSFER_TO",
  "PARTICIPATED_IN",
] as const;

/**
 * Default node budget for the ENTITY NETWORK.  The full active-dataset graph is
 * 500+ nodes and 2500+ edges; rendering all of it at once is an unreadable
 * mesh, not an investigation aid.  The complete graph stays reachable through
 * the "Max nodes" control — it is an intentional mode, not the default.
 */
const DEFAULT_ENTITY_NODE_BUDGET = 80;

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
  FINANCIAL_TRANSACTION: "#0F766E",
  COMMUNICATION: "#16A34A",
  SOCIAL_ACCOUNT: "#0891B2",
  SURVEILLANCE: "#9333EA",
  INTELLIGENCE: "#DB2777",
  EVIDENCE: "#64748B",
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
  const [layoutName, setLayoutName] = useState<"fcose" | "circle" | "concentric" | "breadthfirst">("fcose");

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
  // An entity-level relationship selected on the canvas — the WHY behind the
  // edge and the records that support it.
  const [selectedEntityEdge, setSelectedEntityEdge] = useState<GraphEdgeRow | null>(null);
  const [filterCaseId, setFilterCaseId] = useState<string | null>(null);

  // ENTITY NETWORK progressive disclosure.  People first, supporting entities
  // only on request, and a bounded node count — the full graph stays available
  // but is an explicit choice rather than the first thing that renders.
  const [entityLabels, setEntityLabels] = useState<string[]>([...ENTITY_LABELS]);
  const [entityRelTypes, setEntityRelTypes] = useState<string[]>([]);
  const [entityBudget, setEntityBudget] = useState<number>(DEFAULT_ENTITY_NODE_BUDGET);
  const [entityTotal, setEntityTotal] = useState<{ nodes: number; edges: number } | null>(null);

  // Track which entity types actually exist in the graph data
  const [availableEntityLabels, setAvailableEntityLabels] = useState<Set<string>>(new Set(ENTITY_LABELS));

  const showSupporting = entityLabels.some((label) => label !== "PERSON");

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
      // First fetch with ALL labels to discover what exists (only once initially)
      const data = await masterGraph({
        labels: entityLabels.length ? entityLabels : undefined,
        relTypes: entityRelTypes.length ? entityRelTypes : undefined,
        limit: entityBudget,
      });
      setEntityTotal({
        nodes: data.counts?.by_label
          ? Object.values(data.counts.by_label).reduce((a, b) => a + b, 0)
          : data.nodes.length,
        edges: data.edges.length,
      });
      setEntityNodes(data.nodes);
      setEntityEdges(data.edges);

      // Derive available entity types from the returned data
      if (data.counts?.by_label) {
        setAvailableEntityLabels(new Set(
          Object.keys(data.counts.by_label).map(l => l.toUpperCase().replace(/\s+/g, "_"))
        ));
      } else {
        const labelsInData = new Set(
          data.nodes.map(n => String(n.label).toUpperCase().replace(/\s+/g, "_"))
        );
        setAvailableEntityLabels(labelsInData);
      }
    } catch (err) {
      setEntityError(err instanceof Error ? err.message : String(err));
    } finally {
      setEntityLoading(false);
    }
  }, [entityLabels, entityRelTypes, entityBudget]);

  // Auto-refetch whenever the entity tab is active and filters/budget change
  useEffect(() => {
    if (level === "entity") {
      void loadEntityNetwork();
    }
  }, [level, loadEntityNetwork]);

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

  // ---- Cytoscape canvas: one instance for the CASE and ENTITY tabs ----------
  // The PEOPLE NETWORK owns its own canvas inside <PersonRelationshipNetwork />.
  const graphLabels = useRef(true);

  // Node count drives dynamic sizing: smaller nodes + more spacing for large graphs
  const nodeCount = level === "case"
    ? (caseNetwork?.nodes.length ?? 0)
    : visibleEntityNodes.length;

  const canvasStyle = useMemo(
    () =>
      [
        {
          selector: "node",
          style: {
            // Universal shape rule: ONLY confirmed criminals are a star; every
            // other entity is a circle.  Degree, centrality or having a phone
            // never earns the star — only the dataset's criminal_status does.
            shape: (ele: any) => (ele.data("is_criminal") ? "star" : "ellipse"),
            "background-color": (ele: any) => {
              if (ele.data("is_criminal")) return CRIMINAL_FILL;
              if (ele.data("label") === "CASE") return "#0F172A";
              return entityColor(ele.data("label") || "PERSON");
            },
            "border-width": (ele: any) => {
              const base = ele.data("is_criminal") ? 3 : 2;
              return nodeCount > 400 ? Math.max(1, base - 1.5) : nodeCount > 200 ? Math.max(1, base - 1) : base;
            },
            "border-color": (ele: any) => (ele.data("is_criminal") ? CRIMINAL_BORDER : "#CBD5E1"),
            // Labels disappear when the zoom is too low to read them; the
            // selected node always keeps its label.
            label: (ele: any) => {
              if (!ele.selected() && !graphLabels.current) return "";
              const raw = String(ele.data("name") ?? "");
              if (nodeCount > 300 && raw.length > 18) {
                return raw.slice(0, 16) + "…";
              }
              return raw;
            },
            color: "#FFFFFF",
            "font-family": "Inter, system-ui, sans-serif",
            "font-size": level === "case" ? (nodeCount > 40 ? "11px" : "12.5px") : nodeCount > 400 ? "7px" : nodeCount > 200 ? "8.5px" : "11px",
            "font-weight": 600,
            "text-valign": "center",
            "text-halign": "center",
            width: level === "case" ? (nodeCount > 40 ? 44 : 52) : (ele: any) => ele.data("is_criminal") ? (nodeCount > 400 ? 20 : nodeCount > 200 ? 26 : 42) : (nodeCount > 400 ? 15 : nodeCount > 200 ? 22 : 36),
            height: level === "case" ? (nodeCount > 40 ? 44 : 52) : (ele: any) => ele.data("is_criminal") ? (nodeCount > 400 ? 20 : nodeCount > 200 ? 26 : 42) : (nodeCount > 400 ? 15 : nodeCount > 200 ? 22 : 36),
            "text-outline-color": "#0F172A",
            "text-outline-width": nodeCount > 400 ? 0.6 : nodeCount > 200 ? 0.9 : 1.5,
            "overlay-padding": nodeCount > 400 ? 2 : 5,
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
            width: (ele: any) => {
              const base = ele.data("weight") || 2;
              return nodeCount > 400 ? Math.max(1, base * 0.7) : base;
            },
            "line-color": (ele: any) => {
              const strength = ele.data("strength");
              if (strength === "STRONG") return "#2563EB";
              if (strength === "MODERATE") return "#0D9488";
              return "#94A3B8";
            },
            "curve-style": "bezier",
            opacity: nodeCount > 400 ? 0.75 : 0.85,
            // In large graphs (600/3000), don't clutter the canvas with thousands of edge labels
            // unless the edge is selected or the user is zoomed in close (zoom >= 1.0)
            label: (ele: any) => {
              if (ele.selected()) return String(ele.data("label") ?? "");
              if (nodeCount > 150) {
                if (!graphLabels.current) return "";
                const cyInstance = ele.cy();
                if (cyInstance && cyInstance.zoom() < 1.0) return "";
              } else {
                if (!graphLabels.current) return "";
              }
              return String(ele.data("label") ?? "");
            },
            "font-size": nodeCount > 400 ? "6.5px" : nodeCount > 200 ? "7.5px" : "9.5px",
            "font-weight": 500,
            color: "#475569",
            "text-rotation": "autorotate",
            "text-background-color": "#FFFFFF",
            "text-background-opacity": nodeCount > 200 ? 0.7 : 0.85,
            "text-background-padding": "1px",
          },
        },
        {
          selector: "edge:selected",
          style: {
            "line-color": "#2563EB",
            width: 4,
            "font-weight": 700,
            "font-size": "10px",
            "text-background-opacity": 0.95,
          },
        },
      ] as any,
    [level, nodeCount],
  );

  const {
    containerRef,
    handle: graphHandle,
  } = useGraphCanvas({
    elements: level === "case" ? caseElements : entityElements,
    style: canvasStyle,
    labelsRef: graphLabels,
    layoutName,
    enabled: level !== "people",
    onTapNode: (node: any) => {
      const data = node.data();
      if (level === "case") {
        setSelectedCase(data.raw_node as MasterCaseNode);
        setSelectedEdge(null);
      } else {
        setSelectedEntityNode(data.raw_node as GraphNodeRow);
        setSelectedEntityEdge(null);
      }
    },
    onTapEdge: (edge: any) => {
      const data = edge.data();
      if (level === "case") {
        setSelectedEdge(data.raw_edge as MasterCaseEdge);
        setSelectedCase(null);
      } else {
        // Entity-level relationship: surface its provenance like every other
        // graph edge — type, records and the documents that created it.
        setSelectedEntityEdge(data.raw_edge as GraphEdgeRow);
        setSelectedEntityNode(null);
      }
    },
    onTapBackground: () => {
      setSelectedCase(null);
      setSelectedEdge(null);
      setSelectedEntityNode(null);
      setSelectedEntityEdge(null);
    },
  });
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
                setSelectedEntityEdge(null);
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
                setSelectedEntityEdge(null);
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
                setSelectedEntityEdge(null);
              }}
            >
              ENTITY NETWORK
            </button>
          </div>
          {level === "case" && (
            <button
              type="button"
              className="btn btn-tertiary btn-small"
              onClick={() => void loadCaseNetwork()}
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
            <option value="breadthfirst">Hierarchical (breadth-first)</option>
          </select>
        </label>
        <GraphViewControls handle={graphHandle} />
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

      {/* ENTITY NETWORK progressive disclosure: the full graph is a mode, not
          the default.  People first, supporting entities on request. */}
      {level === "entity" && (
        <div
          style={{
            display: "flex",
            gap: "var(--space-2)",
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: "var(--space-2)",
            fontSize: "var(--text-xs)",
          }}
        >
          <span className="muted">Node types:</span>
          {ENTITY_LABELS.filter((label) => availableEntityLabels.has(label)).map((label) => {
            const on = entityLabels.includes(label);
            return (
              <button
                key={label}
                type="button"
                className={`btn btn-small ${on ? "btn-primary" : "btn-tertiary"}`}
                onClick={() =>
                  setEntityLabels((prev) => {
                    const next = on ? prev.filter((l) => l !== label) : [...prev, label];
                    // An empty graph is not a useful filter state.
                    return next.length ? next : [...ENTITY_LABELS];
                  })
                }
              >
                {label.replace(/_/g, " ").toLowerCase()}
              </button>
            );
          })}
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
            <span className="muted">Max nodes:</span>
            <select
              value={entityBudget}
              onChange={(e) => {
                setEntityBudget(Number(e.target.value));
              }}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value={40}>40</option>
              <option value={80}>80</option>
              <option value={200}>200</option>
              <option value={600}>600 (full graph)</option>
              <option value={3000}>Everything</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
            <span className="muted">Relationships:</span>
            <select
              value={entityRelTypes.join(",") || "ALL"}
              onChange={(e) => {
                setEntityRelTypes(e.target.value === "ALL" ? [] : [e.target.value]);
              }}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value="ALL">All relationship types</option>
              {ENTITY_REL_TYPES.map((rt) => (
                <option key={rt} value={rt}>
                  {rt.replace(/_/g, " ").toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <span className="muted">
            {entityNodes.length} nodes · {entityEdges.length} edges in view
            {entityTotal && entityTotal.nodes > entityNodes.length
              ? ` · raise “Max nodes” for the remaining ${entityTotal.nodes - entityNodes.length}`
              : ""}
            {!showSupporting ? " · people only — supporting entities are hidden" : ""}
          </span>
        </div>
      )}

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
          height: nodeCount > 400 ? 780 : nodeCount > 200 ? 660 : 540,
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
                    {ent.is_criminal ? (                      <span style={{ color: CRIMINAL_FILL, fontSize: "16px" }}>★</span>                    ) : (
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
          {Boolean((selectedEntityNode.properties as Record<string, unknown> | undefined)?.description) && (
            <p style={{ fontSize: "var(--text-sm)", marginTop: "var(--space-1)" }}>
              {String(
                (selectedEntityNode.properties as Record<string, unknown>).description,
              ).trim()}
            </p>
          )}
          {(() => {
            const rows = typeSpecificRows(selectedEntityNode);
            if (rows.length === 0) return null;
            return (
              <dl className="detail-rows" style={{ marginTop: "var(--space-2)" }}>
                {rows.map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            );
          })()}
          <div className="graph-sources" style={{ marginTop: "var(--space-2)" }}>
            <GraphSourceChips
              docIds={selectedEntityNode.source_doc_ids}
              emptyMessage="No source documents recorded for this entity."
            />
          </div>
          {selectedEntityNode.evidence && (
            <div className="evidence-link-row" style={{ marginTop: "var(--space-1)" }}>
              <EvidencePointerLink pointer={selectedEntityNode.evidence} />
            </div>
          )}
        </div>
      )}

      {/* -------------- DETAILS: ENTITY RELATIONSHIP SELECTED ------------- */}
      {selectedEntityEdge && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h3>
              {(visibleEntityNodes.find((n) => n.provenance_key === selectedEntityEdge.source)?.name ||
                selectedEntityEdge.source)}{" "}
              <span className="muted">—</span> {relLabel(selectedEntityEdge.rel_type)} →{" "}
              {(visibleEntityNodes.find((n) => n.provenance_key === selectedEntityEdge.target)?.name ||
                selectedEntityEdge.target)}
            </h3>
            <Badge value={selectedEntityEdge.rel_type.replace(/_/g, " ")} />
          </div>
          <p className="muted" style={{ fontSize: "var(--text-xs)" }}>
            This relationship exists because the case records below document it — never because
            the entities happened to be placed near each other on the canvas.
          </p>
          {(() => {
            const rows = edgeSpecificRows(selectedEntityEdge);
            if (rows.length === 0) return null;
            return (
              <dl className="detail-rows" style={{ marginTop: "var(--space-2)" }}>
                {rows.map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            );
          })()}
          <div className="graph-sources" style={{ marginTop: "var(--space-2)" }}>
            <GraphSourceChips
              docIds={[
                ...(selectedEntityEdge.source_doc_ids ?? []),
                selectedEntityEdge.source_doc_id,
              ]}
              emptyMessage="No source documents recorded for this relationship."
            />
          </div>
          {selectedEntityEdge.evidence && (
            <div className="evidence-link-row" style={{ marginTop: "var(--space-1)" }}>
              <EvidencePointerLink pointer={selectedEntityEdge.evidence} />
            </div>
          )}
        </div>
      )}
      </>
      )}
    </section>
  );
}