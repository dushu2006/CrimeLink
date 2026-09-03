import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { api } from "../api/client";
import { t } from "../i18n";
import { Empty, ErrorState, Spinner } from "../components/Status";

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
  const [graph, setGraph] = useState<CaseGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ApiNode | null>(null);
  const [influence, setInfluence] = useState<Influence | null>(null);
  const [includeStaging, setIncludeStaging] = useState(false);
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [paths, setPaths] = useState<unknown[] | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);

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
  }, [graph]);

  useEffect(() => {
    if (!containerRef.current || elements.length === 0) return;
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
      setInfluence(null);
      if (node) {
        api<Influence>(`/graph/nodes/${key}/influence`)
          .then(setInfluence)
          .catch(() => setInfluence(null));
      }
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, graph]);

  if (error) return <ErrorState message={error} onRetry={load} />;

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
          <span className="muted">
            {graph ? `${graph.counts.nodes} nodes · ${graph.counts.edges} relationships` : null}
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
        {graph && !selected && (
          <div className="panel">
            <h2>{t("graph.title")}</h2>
            <p className="hint">Select a node to see its influence score and the evidence behind it.</p>
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
                          api<Evidence>(`/evidence/${doc}`)
                            .then(setEvidence)
                            .catch(() => setEvidence(null));
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
