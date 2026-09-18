/**
 * PEOPLE NETWORK — the primary investigator graph on /investigate.
 *
 * This is a PERSON → PERSON graph.  Every node is a person; every edge is a
 * relationship between two people that the dataset actually supports.  Phones,
 * bank accounts, vehicles, addresses, organisations, call records, transfers
 * and documents are the *evidence* for an edge — the backend walks them and
 * collapses them into a single aggregated relationship that reports how many
 * records back it.  None of them is ever a node here.
 *
 * Progressive disclosure:
 *   People  →  Relationships  →  Evidence supporting those relationships
 * Selecting an edge opens the supporting records; the deeper entity graph
 * lives behind the ENTITY NETWORK tab, which is a different graph entirely.
 *
 * ★ rule: a person carries the star only when the dataset's authoritative
 * ``criminal_status`` field says so.  Degree, centrality, having a phone, a
 * transaction or a witness statement never earns it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type ElementDefinition } from "cytoscape";
import {
  relationshipEvidence,
  relationshipNetwork,
  type RelationshipEdge,
  type RelationshipNetworkResult,
  type RelationshipPersonNode,
  type RelationshipSupportingItem,
} from "../../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../Status";
import { EvidencePointerLink } from "../EvidenceLink";
import GraphViewControls from "../common/GraphViewControls";
import { useGraphCanvas } from "../../lib/useGraphCanvas";

const PERSON_FILL = "#1D4ED8";
const CRIMINAL_FILL = "#DC2626";
const CRIMINAL_BORDER = "#F59E0B";
const SELECTED_RING = "#0EA5E9";

const STRENGTH_COLOR: Record<string, string> = {
  STRONG: "#1D4ED8",
  MODERATE: "#0D9488",
  WEAK: "#94A3B8",
};

const SUPPORTING_KIND_LABEL: Record<string, string> = {
  PHONE: "Phone",
  BANK_ACCOUNT: "Bank account",
  VEHICLE: "Vehicle",
  LOCATION: "Address",
  ORGANIZATION: "Organization",
  COMMUNICATION: "Communication record",
  TRANSACTION: "Financial transaction",
  DIRECT_RECORD: "Recorded relationship",
};

interface PersonRelationshipNetworkProps {
  /** Restrict to one case; omit for the cross-case master network. */
  caseId?: string | null;
  /** Switch the host panel to the ENTITY NETWORK drill-down. */
  onOpenEntityNetwork?: () => void;
}

