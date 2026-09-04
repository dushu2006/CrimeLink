/**
 * Person-centric investigation graph.
 *
 * The graph page answers ONE question: how is a person connected to the rest
 * of the case.  It never dumps the whole case dataset — the investigator
 * picks a person (the target), the page fetches that person's typed
 * neighbourhood (1–3 hops) from the backend, and renders the target
 * dominant with a per-type visual hierarchy.  Selecting any entity shows a
 * type-specific detail panel with the evidence that justifies it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  api,
  casePersons,
  investigationState,
  personFindings,
  personNetwork,
  type Finding,
  type GraphEdgeRow,
  type GraphNodeRow,
  type PersonNetwork,
  type PersonTarget,
} from "../api/client";
import { t } from "../i18n";
import {
  edgeSpecificRows,
  relLabel,
  typeSpecificRows,
} from "../lib/investigation";
import { Empty, ErrorState, Spinner } from "../components/Status";
import { EvidencePointerLink } from "../components/EvidenceLink";

cytoscape.use(fcose);

/** Centrality explanation served by /graph/nodes/{key}/influence. */
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

/** Per-type visual hierarchy: PERSON is the primary object of the domain. */
const LABEL_SIZE: Record<string, number> = {
  PERSON: 46,
  PHONE: 30,
  BANK_ACCOUNT: 30,
  VEHICLE: 26,
  LOCATION: 24,
  ORGANIZATION: 24,
  EVENT: 22,
  CASE: 20,
};

const LABEL_COLOR: Record<string, string> = {
  PERSON: "#1B3A6B",
  PHONE: "#0F7B6C",
  BANK_ACCOUNT: "#8A5A00",
  VEHICLE: "#6B21A8",
  LOCATION: "#A13B1C",
  EVENT: "#374151",
  ORGANIZATION: "#0B5394",
  CASE: "#111827",
};

