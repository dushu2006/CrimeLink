/**
 * The case network page — three views over ONE canonical dataset.
 *
 *   * Person Graph   — a selected target + its typed neighbourhood (1–3 hops).
 *   * Master Graph   — the complete, evidence-backed network of the case.
 *   * Temporal Graph — a time-constrained visual subgraph with a timeline.
 *
 * Temporal *path* search (chronologically ordered paths between two entities)
 * is a separate operation and remains available inside the Temporal view; its
 * serialised output is supporting evidence, never the primary graph.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  api,
  caseGraph,
  casePersons,
  investigationState,
  personFindings,
  personNetwork,
  temporalGraph,
  type CaseGraph,
  type Finding,
  type GraphEdgeRow,
  type GraphNodeRow,
  type PersonNetwork,
  type PersonTarget,
  type TemporalEvent,
  type TemporalGraph,
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

type GraphMode = "person" | "master" | "temporal";

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

const FILTERABLE_LABELS = [
  "PERSON",
  "PHONE",
  "VEHICLE",
  "BANK_ACCOUNT",
  "LOCATION",
  "ORGANIZATION",
  "EVENT",
] as const;

/** A node list + edge list that any of the three modes can render. */
interface VisibleGraph {
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
  targetKey: string | null;
}

export default function GraphPage() {
  const { caseId = "" } = useParams();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  const [mode, setMode] = useState<GraphMode>("person");

  // ---- person graph state ----
  const [persons, setPersons] = useState<PersonTarget[] | null>(null);
  const [targetKey, setTargetKey] = useState<string>("");
  const [depth, setDepth] = useState<1 | 2 | 3>(1);
  const [network, setNetwork] = useState<PersonNetwork | null>(null);
  const [personFindingItems, setPersonFindingItems] = useState<Finding[]>([]);

  // ---- master graph state ----
  const [master, setMaster] = useState<CaseGraph | null>(null);
  const [masterLabels, setMasterLabels] = useState<string[]>([]);
  const [masterRelTypes, setMasterRelTypes] = useState<string[]>([]);
  const [includeStaging, setIncludeStaging] = useState(false);

  // ---- temporal graph state ----
  const [temporal, setTemporal] = useState<TemporalGraph | null>(null);
  const [temporalFrom, setTemporalFrom] = useState("");
  const [temporalTo, setTemporalTo] = useState("");
  const [temporalTarget, setTemporalTarget] = useState("");
  const [temporalDepth, setTemporalDepth] = useState<number>(3);

  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNodeRow | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeRow | null>(null);
  const [graphBackend, setGraphBackend] = useState<string | null>(null);
  const [influence, setInfluence] = useState<Influence | null>(null);

  // ---- temporal path search (separate capability) ----
  const [pathFrom, setPathFrom] = useState("");
  const [pathTo, setPathTo] = useState("");
  const [paths, setPaths] = useState<unknown[] | null>(null);

  // The graph backend actually in use, reported honestly (neo4j | embedded).
  useEffect(() => {
    investigationState(caseId)
      .then((state) => setGraphBackend(String(state.graph_backend)))
      .catch(() => setGraphBackend(null));
  }, [caseId]);

  // The person rail: who can be investigated in this case.  Also feeds the
  // temporal graph's target selector.
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
    if (mode === "person" && targetKey) loadNetwork(targetKey, depth);
  }, [targetKey, depth, mode, loadNetwork]);

  // ---- master graph load ----
  const loadMaster = useCallback(
    (labels: string[], relTypes: string[], staging: boolean) => {
      setError(null);
      caseGraph(caseId, {
        labels,
        relTypes,
        includeStaging: staging,
      })
        .then((graph) => {
          setMaster(graph);
          setSelected(null);
          setSelectedEdge(null);
          setInfluence(null);
        })
        .catch((err: Error) => setError(err.message));
    },
    [caseId],
  );

  useEffect(() => {
    if (mode === "master") {
      loadMaster(masterLabels, masterRelTypes, includeStaging);
    }
  }, [mode, masterLabels, masterRelTypes, includeStaging, loadMaster]);

  // ---- temporal graph load ----
  const loadTemporal = useCallback(() => {
    setError(null);
    temporalGraph(caseId, {
      target: temporalTarget || undefined,
      fromTs: temporalFrom || undefined,
      toTs: temporalTo || undefined,
      depth: temporalDepth,
    })
      .then((graph) => {
        setTemporal(graph);
        setSelected(null);
        setSelectedEdge(null);
        setInfluence(null);
      })
      .catch((err: Error) => setError(err.message));
  }, [caseId, temporalTarget, temporalFrom, temporalTo, temporalDepth]);

  useEffect(() => {
    if (mode === "temporal") loadTemporal();
  }, [mode, loadTemporal]);

  // ---- the graph visible on the canvas for the current mode ----------------
  const visible = useMemo<VisibleGraph | null>(() => {
    if (mode === "person") {
      return network
        ? {
            nodes: network.nodes,
            edges: network.edges,
            targetKey: network.target.provenance_key,
          }
        : null;
    }
    if (mode === "master") {
      return master
        ? { nodes: master.nodes, edges: master.edges, targetKey: null }
        : null;
    }
    return temporal
      ? {
          nodes: temporal.nodes,
          edges: temporal.edges,
          targetKey: temporal.target ?? null,
        }
      : null;
  }, [mode, network, master, temporal]);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!visible) return [];
    const nodes: ElementDefinition[] = visible.nodes.map((node) => ({
      data: {
        id: node.provenance_key,
        name: node.name,
        label: node.label,
        confidence: node.confidence,
        is_target: node.provenance_key === visible.targetKey,
      },
    }));
    const nameOf = (key: string) =>
      visible.nodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 8);
    const edges: ElementDefinition[] = visible.edges.map((edge) => ({
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
  }, [visible]);

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
              // Zoom-safe: the name is truncated so dense views stay readable;
              // the full name lives in the detail panel.
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
            "border-width": (ele: cytoscape.NodeSingular) =>
              ele.data("is_target") ? 4 : 0,
            "border-style": "solid",
            "border-color": "#B45309",
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
      const node = visible?.nodes.find((n) => n.provenance_key === key) ?? null;
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
        visible?.edges.find(
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
  }, [elements, visible]);

  const nameOf = useCallback(
    (key: string) =>
      visible?.nodes.find((n) => n.provenance_key === key)?.name ?? key.slice(0, 10),
    [visible],
  );

  const counts = useMemo(() => {
    if (mode === "person" && network) return network.counts;
    if (mode === "master" && master) return master.counts;
    if (mode === "temporal" && temporal) return temporal.counts;
    return null;
  }, [mode, network, master, temporal]);

  const toggleLabel = (label: string) => {
    setMasterLabels((prev) =>
      prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label],
    );
  };

  const toggleRelType = (relType: string) => {
    setMasterRelTypes((prev) =>
      prev.includes(relType) ? prev.filter((r) => r !== relType) : [...prev, relType],
    );
  };

  const masterRelOptions = useMemo(() => {
    const set = new Set<string>();
    // Filter options come from the *unfiltered* counts so a relation that is
    // currently hidden can still be re-enabled.
    master?.counts.by_rel_type && Object.keys(master.counts.by_rel_type).forEach((r) => set.add(r));
    masterRelTypes.forEach((r) => set.add(r));
    return Array.from(set).sort();
  }, [master, masterRelTypes]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t("graph.title")}</h1>
          <p className="muted">{t("graph.modeHint")}</p>
        </div>
        {counts && (
          <div className="graph-meta">
            <span className="badge">
              {counts.nodes} nodes · {counts.edges} relations
            </span>
            <span className="badge">{t("graph.backend")}: {graphBackend ?? "…"}</span>
          </div>
        )}
      </header>

      {error && <ErrorState message={error} />}

      {/* ---- mode switcher ---- */}
      <div className="graph-modes" role="tablist" aria-label={t("graph.modes")}>
        {(
          [
            ["person", t("graph.modePerson")],
            ["master", t("graph.modeMaster")],
            ["temporal", t("graph.modeTemporal")],
          ] as [GraphMode, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={mode === value}
            className={`mode-tab ${mode === value ? "active" : ""}`}
            onClick={() => {
              setMode(value);
              setSelected(null);
              setSelectedEdge(null);
              setError(null);
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="graph-workspace">
        {/* ---- left rail: persons (person + temporal modes) ---- */}
        <aside className="graph-rail">
          {mode === "person" && (
            <>
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
            </>
          )}

          {mode === "temporal" && (
            <>
              <h2>{t("graph.temporalControls")}</h2>
              <div className="form-col">
                <label className="form-label" htmlFor="tp-from">{t("graph.temporalFrom")}</label>
                <input
                  id="tp-from"
                  type="datetime-local"
                  value={temporalFrom}
                  onChange={(e) => setTemporalFrom(e.target.value)}
                />
              </div>
              <div className="form-col">
                <label className="form-label" htmlFor="tp-to">{t("graph.temporalTo")}</label>
                <input
                  id="tp-to"
                  type="datetime-local"
                  value={temporalTo}
                  onChange={(e) => setTemporalTo(e.target.value)}
                />
              </div>
              <div className="form-col">
                <label className="form-label" htmlFor="tp-target">{t("graph.temporalTarget")}</label>
                <select
                  id="tp-target"
                  value={temporalTarget}
                  onChange={(e) => setTemporalTarget(e.target.value)}
                >
                  <option value="">{t("graph.temporalNoTarget")}</option>
                  {persons?.map((person) => (
                    <option key={person.provenance_key} value={person.provenance_key}>
                      {person.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-col">
                <label className="form-label" htmlFor="tp-depth">{t("graph.depth")}</label>
                <select
                  id="tp-depth"
                  value={temporalDepth}
                  onChange={(e) => setTemporalDepth(Number(e.target.value))}
                >
                  {[1, 2, 3, 4].map((hop) => (
                    <option key={hop} value={hop}>{hop}-hop</option>
                  ))}
                </select>
              </div>
              <button type="button" className="btn btn-primary" onClick={loadTemporal}>
                {t("graph.temporalBuild")}
              </button>
              {temporal?.empty_reason && (
                <p className="muted">{t("graph.temporalEmpty")}</p>
              )}
            </>
          )}

          {mode === "master" && (
            <>
              <h2>{t("graph.masterFilters")}</h2>
              <h3 className="rail-sub">{t("graph.filterLabels")}</h3>
              <div className="chip-group">
                {FILTERABLE_LABELS.map((label) => (
                  <button
                    key={label}
                    type="button"
                    className={`chip ${masterLabels.includes(label) ? "active" : ""}`}
                    onClick={() => toggleLabel(label)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <h3 className="rail-sub">{t("graph.filterRelTypes")}</h3>
              <div className="chip-group">
                {masterRelOptions.map((rel) => (
                  <button
                    key={rel}
                    type="button"
                    className={`chip chip-rel ${masterRelTypes.includes(rel) ? "active" : ""}`}
                    onClick={() => toggleRelType(rel)}
                  >
                    {relLabel(rel)}
                  </button>
                ))}
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={includeStaging}
                  onChange={(e) => setIncludeStaging(e.target.checked)}
                />
                {t("graph.includeStaging")}
              </label>
            </>
          )}
        </aside>

        {/* ---- canvas + controls ---- */}
        <section className="graph-main">
          {mode === "person" && network && (
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

          {/* temporal path search — a separate capability, supporting evidence */}
          {(mode === "temporal" || mode === "person") && visible && (
            <details className="paths-panel">
              <summary>{t("graph.paths")}</summary>
              <div className="form-row">
                <select
                  value={pathFrom}
                  onChange={(e) => setPathFrom(e.target.value)}
                  aria-label="From"
                >
                  <option value="">From…</option>
                  {visible.nodes.map((node) => (
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
                  {visible.nodes.map((node) => (
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
                  {t("graph.pathSearch")}
                </button>
              </div>
              {paths && paths.length === 0 && (
                <p className="muted">{t("graph.noPath")}</p>
              )}
              {paths && paths.length > 0 && (
                <pre className="code-block">
                  {JSON.stringify(paths, null, 2).slice(0, 4000)}
                </pre>
              )}
            </details>
          )}

          {/* temporal timeline strip */}
          {mode === "temporal" && temporal && temporal.events.length > 0 && (
            <TimelineStrip
              events={temporal.events}
              first={temporal.time_range.first}
              last={temporal.time_range.last}
              onSelect={(eventKey) => {
                const node = temporal.nodes.find((n) => n.provenance_key === eventKey) ?? null;
                setSelected(node);
                setSelectedEdge(null);
              }}
            />
          )}

          <div className="graph-canvas-wrap">
            {mode === "person" && !network && persons !== null && persons.length > 0 && (
              <Empty message={t("graph.pickTarget")} />
            )}
            {mode === "master" && !master && <Spinner />}
            {mode === "temporal" && !temporal && <Spinner />}
            {visible && visible.nodes.length === 0 && (
              <Empty message={t("graph.emptyView")} />
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
                {selected.provenance_key === visible?.targetKey && (
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
              {mode === "person" &&
                selected.provenance_key !== visible?.targetKey &&
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
              {mode === "person" &&
                selected.label === "PERSON" &&
                personFindingItems.length > 0 && (
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

/**
 * A horizontal strip of dated events inside the temporal window.  Each marker
 * is a real event node; clicking it selects the node on the canvas.
 */
function TimelineStrip(props: {
  events: TemporalEvent[];
  first: string | null;
  last: string | null;
  onSelect: (eventKey: string) => void;
}) {
  const { events, first, last, onSelect } = props;

  const span = useMemo(() => {
    if (!first || !last) return null;
    const start = Date.parse(first);
    const end = Date.parse(last);
    if (Number.isNaN(start) || Number.isNaN(end) || end === start) return null;
    return { start, end, length: end - start };
  }, [first, last]);

  return (
    <div className="timeline-strip" aria-label={t("graph.timeline")}>
      {events.map((event) => {
        let leftPct: number | null = null;
        if (span && event.timestamp) {
          const at = Date.parse(event.timestamp);
          if (!Number.isNaN(at)) {
            leftPct = Math.min(100, Math.max(0, ((at - span.start) / span.length) * 100));
          }
        }
        return (
          <button
            key={event.provenance_key}
            type="button"
            className="timeline-dot"
            style={leftPct !== null ? { left: `${leftPct}%` } : undefined}
            title={`${event.name} · ${event.timestamp ?? ""}`}
            onClick={() => onSelect(event.provenance_key)}
          />
        );
      })}
    </div>
  );
}
