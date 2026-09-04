import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { api } from "../api/client";
import { t } from "../i18n";
import { Empty, ErrorState, Spinner } from "../components/Status";
import {
  EvidencePointer,
  EvidencePointerLink,
} from "../components/EvidenceLink";

cytoscape.use(fcose);

interface ApiNode {
  provenance_key: string;
  label: string;
  name: string;
  confidence: number;
  staging: boolean;
  aliases: string[];
  source_doc_ids: string[];
  properties: Record<string, unknown>;
}

interface ApiEdge {
  key: string;
  source: string;
  target: string;
  rel_type: string;
  confidence: number;
  staging: boolean;
  source_doc_ids: string[];
  source_doc_id?: string | null;
  evidence?: EvidencePointer | null;
  properties: Record<string, unknown>;
}

interface CaseGraph {
  case_id: string;
  counts: { nodes: number; edges: number; by_label: Record<string, number>; by_rel_type: Record<string, number> };
  nodes: ApiNode[];
  edges: ApiEdge[];
}

interface Evidence {
  document_id: string;
  filename: string;
  document_type: string;
  content_hash: string;
  snippet: string | null;
  signed_url?: string | null;
}

interface Influence {
  provenance_key: string;
  node: string;
  betweenness: number;
  pagerank: number;
  degree: number;
  rank_in_case: number;
  rank_total: number;
  community: number | null;
  community_size: number;
  explanation: {
    summary: string;
    method: string;
    top_weighted_edges: { name: string; label?: string; rel_type?: string; confidence?: number }[];
    evidence_doc_ids: string[];
    community_members: string[];
  };
}

const LABEL_COLOR: Record<string, string> = {
  PERSON: "#1B3A6B",
  PHONE: "#0F7B6C",
  BANK_ACCOUNT: "#8A5A00",
  VEHICLE: "#6B21A8",
  LOCATION: "#A13B1C",
  EVENT: "#374151",
  ORGANIZATION: "#0B5394",
};