export default function GraphPage() {
  const { caseId = "" } = useParams();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  const [persons, setPersons] = useState<PersonTarget[] | null>(null);
  const [targetKey, setTargetKey] = useState<string>("");
  const [depth, setDepth] = useState<1 | 2 | 3>(1);
  const [network, setNetwork] = useState<PersonNetwork | null>(null);
  const [personFindingItems, setPersonFindingItems] = useState<Finding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNodeRow | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeRow | null>(null);
  const [graphBackend, setGraphBackend] = useState<string | null>(null);
  const [influence, setInfluence] = useState<Influence | null>(null);
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [paths, setPaths] = useState<unknown[] | null>(null);

  // The graph backend actually in use, reported honestly (neo4j | embedded).
  useEffect(() => {
    investigationState(caseId)
      .then((state) => setGraphBackend(String(state.graph_backend)))
      .catch(() => setGraphBackend(null));
  }, [caseId]);

  // The person rail: who can be investigated in this case.
  useEffect(() => {
    setPersons(null);
    setTargetKey("");
    setNetwork(null);
    casePersons(caseId)
      .then((res) => setPersons(res.items))
      .catch((err: Error) => setError(err.message));
  }, [caseId]);

  const loadNetwork = useCallback(
    (key: string, hop: 1 | 2 | 3) => {
      if (!key) return;
      setError(null);
      personNetwork(caseId, key, hop)
        .then((net) => {
          setNetwork(net);
          setSelected(net.target);
          setSelectedEdge(null);
          setInfluence(null);
          setPaths(null);
          setPathFrom("");
          setPathTo("");
          // The target's centrality explanation loads with the network —
          // the investigator should not have to tap the canvas for it.
          api<Influence>(`/graph/nodes/${encodeURIComponent(key)}/influence`)
            .then(setInfluence)
            .catch(() => setInfluence(null));
          return personFindings(caseId, key).then((res) =>
            setPersonFindingItems(res.items),
          );
        })
        .catch((err: Error) => setError(err.message));
    },
    [caseId],
  );

  useEffect(() => {
    if (targetKey) loadNetwork(targetKey, depth);
  }, [targetKey, depth, loadNetwork]);

  // ----- Cytoscape: rebuild on every network change ------------------------
  const elements = useMemo<ElementDefinition[]>(() => {
    if (!network) return [];
    const nodes: ElementDefinition[] = network.nodes.map((node) => ({
      data: {
        id: node.provenance_key,
        name: node.name,
        label: node.label,
        confidence: node.confidence,
        is_target: node.provenance_key === network.target.provenance_key,
        hop:
          node.provenance_key === network.target.provenance_key
            ? 0
            : undefined,
      },
    }));
    const nameOf = (key: string) =>
      network.nodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 8);
    const edges: ElementDefinition[] = network.edges.map((edge) => ({
      data: {
        id: edge.key || `${edge.source}-${edge.rel_type}-${edge.target}`,
        source: edge.source,
        target: edge.target,
        rel: relLabel(edge.rel_type),
        raw_rel: edge.rel_type,
        confidence: edge.confidence,
        title: `${nameOf(edge.source)} —${relLabel(edge.rel_type)}→ ${nameOf(edge.target)}`,
      },
    }));
    return [...nodes, ...edges];
  }, [network]);

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
      } as never,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele: cytoscape.NodeSingular) =>
              LABEL_COLOR[String(ele.data("label"))] ?? "#1B3A6B",
            label: (ele: cytoscape.NodeSingular) => {
              // Zoom-safe: the name is truncated so dense 3-hop views stay
              // readable; the full name lives in the detail panel.
              const name = String(ele.data("name") ?? "");
              return name.length > 22 ? `${name.slice(0, 21)}…` : name;
            },
            color: "#111827",
            "font-size": 11,
            "font-weight": (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") ? 700 : 500,
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
            width: (ele: cytoscape.NodeSingular) =>
              ele.data("is_target")
                ? LABEL_SIZE.PERSON + 18
                : (LABEL_SIZE[String(ele.data("label"))] ?? 22) *
                  (0.75 + 0.25 * Number(ele.data("confidence") ?? 1)),
            height: (ele: cytoscape.NodeSingular) =>
              ele.data("is_target")
                ? LABEL_SIZE.PERSON + 18
                : (LABEL_SIZE[String(ele.data("label"))] ?? 22) *
                  (0.75 + 0.25 * Number(ele.data("confidence") ?? 1)),
            "border-width": (ele: cytoscape.NodeSingular) => (ele.data("is_target") ? 4 : ele.data("staging") ? 2 : 0),
            "border-style": "solid",
            "border-color": (ele: cytoscape.NodeSingular) => (ele.data("is_target") ? "#B45309" : "#B45309"),
            "overlay-padding": 4,
          },
        },
        {
          selector: "edge",
          style: {
            width: (ele: cytoscape.NodeSingular) => 1 + 3 * Number(ele.data("confidence") ?? 1),
            "line-color": (ele: cytoscape.EdgeSingular) =>
              ele.data("raw_rel") === "TRANSFER_TO" ? "#8A5A00" : "#9CA3AF",
            "target-arrow-color": (ele: cytoscape.EdgeSingular) =>
              ele.data("raw_rel") === "TRANSFER_TO" ? "#8A5A00" : "#9CA3AF",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "line-style": (ele: cytoscape.EdgeSingular) => (ele.data("staging") ? "dashed" : "solid"),
            label: "data(rel)",
            "font-size": 8,
            color: "#6B7280",
            "text-rotation": "autorotate",
            "text-outline-width": 2,
            "text-outline-color": "#FFFFFF",
          },
        },
        {
          selector: "node:selected",
          style: { "border-width": 4, "border-color": "#1B3A6B" },
        },
      ],
    });
    cy.on("tap", "node", (event) => {
      const key = String(event.target.id());
      const node = network?.nodes.find((n) => n.provenance_key === key) ?? null;
      setSelected(node);
      setSelectedEdge(null);
      setInfluence(null);
      if (node) {
        // The centrality explanation is a real, audited analytics endpoint.
        // If it cannot answer, the section stays hidden — no invented score.
        api<Influence>(`/graph/nodes/${encodeURIComponent(key)}/influence`)
          .then(setInfluence)
          .catch(() => setInfluence(null));
      }
    });
    cy.on("tap", "edge", (event) => {
      const id = String(event.target.id());
      const edge =
        network?.edges.find(
          (e) => (e.key || `${e.source}-${e.rel_type}-${e.target}`) === id,
        ) ?? null;
      setSelectedEdge(edge);
      setSelected(null);
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, network]);

  const nameOf = useCallback(
    (key: string) =>
      network?.nodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 10),
    [network],
  );

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t("graph.title")}</h1>
          <p className="muted">{t("graph.personCentricHint")}</p>
        </div>
        {network && (
          <div className="graph-meta">
            <span className="badge">
              {network.counts.nodes} nodes · {network.counts.edges} relations
            </span>
            <span className="badge">{t("graph.backend")}: {graphBackend ?? "…"}</span>
          </div>
        )}
      </header>

      {error && <ErrorState message={error} />}

      <div className="graph-workspace">
        {/* ---- target rail ---- */}
        <aside className="graph-rail">
          <h2>{t("graph.targets")}</h2>
          {persons === null && <Spinner />}
          {persons !== null && persons.length === 0 && (
            <Empty message={t("graph.noPersons")} />
          )}
          {persons?.map((person) => (
            <button
              key={person.provenance_key}
              type="button"
              className={`rail-item ${person.provenance_key === targetKey ? "active" : ""}`}
              onClick={() => setTargetKey(person.provenance_key)}
            >
              <span className="rail-name">{person.name}</span>
              <span className="muted">
                {person.connections} {t("graph.connections")}
                {person.aliases.length > 0 && ` · ${t("graph.aka")} ${person.aliases[0]}`}
              </span>
            </button>
          ))}
        </aside>

        {/* ---- canvas + depth ---- */}
        <section className="graph-main">
          {network && (
            <div className="graph-controls">
              <span className="muted">{t("graph.target")}:</span>
              <strong>{network.target.name}</strong>
              <div className="depth-buttons" role="group" aria-label={t("graph.depth")}>
                {([1, 2, 3] as const).map((hop) => (
                  <button
                    key={hop}
                    type="button"
                    className={`btn btn-sm ${depth === hop ? "btn-primary" : ""}`}
                    disabled={hop > 1 && network.layers[String(hop - 1)] === 0}
                    onClick={() => setDepth(hop)}
                  >
                    {hop}-hop
                  </button>
                ))}
              </div>
              {network.truncated && (
                <span className="badge badge-warn">{t("graph.truncated")}</span>
              )}
            </div>
          )}
          {network && (
            <details className="paths-panel">
              <summary>{t("graph.paths")}</summary>
              <div className="form-row">
                <select
                  value={pathFrom}
                  onChange={(e) => setPathFrom(e.target.value)}
                  aria-label="From"
                >
                  <option value="">From…</option>
                  {network.nodes.map((node) => (
                    <option key={node.provenance_key} value={node.provenance_key}>
                      {node.name} ({node.label})
                    </option>
                  ))}
                </select>
                <select
                  value={pathTo}
                  onChange={(e) => setPathTo(e.target.value)}
                  aria-label="To"
                >
                  <option value="">To…</option>
                  {network.nodes.map((node) => (
                    <option key={node.provenance_key} value={node.provenance_key}>
                      {node.name} ({node.label})
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={!pathFrom || !pathTo || pathFrom === pathTo}
                  onClick={() =>
                    api<{ paths: unknown[] }>(`/graph/cases/${caseId}/paths`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ source_key: pathFrom, target_key: pathTo }),
                    })
                      .then((data) => setPaths(data.paths))
                      .catch(() => setPaths([]))
                  }
                >
                  Search
                </button>
              </div>
              {paths && paths.length === 0 && (
                <p className="muted">No chronologically coherent path.</p>
              )}
              {paths && paths.length > 0 && (
                <pre className="code-block">
                  {JSON.stringify(paths, null, 2).slice(0, 4000)}
                </pre>
              )}
            </details>
          )}
          <div className="graph-canvas-wrap">
            {!network && persons !== null && persons.length > 0 && (
              <Empty message={t("graph.pickTarget")} />
            )}
            <div ref={containerRef} className="graph-canvas" />
          </div>
        </section>

        {/* ---- detail panels ---- */}
        <aside className="graph-detail">
          {selected && (
            <div className="detail-panel">
              <h3>
                {selected.name}
                {selected.provenance_key === network?.target.provenance_key && (
                  <span className="badge">{t("graph.targetBadge")}</span>
                )}
              </h3>
              <p className="muted">
                {t(`entity.${selected.label}`)} · {t("graph.confidence")}{" "}
                {Math.round(selected.confidence * 100)}%
              </p>
              {selected.aliases.length > 0 && (
                <p>
                  {t("graph.aka")}: {selected.aliases.join(", ")}
                </p>
              )}
              <dl className="detail-rows">
                {typeSpecificRows(selected).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
              {influence && (
                <div className="influence">
                  <h4>{t("graph.influence")}</h4>
                  <dl className="detail-rows">
                    <div>
                      <dt>betweenness</dt>
                      <dd>{influence.betweenness.toFixed(4)}</dd>
                    </div>
                    <div>
                      <dt>pagerank</dt>
                      <dd>{influence.pagerank.toFixed(4)}</dd>
                    </div>
                    <div>
                      <dt>degree</dt>
                      <dd>{influence.degree}</dd>
                    </div>
                    <div>
                      <dt>rank</dt>
                      <dd>
                        {influence.rank_in_case} / {influence.rank_total}
                      </dd>
                    </div>
                  </dl>
                  <p className="muted">{influence.explanation.summary}</p>
                </div>
              )}
              {selected.provenance_key !== network?.target.provenance_key &&
                selected.label === "PERSON" && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setTargetKey(selected.provenance_key)}
                  >
                    {t("graph.setFocus")}
                  </button>
                )}
              <p>
                <EvidencePointerLink pointer={selected.evidence} />
              </p>
              {selected.label === "PERSON" && personFindingItems.length > 0 && (
                <div className="person-findings">
                  <h4>{t("graph.findingsAbout")}</h4>
                  <ul>
                    {personFindingItems.map((f) => (
                      <li key={f.id}>
                        <strong>{f.title}</strong> · {f.confidence_band}
                        <br />
                        <span className="muted">{f.narrative}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          {selectedEdge && (
            <div className="detail-panel">
              <h3>{t("graph.relation")}</h3>
              <p>
                <strong>{nameOf(selectedEdge.source)}</strong> —{" "}
                {relLabel(selectedEdge.rel_type)} →{" "}
                <strong>{nameOf(selectedEdge.target)}</strong>
              </p>
              <p className="muted">
                {t("graph.confidence")} {Math.round(selectedEdge.confidence * 100)}%
              </p>
              <dl className="detail-rows">
                {edgeSpecificRows(selectedEdge).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
              <p>
                <EvidencePointerLink pointer={selectedEdge.evidence} />
              </p>
            </div>
          )}
          {!selected && !selectedEdge && (
            <Empty message={t("graph.selectHint")} />
          )}
        </aside>
      </div>
    </div>
  );
}