export default function PersonRelationshipNetwork({
  caseId,
  onOpenEntityNetwork,
}: PersonRelationshipNetworkProps) {
  const [data, setData] = useState<RelationshipNetworkResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedNode, setSelectedNode] = useState<RelationshipPersonNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<RelationshipEdge | null>(null);
  const [edgeEvidence, setEdgeEvidence] = useState<RelationshipSupportingItem[] | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  const [layoutName, setLayoutName] = useState<"fcose" | "circle" | "concentric" | "breadthfirst">(
    "fcose",
  );
  const [maxRelationships, setMaxRelationships] = useState(75);
  const [minEvidence, setMinEvidence] = useState(1);
  const [typeFilter, setTypeFilter] = useState<string>("ALL");

  const graphLabels = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedNode(null);
    setSelectedEdge(null);
    setEdgeEvidence(null);
    setEvidenceError(null);
    try {
      const result = await relationshipNetwork({
        caseId: caseId ?? undefined,
        limit: maxRelationships,
        minEvidence,
        relationshipTypes: typeFilter === "ALL" ? undefined : [typeFilter],
      });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [caseId, maxRelationships, minEvidence, typeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const nameOf = useCallback(
    (key: string): string => {
      const node = data?.nodes.find((n) => n.provenance_key === key);
      return node?.name ?? key;
    },
    [data],
  );

  // ---- Fetch the authoritative evidence behind a selected edge --------------
  const loadEdgeEvidence = useCallback(async (edge: RelationshipEdge) => {
    setEvidenceLoading(true);
    setEdgeEvidence(null);
    setEvidenceError(null);
    try {
      const detail = await relationshipEvidence(edge.source, edge.target);
      setEdgeEvidence(detail.supporting_items);
    } catch (err) {
      // The aggregated edge already carries its supporting items, so the panel
      // keeps showing them — but the failure is reported, never swallowed.
      // Silently substituting the cached list is how a stale edge starts
      // looking like a freshly verified one.
      setEdgeEvidence(edge.supporting_items);
      setEvidenceError(
        `Could not refresh the supporting records (${err instanceof Error ? err.message : String(err)}). ` +
          "Showing the records already carried by this relationship.",
      );
    } finally {
      setEvidenceLoading(false);
    }
  }, []);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!data) return [];
    const out: ElementDefinition[] = [];
    for (const node of data.nodes) {
      out.push({
        data: {
          id: node.provenance_key,
          name: node.name,
          role: node.role ?? null,
          is_criminal: Boolean(node.is_criminal),
          criminal_status: node.criminal_status ?? null,
          relationship_count: node.relationship_count,
          raw_node: node,
        },
      });
    }
    for (const edge of data.edges) {
      out.push({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          // Edge label states the relationship, not an identifier.
          label: `${edge.label}${edge.evidence_count > 1 ? ` · ${edge.evidence_count}` : ""}`,
          strength: edge.strength,
          evidence_count: edge.evidence_count,
          cross_case: edge.cross_case,
          raw_edge: edge,
        },
      });
    }
    return out;
  }, [data]);

  const style = useMemo(
    () =>
      [
        {
          selector: "node",
          style: {
            // The person graph keeps a consistent circular silhouette; confirmed
            // criminal status is communicated by the gated star label, fill and ring.
            shape: "ellipse",
            "background-color": (ele: any) =>
              ele.data("is_criminal") ? CRIMINAL_FILL : PERSON_FILL,
            "border-width": (ele: any) => (ele.data("is_criminal") ? 4 : 2),
            "border-color": (ele: any) =>
              ele.data("is_criminal") ? CRIMINAL_BORDER : "#BFDBFE",
            width: (ele: any) =>
              Math.min(64, 30 + 3 * Number(ele.data("relationship_count") || 0)),
            height: (ele: any) =>
              Math.min(64, 30 + 3 * Number(ele.data("relationship_count") || 0)),
            // Keep the person name on the node; the silhouette itself conveys criminal status.
            label: (ele: any) => {
              const name = String(ele.data("name") ?? "");
              const text = ele.data("is_criminal") ? `★\n${name}` : name;
              return ele.selected() || graphLabels.current ? text : "";
            },
            "text-wrap": "wrap",
            "text-max-width": "140px",
            "font-family": "Inter, system-ui, sans-serif",
            "font-size": "11px",
            "font-weight": 600,
            color: "#0F172A",
            "text-valign": "top",
            "text-halign": "center",
            "text-margin-y": -6,
            "text-background-color": "#FFFFFF",
            "text-background-opacity": 0.82,
            "text-background-padding": "2px",
            "overlay-padding": 8,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": SELECTED_RING,
            "border-width": 5,
            "overlay-color": SELECTED_RING,
            "overlay-opacity": 0.22,
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele: any) =>
              Math.min(9, 1.5 + 0.9 * Number(ele.data("evidence_count") || 1)),
            "line-color": (ele: any) =>
              STRENGTH_COLOR[ele.data("strength")] ?? "#94A3B8",
            "target-arrow-shape": "none",
            "curve-style": "bezier",
            opacity: 0.85,
            // Edge labels are only drawn when they can be read, or for the
            // edge under inspection — otherwise 75 of them overlap into noise.
            label: (ele: any) =>
              ele.selected() || graphLabels.current ? String(ele.data("label") ?? "") : "",
            "font-size": "9px",
            "font-weight": 600,
            color: "#334155",
            "text-rotation": "autorotate",
            "text-background-color": "#FFFFFF",
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
          },
        },
        {
          selector: "edge[?cross_case]",
          style: { "line-style": "dashed" },
        },
        {
          selector: "edge:selected",
          style: {
            "line-color": SELECTED_RING,
            width: 5,
            opacity: 1,
            "font-weight": 700,
            "z-index": 99,
          },
        },
      ] as any,
    [],
  );

  const { containerRef, handle: graphHandle } = useGraphCanvas({
    elements,
    style,
    labelsRef: graphLabels,
    layoutName,
    onTapNode: (node: any) => {
      setSelectedNode(node.data("raw_node") as RelationshipPersonNode);
      setSelectedEdge(null);
      setEdgeEvidence(null);
    },
    onTapEdge: (edge: any) => {
      const raw = edge.data("raw_edge") as RelationshipEdge;
      setSelectedEdge(raw);
      setSelectedNode(null);
      void loadEdgeEvidence(raw);
    },
    onTapBackground: () => {
      setSelectedNode(null);
      setSelectedEdge(null);
      setEdgeEvidence(null);
    },
  });

  const counts = data?.counts;
  const hasRelationships = (data?.edges.length ?? 0) > 0;
  const shownItems = edgeEvidence ?? selectedEdge?.supporting_items ?? [];

  return (
    <section className="panel inv-person-relationship-network" aria-labelledby="prn-title">
      <div className="inv-objective-head">
        <div>          <h3 id="prn-title">PEOPLE NETWORK — PERSON → PERSON</h3>
          <p className="muted" style={{ margin: "var(--space-1) 0 0" }}>
            {caseId
              ? "Who is connected to whom in this case. Supporting entities stay behind the edge."
              : "Cross-case: who is connected to whom across the active dataset."}
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-xs)" }}>
            <span className="muted">Relationships:</span>
            <select
              value={maxRelationships}
              onChange={(e) => setMaxRelationships(Number(e.target.value))}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value={25}>Top 25</option>
              <option value={75}>Top 75</option>
              <option value={150}>Top 150</option>
              <option value={400}>Top 400</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-xs)" }}>
            <span className="muted">Min. records:</span>
            <select
              value={minEvidence}
              onChange={(e) => setMinEvidence(Number(e.target.value))}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value={1}>1+</option>
              <option value={2}>2+</option>
              <option value={3}>3+</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-xs)" }}>
            <span className="muted">Type:</span>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value="ALL">All relationship types</option>
              <option value="COMMUNICATION">Communication</option>
              <option value="FINANCIAL_LINK">Financial link</option>
              <option value="SHARED_PHONE">Shared phone</option>
              <option value="SHARED_ACCOUNT">Shared bank account</option>
              <option value="SHARED_VEHICLE">Shared vehicle</option>
              <option value="SHARED_ADDRESS">Shared address</option>
              <option value="SHARED_ORGANIZATION">Shared organization</option>
              <option value="FAMILY_RELATIVE">Family / relative</option>
              <option value="KNOWN_ASSOCIATION">Known association</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", fontSize: "var(--text-xs)" }}>
            <span className="muted">Layout:</span>
            <select
              value={layoutName}
              onChange={(e) => setLayoutName(e.target.value as any)}
              style={{ fontSize: "var(--text-xs)", padding: "2px 6px" }}
            >
              <option value="fcose">Force-directed</option>
              <option value="circle">Circular</option>
              <option value="concentric">Concentric</option>
              <option value="breadthfirst">Hierarchy</option>
            </select>
          </label>
          <GraphViewControls handle={graphHandle} />
          <button type="button" className="btn btn-tertiary btn-small" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "var(--space-2)",
          margin: "var(--space-2) 0",
          fontSize: "var(--text-xs)",
        }}
      >
        <p className="muted" style={{ margin: 0 }}>
          {counts
            ? `${counts.persons} people · ${counts.relationships} relationships` +
              (counts.relationships_total > counts.relationships
                ? ` shown of ${counts.relationships_total}`
                : "") +
              ` · ${counts.supporting_items} supporting records · ${counts.confirmed_criminals} confirmed criminal${counts.confirmed_criminals === 1 ? "" : "s"}`
            : "People and the relationships the dataset actually supports."}
          {data?.truncated ? " · raise “Relationships” to see more" : ""}
        </p>

        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: PERSON_FILL,
                border: "2px solid #BFDBFE",
                display: "inline-block",
              }}
            />
            <span>Person</span>
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <span style={{ fontSize: "14px", color: CRIMINAL_FILL, lineHeight: 1 }}>★</span>
            <span
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: CRIMINAL_FILL,
                border: `2px solid ${CRIMINAL_BORDER}`,
                display: "inline-block",
              }}
            />
            <span>Confirmed criminal (★ + amber ring)</span>
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ height: "2px", width: "16px", background: "#94A3B8" }} />
            <span>1 record</span>
            <span style={{ height: "4px", width: "16px", background: "#0D9488" }} />
            <span>2–3</span>
            <span style={{ height: "6px", width: "16px", background: "#1D4ED8" }} />
            <span>4+</span>
            <span style={{ height: "2px", width: "16px", background: "#64748B", borderTop: "1px dashed #64748B" }} />
            <span>cross-case</span>
          </span>
        </div>
      </div>

      {loading && <Spinner label="Deriving person-to-person relationships..." />}
      {error && <ErrorState message={error} onRetry={() => void load()} />}

      {!loading && !error && data && !hasRelationships && (
        <div style={{ marginBottom: "var(--space-2)" }}>
          <Empty
            message={
              data.empty_reason ??
              "No verified person-to-person relationships found in the active dataset."
            }
          />
          <div style={{ display: "flex", gap: "var(--space-2)", justifyContent: "center" }}>
            {onOpenEntityNetwork && (
              <button type="button" className="btn btn-secondary" onClick={onOpenEntityNetwork}>
                View supporting entities
              </button>
            )}
            {(minEvidence > 1 || typeFilter !== "ALL") && (
              <button
                type="button"
                className="btn btn-tertiary"
                onClick={() => {
                  setMinEvidence(1);
                  setTypeFilter("ALL");
                }}
              >
                Clear relationship filters
              </button>
            )}
          </div>
        </div>
      )}

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

      {/* ---------------- SELECTED PERSON ---------------- */}
      {selectedNode && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h4>
              {selectedNode.is_criminal && (
                <span style={{ color: CRIMINAL_FILL, marginRight: 6 }}>★</span>
              )}
              {selectedNode.name}
            </h4>
            <div style={{ display: "flex", gap: "var(--space-1)" }}>
              {selectedNode.is_criminal ? (
                <Badge value={`Confirmed criminal · ${selectedNode.criminal_status ?? ""}`} />
              ) : (
                <Badge value="No confirmed criminal status" />
              )}
              {selectedNode.role && <Badge value={selectedNode.role} />}
            </div>
          </div>
          <p className="muted" style={{ fontSize: "var(--text-xs)", marginTop: "var(--space-1)" }}>
            Criminal status is source-derived from the dataset's{" "}
            <code>criminal_status</code> field. Network position, evidence volume and
            case involvement never establish criminality.
          </p>
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", margin: "var(--space-2) 0" }}>
            <span className="chip">
              <span className="chip-label">Relationships:</span>{" "}
              <strong>{selectedNode.relationship_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Supporting records:</span>{" "}
              <strong>{selectedNode.evidence_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Cases:</span>{" "}
              <strong>{selectedNode.case_ids.length}</strong>
            </span>
          </div>
          <h5>RELATIONSHIPS</h5>
          <ul className="inv-relationship-list" style={{ marginTop: "var(--space-1)" }}>
            {(data?.edges ?? [])
              .filter(
                (e) =>
                  e.source === selectedNode.provenance_key ||
                  e.target === selectedNode.provenance_key,
              )
              .slice(0, 12)
              .map((edge) => {
                const other =
                  edge.source === selectedNode.provenance_key ? edge.target : edge.source;
                return (
                  <li key={edge.id} className="inv-relationship">
                    <div className="inv-relationship-head">
                      <button
                        type="button"
                        className="btn btn-tertiary btn-small"
                        onClick={() => {
                          setSelectedEdge(edge);
                          void loadEdgeEvidence(edge);
                        }}
                      >
                        {edge.label}
                      </button>
                      <span>↔ {nameOf(other)}</span>
                      <Badge value={`${edge.evidence_count} record${edge.evidence_count === 1 ? "" : "s"}`} />
                      {edge.cross_case && <Badge value="Cross-case" />}
                    </div>
                  </li>
                );
              })}
          </ul>
        </div>
      )}

      {/* ---------------- SELECTED RELATIONSHIP ---------------- */}
      {selectedEdge && (
        <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
          <div className="inv-objective-head">
            <h4>
              {nameOf(selectedEdge.source)} ↔ {nameOf(selectedEdge.target)}
            </h4>
            <div style={{ display: "flex", gap: "var(--space-1)" }}>
              <Badge value={selectedEdge.label} />
              <Badge value={`Strength: ${selectedEdge.strength}`} />
              {selectedEdge.cross_case && <Badge value="Cross-case" />}
            </div>
          </div>

          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", margin: "var(--space-2) 0" }}>
            <span className="chip">
              <span className="chip-label">Supporting records:</span>{" "}
              <strong>{selectedEdge.supporting_item_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Relationship kinds:</span>{" "}
              <strong>{selectedEdge.relationship_types.length}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Cases:</span>{" "}
              <strong>{selectedEdge.case_ids.length}</strong>
            </span>
          </div>

          <div style={{ marginTop: "var(--space-2)" }}>
            <span className="muted" style={{ marginRight: "var(--space-2)", fontSize: "var(--text-xs)" }}>
              Established by:
            </span>
            {selectedEdge.relationship_types.map((t) => (
              <span key={t} className="badge badge-navy" style={{ marginRight: "var(--space-1)" }}>
                {t.replace(/_/g, " ").toLowerCase()}
              </span>
            ))}
          </div>

          <div style={{ marginTop: "var(--space-3)" }}>
            <h5>
              SUPPORTING EVIDENCE{" "}
              <span className="muted" style={{ fontWeight: 400, fontSize: "var(--text-xs)" }}>
                (aggregated behind this single relationship)
              </span>            </h5>
            {evidenceError && (
              <p
                role="alert"
                style={{
                  margin: "var(--space-1) 0",
                  fontSize: "var(--text-xs)",
                  color: "var(--danger, #B91C1C)",
                }}
              >
                {evidenceError}
              </p>
            )}
            {evidenceLoading ? (
              <Spinner label="Loading supporting evidence..." />
            ) : shownItems.length === 0 ? (
              <p className="muted">No supporting records recorded.</p>
            ) : (
              <ul className="inv-relationship-list" style={{ marginTop: "var(--space-1)" }}>
                {shownItems.map((item, idx) => (
                  <li key={`${item.kind}-${item.ref}-${idx}`} className="inv-relationship">
                    <div className="inv-relationship-head">
                      <Badge value={SUPPORTING_KIND_LABEL[item.kind] ?? item.kind} />
                      <strong>{item.label}</strong>
                      {item.detail && <span className="muted">· {item.detail}</span>}
                    </div>
                    <div
                      className="evidence-link-row"
                      style={{ marginTop: "var(--space-1)", display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}
                    >
                      <EvidencePointerLink
                        pointer={item.evidence}
                        emptyMessage={
                          item.source_doc_ids[0]
                            ? `Source record ${item.source_doc_ids[0]}`
                            : "No source reference recorded"
                        }
                      />
                      {item.case_ids.length > 1 && <Badge value={`${item.case_ids.length} cases`} />}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {selectedEdge.supporting_item_count > shownItems.length && (
              <p className="muted" style={{ fontSize: "var(--text-xs)" }}>
                + {selectedEdge.supporting_item_count - shownItems.length} further records behind
                this relationship.
              </p>
            )}
          </div>

          {onOpenEntityNetwork && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <button type="button" className="btn btn-secondary" onClick={onOpenEntityNetwork}>
                [OPEN ENTITY NETWORK] — see the underlying evidence graph
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}