export default function GraphPage() {
  const { caseId = "" } = useParams();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const extraEdgesRef = useRef<ApiEdge[]>([]);
  const [graph, setGraph] = useState<CaseGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ApiNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<ApiEdge | null>(null);
  const [influence, setInfluence] = useState<Influence | null>(null);
  const [includeStaging, setIncludeStaging] = useState(false);
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [paths, setPaths] = useState<unknown[] | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [filterRelType, setFilterRelType] = useState("");
  const [expandedCount, setExpandedCount] = useState(0);

  const load = useCallback(() => {
    setError(null);
    api<CaseGraph>(`/graph/cases/${caseId}?include_staging=${includeStaging}`)
      .then(setGraph)
      .catch((err: Error) => setError(err.message));
  }, [caseId, includeStaging]);

  useEffect(load, [load]);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!graph) return [];
    const nodes: ElementDefinition[] = graph.nodes.map((node) => ({
      data: {
        id: node.provenance_key,
        name: node.name,
        label: node.label,
        confidence: node.confidence,
        staging: node.staging,
        aliases: node.aliases.join(", "),
      },
    }));
    const edges: ElementDefinition[] = graph.edges
      .filter((edge) => !filterRelType || edge.rel_type === filterRelType)
      .filter((edge) => graph.nodes.some((n) => n.provenance_key === edge.source))
      .map((edge) => ({
        data: {
          id: edge.key || `${edge.source}-${edge.rel_type}-${edge.target}`,
          source: edge.source,
          target: edge.target,
          rel: edge.rel_type,
          confidence: edge.confidence,
          staging: edge.staging,
        },
      }));
    return [...nodes, ...edges];
  }, [graph, filterRelType]);

  useEffect(() => {
    if (!containerRef.current || elements.length === 0) return;
    // The canvas is being rebuilt from the case snapshot, so any previously
    // expanded elements are gone — clear the expanded-element state too.
    extraEdgesRef.current = [];
    setExpandedCount(0);
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      layout: { name: "fcose", animate: false, nodeRepulsion: 8000, idealEdgeLength: 110 } as never,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele: cytoscape.NodeSingular) =>
              LABEL_COLOR[String(ele.data("label"))] ?? "#1B3A6B",
            label: "data(name)",
            color: "#111827",
            "font-size": 10,
            "text-valign": "bottom",
            "text-margin-y": 3,
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
            width: (ele: cytoscape.NodeSingular) => 14 + 16 * Number(ele.data("confidence") ?? 0),
            height: (ele: cytoscape.NodeSingular) => 14 + 16 * Number(ele.data("confidence") ?? 0),
            "border-width": (ele: cytoscape.NodeSingular) => (ele.data("staging") ? 2 : 0),
            "border-style": "dashed",
            "border-color": "#B45309",
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele: cytoscape.EdgeSingular) => 1 + 3 * Number(ele.data("confidence") ?? 0),
            "line-color": "#9CA3AF",
            "target-arrow-color": "#9CA3AF",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "line-style": (ele: cytoscape.EdgeSingular) => (ele.data("staging") ? "dashed" : "solid"),
            label: "data(rel)",
            "font-size": 7,
            color: "#6B7280",
            "text-rotation": "autorotate",
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
          },
        },
        {
          selector: "node:selected",
          style: { "border-width": 3, "border-color": "#1B3A6B", "border-style": "solid" },
        },
      ],
    });
    cy.on("tap", "node", (event) => {
      const key = String(event.target.id());
      const node = graph?.nodes.find((n) => n.provenance_key === key) ?? null;
      setSelected(node);
      setSelectedEdge(null);
      setInfluence(null);
      if (node) {
        api<Influence>(`/graph/nodes/${encodeURIComponent(key)}/influence`)
          .then(setInfluence)
          .catch(() => setInfluence(null));
      }
    });
    // Clicking a relationship must explain it: the relationship type, its
    // confidence, and the evidence pointer that justifies it — which opens the
    // original source record (file + row) through the audited source viewer.
    cy.on("tap", "edge", (event) => {
      const id = String(event.target.id());
      const edge =
        graph?.edges.find((e) => e.key === id || `${e.source}-${e.rel_type}-${e.target}` === id) ??
        extraEdgesRef.current.find(
          (e) => e.key === id || `${e.source}-${e.rel_type}-${e.target}` === id,
        ) ??
        null;
      setSelectedEdge(edge);
      setSelected(null);
      setInfluence(null);
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, graph]);

  const nameOf = useMemo(
    () => new Map((graph?.nodes ?? []).map((n) => [n.provenance_key, n.name])),
    [graph],
  );

  function openEvidence(docId: string, span?: number[] | null) {
    api<Evidence>(`/evidence/${docId}${span ? `?span=${span[0]},${span[1]}` : ""}`)
      .then(setEvidence)
      .catch(() => setEvidence(null));
  }

  /** Normalise a wire element ({data:{...}} or flat) into a graph edge row. */
  function edgeFromWire(item: unknown): ApiEdge | null {
    const maybe = (item as { data?: Record<string, unknown> }).data ?? (item as Record<string, unknown>);
    const source = String(maybe.source ?? maybe.source_key ?? "");
    const target = String(maybe.target ?? maybe.target_key ?? "");
    if (!source || !target) return null;
    const rel_type = String(maybe.rel_type ?? maybe.type ?? "RELATED");
    const key = String(maybe.key ?? maybe.id ?? "") || `${source}-${rel_type}-${target}`;
    const origin = (maybe.origin as EvidencePointer["origin"]) ?? null;
    const textSpan = Array.isArray(maybe.text_span)
      ? (maybe.text_span as number[]).map(Number)
      : null;
    const sourceDocId = maybe.source_doc_id ? String(maybe.source_doc_id) : null;
    return {
      key,
      source,
      target,
      rel_type,
      confidence: Number(maybe.confidence ?? 1) || 1,
      staging: Boolean(maybe.staging),
      source_doc_ids: Array.isArray(maybe.source_doc_ids)
        ? maybe.source_doc_ids.map(String)
        : [],
      source_doc_id: sourceDocId,
      evidence:
        origin || textSpan || sourceDocId
          ? { source_doc_id: sourceDocId, text_span: textSpan, origin }
          : null,
      properties: {},
    };
  }

  /** Normalise a wire element ({data:{...}} or flat) into a graph node row. */
  function nodeFromWire(item: unknown): ApiNode | null {
    const maybe = (item as { data?: Record<string, unknown> }).data ?? (item as Record<string, unknown>);
    const provenanceKey = String(maybe.provenance_key ?? maybe.id ?? maybe.key ?? "");
    if (!provenanceKey) return null;
    return {
      provenance_key: provenanceKey,
      label: String(maybe.label ?? "PERSON"),
      name: String(maybe.name ?? provenanceKey),
      confidence: Number(maybe.confidence ?? 1) || 1,
      staging: Boolean(maybe.staging),
      aliases: Array.isArray(maybe.aliases) ? maybe.aliases.map(String) : [],
      source_doc_ids: Array.isArray(maybe.source_doc_ids)
        ? maybe.source_doc_ids.map(String)
        : [],
      properties: {},
    };
  }

  // Expand one hop around a node through the audited /graph/nodes/{pk}/expand
  // endpoint and merge the new elements into the live canvas. Expanded edges
  // stay tappable and explainable just like in-case edges.
  const expandNeighbours = useCallback(async (key: string) => {
    setError(null);
    try {
      const data = await api<{ nodes: unknown[]; edges: unknown[] }>(
        `/graph/nodes/${encodeURIComponent(key)}/expand?depth=1&limit=300`,
      );
      const cy = cyRef.current;
      if (!cy) return;
      const existingNodes = new Set(cy.nodes().map((n) => n.id()));
      const existingEdges = new Set(cy.edges().map((e) => e.id()));
      const fresh: ElementDefinition[] = [];
      const freshEdges: ApiEdge[] = [];
      for (const item of data.nodes) {
        const node = nodeFromWire(item);
        if (!node || existingNodes.has(node.provenance_key)) continue;
        existingNodes.add(node.provenance_key);
        fresh.push({
          data: {
            id: node.provenance_key,
            name: node.name,
            label: node.label,
            confidence: node.confidence,
            staging: node.staging,
            aliases: node.aliases.join(", "),
          },
        });
      }
      for (const item of data.edges) {
        const edge = edgeFromWire(item);
        if (
          !edge ||
          existingEdges.has(edge.key) ||
          !existingNodes.has(edge.source) ||
          !existingNodes.has(edge.target)
        )
          continue;
        existingEdges.add(edge.key);
        freshEdges.push(edge);
        fresh.push({
          data: {
            id: edge.key,
            source: edge.source,
            target: edge.target,
            rel: edge.rel_type,
            confidence: edge.confidence,
            staging: edge.staging,
          },
        });
      }
      if (fresh.length) {
        cy.add(fresh);
        extraEdgesRef.current = [...extraEdgesRef.current, ...freshEdges];
        setExpandedCount((count) => count + fresh.length);
        cy.layout({ name: "fcose", animate: true, nodeRepulsion: 8000, idealEdgeLength: 110 } as never).run();
      }
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  if (error && !graph) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="graph-layout">
      <div className="graph-canvas">
        <div className="graph-toolbar">
          <label className="check">
            <input
              type="checkbox"
              checked={includeStaging}
              onChange={(e) => setIncludeStaging(e.target.checked)}
            />
            {t("graph.staging")}
          </label>
          {graph && (
            <select
              value={filterRelType}
              onChange={(e) => setFilterRelType(e.target.value)}
              title="Filter relationships shown on the canvas"
            >
              <option value="">All relationship types</option>
              {Object.entries(graph.counts.by_rel_type).map(([relType, count]) => (
                <option key={relType} value={relType}>
                  {relType} ({count})
                </option>
              ))}
            </select>
          )}
          <span className="muted">
            {graph
              ? `${graph.counts.nodes} nodes · ${graph.counts.edges} relationships${
                  expandedCount ? ` · +${expandedCount} expanded` : ""
                }`
              : null}
          </span>
          <button className="btn btn-small" onClick={() => cyRef.current?.layout({ name: "fcose", animate: true } as never).run()}>
            Re-layout
          </button>
        </div>
        {graph && graph.nodes.length === 0 ? (
          <Empty message="No graph entities for this case yet." />
        ) : (
          <div ref={containerRef} className="cy" />
        )}
        <p className="hint">{t("graph.legend")}</p>
      </div>

      <aside className="graph-side">
        {!graph && <Spinner />}
        {graph && !selected && !selectedEdge && (
          <div className="panel">
            <h2>{t("graph.title")}</h2>
            <p className="hint">
              Select a node to see its influence score and its evidence, or select a
              relationship to see why it exists and which source record produced it.
            </p>
            <div className="legend">
              {Object.entries(LABEL_COLOR).map(([label, color]) => (
                <div key={label} className="legend-item">
                  <span className="dot" style={{ background: color }} />
                  {label} <span className="muted">({graph.counts.by_label[label] ?? 0})</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {selected && (
          <div className="panel">
            <h2>{selected.name}</h2>
            <p className="muted">
              {selected.label} · {(selected.confidence * 100).toFixed(0)}% extraction confidence
            </p>
            {selected.aliases.length > 0 && <p className="hint">Aliases: {selected.aliases.join(", ")}</p>}

            <div className="row-actions">
              <button
                className="btn btn-small"
                onClick={() => void expandNeighbours(selected.provenance_key)}
                title="Add the immediate neighbours of this entity to the canvas"
              >
                Expand neighbours (1 hop)
              </button>
            </div>

            <h3>{t("graph.influence")}</h3>
            {!influence && <Spinner />}
            {influence && (
              <>
                <dl className="kv">
                  <dt>Betweenness</dt>
                  <dd>{influence.betweenness.toFixed(4)}</dd>
                  <dt>PageRank</dt>
                  <dd>{influence.pagerank.toFixed(4)}</dd>
                  <dt>Degree</dt>
                  <dd>{influence.degree}</dd>
                  <dt>Rank</dt>
                  <dd>
                    {influence.rank_in_case} / {influence.rank_total}
                  </dd>
                </dl>
                <h3>{t("graph.explanation")}</h3>
                <p>{influence.explanation.summary}</p>
                <ul className="compact">
                  {influence.explanation.top_weighted_edges.map((edge, index) => (
                    <li key={index}>
                      {edge.name}
                      {edge.rel_type ? ` (${edge.rel_type})` : ""}
                      {edge.confidence !== undefined ? ` · ${(edge.confidence * 100).toFixed(0)}%` : ""}
                    </li>
                  ))}
                </ul>
                <h3>{t("graph.evidence")}</h3>
                {influence.explanation.evidence_doc_ids.length === 0 && <Empty />}
                <ul className="compact">
                  {influence.explanation.evidence_doc_ids.map((doc) => (
                    <li key={doc}>
                      {/* Opening the source document is itself an audited
                          action, so it goes through the authenticated client
                          rather than a bare link. */}
                      <a
                        href="#"
                        onClick={(event) => {
                          event.preventDefault();
                          openEvidence(doc);
                        }}
                      >
                        <code>{doc.slice(0, 12)}…</code>
                      </a>
                    </li>
                  ))}
                </ul>
                {evidence && (
                  <div className="evidence">
                    <strong>{evidence.filename}</strong>{" "}
                    <span className="muted">
                      {evidence.document_type} · {evidence.content_hash.slice(0, 16)}…
                    </span>
                    {evidence.snippet ? (
                      <blockquote>{evidence.snippet}</blockquote>
                    ) : (
                      <p className="hint">No text span recorded for this pointer.</p>
                    )}
                    {evidence.signed_url && (
                      <a href={evidence.signed_url} target="_blank" rel="noreferrer">
                        Open document
                      </a>
                    )}
                  </div>
                )}
                <p className="hint">{influence.explanation.method}</p>
              </>
            )}
          </div>
        )}

        {selectedEdge && (
          <div className="panel">
            <h2>Relationship: {selectedEdge.rel_type}</h2>
            <p className="muted">
              {nameOf.get(selectedEdge.source) ?? selectedEdge.source} →{" "}
              {nameOf.get(selectedEdge.target) ?? selectedEdge.target}
            </p>
            <dl className="kv">
              <dt>Type</dt>
              <dd>{selectedEdge.rel_type}</dd>
              <dt>Confidence</dt>
              <dd>{(selectedEdge.confidence * 100).toFixed(0)}%</dd>
              <dt>Staging</dt>
              <dd>{selectedEdge.staging ? "yes (anonymous tip)" : "no"}</dd>
              <dt>Supporting documents</dt>
              <dd>{selectedEdge.source_doc_ids.length}</dd>
            </dl>

            <h3>Why this relationship exists</h3>
            <EvidencePointerLink
              pointer={selectedEdge.evidence}
              emptyMessage="This edge carries no exact source position — check the supporting documents below."
            />

            {selectedEdge.source_doc_ids.length > 0 && (
              <>
                <h3>Supporting documents</h3>
                <ul className="compact">
                  {selectedEdge.source_doc_ids.map((doc) => (
                    <li key={doc}>
                      <a
                        href="#"
                        onClick={(event) => {
                          event.preventDefault();
                          openEvidence(doc, selectedEdge.evidence?.text_span ?? null);
                        }}
                      >
                        <code>{doc.slice(0, 12)}…</code>
                      </a>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {evidence && (
              <div className="evidence">
                <strong>{evidence.filename}</strong>{" "}
                <span className="muted">
                  {evidence.document_type} · {evidence.content_hash.slice(0, 16)}…
                </span>
                {evidence.snippet ? (
                  <blockquote>{evidence.snippet}</blockquote>
                ) : (
                  <p className="hint">No text span recorded for this pointer.</p>
                )}
                {evidence.signed_url && (
                  <a href={evidence.signed_url} target="_blank" rel="noreferrer">
                    Open document
                  </a>
                )}
              </div>
            )}
          </div>
        )}

        {graph && (
          <div className="panel">
            <h2>{t("graph.paths")}</h2>
            <div className="form-row">
              <select value={pathFrom} onChange={(e) => setPathFrom(e.target.value)}>
                <option value="">From…</option>
                {graph.nodes.map((node) => (
                  <option key={node.provenance_key} value={node.provenance_key}>
                    {node.name}
                  </option>
                ))}
              </select>
              <select value={pathTo} onChange={(e) => setPathTo(e.target.value)}>
                <option value="">To…</option>
                {graph.nodes.map((node) => (
                  <option key={node.provenance_key} value={node.provenance_key}>
                    {node.name}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="btn btn-primary"
              disabled={!pathFrom || !pathTo}
              onClick={() =>
                api<{ paths: unknown[] }>(`/graph/cases/${caseId}/paths`, {
                  method: "POST",
                  body: JSON.stringify({ source_key: pathFrom, target_key: pathTo }),
                })
                  .then((data) => setPaths(data.paths))
                  .catch(() => setPaths([]))
              }
            >
              Search
            </button>
            {paths && paths.length === 0 && <Empty message="No chronologically coherent path." />}
            {paths && paths.length > 0 && (
              <pre className="code-block">{JSON.stringify(paths, null, 2).slice(0, 4000)}</pre>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
