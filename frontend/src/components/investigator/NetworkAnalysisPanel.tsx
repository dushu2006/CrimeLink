/**
 * NETWORK ANALYSIS — the explicit, non-auto-running graph surface.
 *
 * Three distinct, non-interchangeable scopes:
 *   MASTER NETWORK  — the whole active dataset, cross-case.
 *   CASE NETWORK    — one case's master graph.
 *   PERSON NETWORK  — one person's neighbourhood, cross-case.
 *
 * Nothing runs on mount: the investigator picks a scope, then clicks
 * RUN NETWORK ANALYSIS.  The job reports honest stages; the deterministic
 * graph/metrics survive an unavailable reasoning model.  The graph is drawn
 * from the real graph endpoints — never fabricated — and every metric row is
 * a structural measure (degree / weighted degree / betweenness / PageRank /
 * community / cross-case), never a criminal-probability score.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  caseGraph,
  getInvestigationJob,
  listCases,
  masterGraph,
  masterPersons,
  masterPersonNetwork,
  networkAnalysisFromJob,
  startNetworkAnalysis,
  type CaseSummary,
  type GraphEdgeRow,
  type GraphNodeRow,
  type InvestigationJob,
  type MasterPersonTarget,
  type NetworkAnalysisResult,
  type NetworkScopeMode,
  type StructuredFinding,
} from "../../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../Status";
import { EvidencePointerLink } from "../EvidenceLink";
import { GraphSourceChips } from "../graph/GraphSourceChips";
import { TechnicalDetails } from "../TechnicalDetails";
import { NetworkGraph } from "../NetworkGraph";
import { PatternList } from "./PatternCard";
import { isConfirmedCriminal, getDisplayLabel } from "../../lib/displayLabels";
import { HypothesisCard } from "./HypothesisCard";
import {
  EvidenceList,
  LabelLegend,
  ProvenanceChip,
} from "./InvestigatorEvidence";
import { relLabel } from "../../lib/investigation";
import {
  analyticalBasisLines,
  evidenceConfidenceSentence,
  gapHeading,
  gapSentence,
  labelText,
  labelsPresent,
  orderHypotheses,
  orderNextSteps,
  relationshipStrengthSentence,
  relationshipWhy,
} from "../../lib/investigator";

const MODES: { value: NetworkScopeMode; label: string }[] = [
  { value: "master", label: "MASTER NETWORK" },
  { value: "case", label: "CASE NETWORK" },
  { value: "person", label: "PERSON NETWORK" },
];

const JOB_STAGE_LABELS: Record<string, string> = {
  QUEUED: "Queued",
  PREPARING: "Preparing scope and loading graph snapshot",
  BUILDING_GRAPH: "Building graph snapshot for this scope",
  CALCULATING_METRICS: "Calculating degree, weighted degree, betweenness, PageRank",
  DETECTING_COMMUNITIES: "Detecting communities",
  DETECTING_PATTERNS: "Detecting unusual patterns (deterministic)",
  RETRIEVING_EVIDENCE: "Retrieving supporting evidence & relationships",
  SEARCHING_CONTRADICTIONS: "Searching contradictory evidence & alternatives",
  REASONING: "Reasoning (big reasoning model — may take time)",
  VALIDATING: "Validating evidence references & canonical IDs",
  GENERATING_EXPLANATION: "Generating investigator explanation",
  COMPLETED: "Completed",
  AI_UNAVAILABLE: "AI reasoning offline — deterministic analysis complete",
  AI_TIMEOUT: "AI timed out — deterministic analysis complete",
  AI_INVALID_RESPONSE: "AI response invalid — deterministic analysis complete",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

const METRIC_HEADINGS: Record<string, string> = {
  betweenness: "Betweenness centrality",
  degree: "Degree",
  weighted_degree: "Weighted degree",
  pagerank: "PageRank",
};

export type NetworkAnalysisPanelProps = {
  /**
   * Case id carried by the URL (``?case=…``).  When present the panel opens
   * on CASE NETWORK with that case already selected, so drilling into a case
   * never lands on the cross-case master scope by accident.
   */
  initialCaseId?: string;
};

export function NetworkAnalysisPanel({ initialCaseId }: NetworkAnalysisPanelProps = {}) {
  const [mode, setMode] = useState<NetworkScopeMode>(initialCaseId ? "case" : "master");
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [caseId, setCaseId] = useState<string>(initialCaseId ?? "");
  const [persons, setPersons] = useState<MasterPersonTarget[] | null>(null);
  const [personKey, setPersonKey] = useState<string>("");

  const [job, setJob] = useState<InvestigationJob | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [result, setResult] = useState<NetworkAnalysisResult | null>(null);

  // The graph, fetched from the real graph endpoints for the chosen scope.
  const [graphNodes, setGraphNodes] = useState<GraphNodeRow[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdgeRow[]>([]);
  const [graphTarget, setGraphTarget] = useState<string | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);

  const [selectedNode, setSelectedNode] = useState<GraphNodeRow | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeRow | null>(null);

  const pollRef = useRef<number | null>(null);

  // A case-scoped URL always wins: adopt it (and its scope) when it changes.
  useEffect(() => {
    if (!initialCaseId) return;
    setMode("case");
    setCaseId(initialCaseId);
  }, [initialCaseId]);

  // ---- context selectors -------------------------------------------------
  useEffect(() => {
    if (mode === "case" && cases === null) {
      listCases()
        .then((res) => {
          setCases(res.items);
          setCaseId((current) => {
            // Keep a valid explicit choice (URL or user); otherwise fall back
            // to the URL case and only then to the first case in the dataset.
            if (current && res.items.some((c) => c.id === current)) return current;
            if (initialCaseId && res.items.some((c) => c.id === initialCaseId)) return initialCaseId;
            return current || (res.items[0]?.id ?? "");
          });
        })
        .catch((err: Error) => setError(err.message));
    }
    if (mode === "person" && persons === null) {
      masterPersons()
        .then((res) => {
          setPersons(res.items);
          setPersonKey((current) => current || (res.items[0]?.provenance_key ?? ""));
        })
        .catch((err: Error) => setError(err.message));
    }
  }, [mode, cases, persons]);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  // ---- explicit run ------------------------------------------------------
  const run = useCallback(async () => {
    if (mode === "case" && !caseId) {
      setError("CASE NETWORK analysis requires a case.");
      return;
    }
    if (mode === "person" && !personKey) {
      setError("PERSON NETWORK analysis requires a person.");
      return;
    }
    setRunning(true);
    setError(null);
    setResult(null);
    setSelectedNode(null);
    setSelectedEdge(null);
    try {
      const started = await startNetworkAnalysis({
        mode,
        case_id: mode === "case" ? caseId : null,
        person_key: mode === "person" ? personKey : null,
      });
      setJob(started);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRunning(false);
    }
  }, [mode, caseId, personKey]);

  // ---- poll the job ------------------------------------------------------
  useEffect(() => {
    if (!job || job.terminal) return;
    const poll = async () => {
      try {
        const latest = await getInvestigationJob(job.id);
        setJob(latest);
        if (latest.terminal) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setRunning(false);
          const analysis = networkAnalysisFromJob(latest);
          if (analysis) {
            setResult(analysis);
          } else if (latest.status !== "COMPLETED") {
            setError(latest.error || `Network analysis finished with ${latest.status}.`);
          }
        }
      } catch (err) {
        console.warn("network analysis polling failed", err);
      }
    };
    pollRef.current = window.setInterval(() => void poll(), 1500) as unknown as number;
    void poll();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [job?.id, job?.terminal]);

  // ---- load the graph for the finished scope -----------------------------
  const loadGraph = useCallback(async () => {
    setGraphLoading(true);
    setGraphError(null);
    setSelectedNode(null);
    setSelectedEdge(null);
    try {
      if (mode === "master") {
        const g = await masterGraph();
        setGraphNodes(g.nodes);
        setGraphEdges(g.edges);
        setGraphTarget(null);
      } else if (mode === "case") {
        const g = await caseGraph(caseId);
        setGraphNodes(g.nodes);
        setGraphEdges(g.edges);
        setGraphTarget(null);
      } else {
        const g = await masterPersonNetwork(personKey);
        setGraphNodes(g.nodes);
        setGraphEdges(g.edges);
        setGraphTarget(g.target.provenance_key);
      }
    } catch (err) {
      setGraphError(err instanceof Error ? err.message : String(err));
    } finally {
      setGraphLoading(false);
    }
  }, [mode, caseId, personKey]);

  // Load the graph once the scope is finished (and when it changes).
  useEffect(() => {
    if (!result) return;
    if (mode === "case" && !caseId) return;
    if (mode === "person" && !personKey) return;
    void loadGraph();
  }, [result, mode, caseId, personKey, loadGraph]);

  const canRun =
    !running &&
    (mode === "master" || (mode === "case" && Boolean(caseId)) || (mode === "person" && Boolean(personKey)));

  return (
    <section className="panel inv-network-analysis" aria-labelledby="na-title" style={{ scrollMarginTop: "80px", paddingTop: "20px" }}>
      <div className="inv-objective-head" style={{ marginBottom: "6px" }}>
        <h2 id="na-title" style={{ margin: 0, fontSize: "18px", fontWeight: 700 }}>Network analysis — three scopes</h2>
        <span className="badge badge-navy" style={{ textTransform: "uppercase" }}>{mode} scope</span>
      </div>
      <p className="muted" style={{ marginBottom: "16px", lineHeight: "1.5" }}>
        Three distinct, non-interchangeable graphs over one active dataset. Nothing runs on its
        own — pick a scope, then RUN NETWORK ANALYSIS. Metrics are structural (degree, weighted
        degree, betweenness, PageRank, community, cross-case); none of them is a criminality score.
      </p>

      <div className="graph-modes" role="tablist" aria-label="Network analysis scope">
        {MODES.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={mode === item.value}
            className={`mode-tab ${mode === item.value ? "active" : ""}`}
            onClick={() => {
              setMode(item.value);
              setResult(null);
              setJob(null);
              setError(null);
              setGraphNodes([]);
              setGraphEdges([]);
              setGraphTarget(null);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="form-row" style={{ marginTop: "var(--space-3)" }}>
        {mode === "case" && (
          <label className="form-col" style={{ flex: 1 }}>
            <span className="form-label">Case</span>
            <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
              <option value="">Select a case…</option>
              {(cases ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.case_number} — {c.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {mode === "person" && (
          <label className="form-col" style={{ flex: 1 }}>
            <span className="form-label">Person (cross-case, active dataset)</span>
            <select value={personKey} onChange={(e) => setPersonKey(e.target.value)}>
              <option value="">Select a person…</option>
              {(persons ?? []).map((p) => (
                <option key={p.provenance_key} value={p.provenance_key}>
                  {p.name}
                  {p.criminal_status ? " · confirmed criminal" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="btn btn-primary" onClick={() => void run()} disabled={!canRun}>
          {running ? "Analysing…" : "RUN NETWORK ANALYSIS"}
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={() => { setError(null); void run(); }} />}

      {/* ---- honest progress ------------------------------------------- */}
      {job && (
        <div
          className="inv-progress"
          style={{
            marginTop: "var(--space-3)",
            padding: "16px 18px",
            background: "var(--cl-bg-elevated, #f8fafc)",
            borderRadius: "var(--cl-r-lg, 12px)",
            border: "1px solid var(--cl-border, #e2e8f0)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
            <h4 style={{ margin: 0, fontSize: "14px", fontWeight: 700, color: "var(--cl-ink, #0f172a)" }}>
              Network analysis job — honest progress
            </h4>
            {job.terminal && (
              <span
                style={{
                  background:
                    job.status === "COMPLETED" || Boolean(result) || job.status.startsWith("AI_")
                      ? "#dcfce7"
                      : "#fee2e2",
                  color:
                    job.status === "COMPLETED" || Boolean(result) || job.status.startsWith("AI_")
                      ? "#15803d"
                      : "#b91c1c",
                  fontWeight: 600,
                  fontSize: "11.5px",
                  padding: "3px 10px",
                  borderRadius: "999px",
                  border: `1px solid ${
                    job.status === "COMPLETED" || Boolean(result) || job.status.startsWith("AI_")
                      ? "#bbf7d0"
                      : "#fecaca"
                  }`,
                }}
              >
                {job.status === "COMPLETED"
                  ? "Analysis Complete"
                  : job.status.startsWith("AI_")
                  ? "Deterministic Analysis Preserved"
                  : job.status}
              </span>
            )}
          </div>
          <p className="muted" style={{ marginTop: "6px", marginBottom: "8px", fontSize: "12.5px" }}>
            Job {job.id.slice(0, 8)} · {JOB_STAGE_LABELS[job.stage] ?? job.stage} ·{" "}
            {job.progress_pct}% · {job.message}
          </p>
          <div style={{ background: "#e2e8f0", height: 8, borderRadius: 4, overflow: "hidden", margin: "8px 0" }}>
            <div
              style={{
                width: `${job.progress_pct}%`,
                background: job.terminal
                  ? (job.status === "COMPLETED" || Boolean(result) || job.status.startsWith("AI_") ? "#10b981" : "#ef4444")
                  : "#3b82f6",
                height: "100%",
                transition: "width 0.4s ease-in-out",
              }}
            />
          </div>
          {job.steps && job.steps.length > 0 && (
            <ul className="kv" style={{ fontSize: "var(--text-xs)", marginTop: "8px" }}>
              {job.steps.slice(-8).map((s, i) => (
                <li key={i}>
                  <dt>{JOB_STAGE_LABELS[s.stage] ?? s.stage}</dt>
                  <dd>{s.message} · {new Date(s.at).toLocaleTimeString()}</dd>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {running && !result && (
        <Spinner label="Running deterministic network analysis over the active dataset (real stages, no fake progress)…" />
      )}

      {/* ---- results ---------------------------------------------------- */}
      {result && (
        <>
          <div className="inv-graph" style={{ marginTop: "var(--space-3)" }}>
            <div className="inv-objective-head">
              <h3>Network graph — {result.analysis.scope_label}</h3>
              <button type="button" className="btn btn-tertiary btn-small" onClick={() => void loadGraph()}>
                Refresh graph
              </button>
            </div>
            <p className="muted">
              {result.analysis.graph.nodes} node(s) · {result.analysis.graph.edges} edge(s) ·{" "}
              {result.analysis.graph.communities} communit(ies). Pan, zoom and tap a node or edge to
              inspect. Confirmed criminals are star-shaped (source-derived legal/criminal status only).
            </p>
            <NetworkGraph
              nodes={graphNodes}
              edges={graphEdges}
              loading={graphLoading}
              error={graphError}
              onRetry={() => void loadGraph()}
              onSelectNode={(n) => {
                setSelectedNode(n);
                setSelectedEdge(null);
              }}
              onSelectEdge={(e) => {
                setSelectedEdge(e);
                setSelectedNode(null);
              }}
              targetKey={graphTarget}
            />
          </div>

          {(selectedNode || selectedEdge) && (
            <div className="detail-panel" style={{ marginTop: "var(--space-3)" }}>
              {selectedNode && (
                <>
                  <h3>
                    {getDisplayLabel(selectedNode)}
                    {isConfirmedCriminal(selectedNode) && <Badge value="confirmed criminal" />}
                    {selectedNode.provenance_key === graphTarget && (
                      <span className="badge">person target</span>
                    )}
                  </h3>
                  <p className="muted">
                    <Badge value={selectedNode.label} /> · confidence{" "}
                    {Math.round(selectedNode.confidence * 100)}%
                    {selectedNode.aliases.length > 0 && ` · a.k.a. ${selectedNode.aliases.join(", ")}`}
                  </p>
                  <p className="muted">
                    Criminal status is source-derived:{" "}
                    {selectedNode.criminal_status ? (
                      <Badge value={selectedNode.criminal_status} />
                    ) : (
                      <span className="muted">not recorded in source documents</span>
                    )}
                    . Network position never changes it.
                  </p>
                  <div className="graph-sources" style={{ marginBottom: "var(--space-2)" }}>
                    <GraphSourceChips
                      docIds={selectedNode.source_doc_ids}
                      emptyMessage="No source documents recorded for this entity."
                    />
                  </div>
                  <div className="evidence-link-row">
                    <EvidencePointerLink pointer={selectedNode.evidence} />
                  </div>
                </>
              )}
              {selectedEdge && (
                <>
                  <h3>Relationship — {relLabel(selectedEdge.rel_type)}</h3>
                  <p className="muted">
                    confidence {Math.round(selectedEdge.confidence * 100)}%
                  </p>
                  <div className="graph-sources" style={{ marginBottom: "var(--space-2)" }}>
                    <GraphSourceChips
                      docIds={[
                        ...(selectedEdge.source_doc_ids ?? []),
                        selectedEdge.source_doc_id,
                      ]}
                      emptyMessage="No source documents recorded for this relationship."
                    />
                  </div>
                  <div className="evidence-link-row">
                    <EvidencePointerLink pointer={selectedEdge.evidence} />
                  </div>
                </>
              )}
            </div>
          )}

          <StructuredFindingsSection findings={result.response.structured_findings ?? []} />
          <SilentIntermediariesSection items={result.response.silent_intermediaries ?? []} />
          <MetricsSection analysis={result} />
          <CommunitiesSection analysis={result} />
          <CrossCaseSection analysis={result} />

          <section className="panel">
            <div className="inv-objective-head">
              <h3>Suspicious / unusual patterns</h3>
              <span className="badge badge-muted">
                {result.response.patterns.filter((p) => !p.excluded).length} live signal(s)
              </span>
            </div>
            <PatternList patterns={result.response.patterns} />
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h3>Relationship analysis</h3>
              <span className="badge badge-muted">{result.response.relationships.length} relationship(s)</span>
            </div>
            {result.response.relationships.length === 0 ? (
              <p className="muted">No relationship was computed in this scope.</p>
            ) : (
              <ul className="inv-relationship-list">
                {result.response.relationships.map((relationship) => (
                  <li key={`${relationship.kind}-${relationship.title}`} className="inv-relationship">
                    <div className="inv-relationship-head">
                      <span className="badge badge-navy">{relationship.kind.replaceAll("_", " ")}</span>
                      <Badge value={relationship.inference_label} />
                      <strong>{relationship.title}</strong>
                    </div>
                    <p>{relationship.description}</p>
                    <p className="muted">
                      <strong>WHY:</strong> {relationshipWhy(relationship)}
                    </p>
                    {(relationship.relationship_strength || relationship.evidence_strength) && (
                      <p className="muted inv-relationship-meta">
                        {relationshipStrengthSentence(relationship.relationship_strength)}
                        {" · "}
                        {evidenceConfidenceSentence(relationship.evidence_strength)}
                      </p>
                    )}
                    <EvidenceList items={relationship.evidence} caseId="" />
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h3>Hypotheses — supporting / contradictory / alternatives</h3>
              <span className="badge badge-muted">
                {orderHypotheses(result.response.hypotheses).length} under test
              </span>
            </div>
            {result.response.hypotheses.length === 0 ? (
              <p className="muted">No hypothesis was generated for this scope.</p>
            ) : (
              orderHypotheses(result.response.hypotheses).map((hypothesis) => (
                <HypothesisCard key={hypothesis.id} hypothesis={hypothesis} />
              ))
            )}
          </section>

          <section className="panel">
            <h3>Data gaps</h3>
            {result.response.gaps.length === 0 ? (
              <p className="muted">No data gap was recorded for this scope.</p>
            ) : (
              <ul className="inv-gap-list">
                {result.response.gaps.map((gap, index) => (
                  <li key={`${gap.category}-${index}`} className="inv-gap">
                    <div className="inv-gap-head">
                      <span className="badge badge-muted">{labelText(gap.inference_label)}</span>
                      <strong>{gapHeading(gap)}</strong>
                    </div>
                    <p>{gapSentence(gap)}</p>
                    {gap.what_would_help && (
                      <p className="muted"><strong>What would close it:</strong> {gap.what_would_help}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h3>Next investigative direction</h3>
            {result.response.next_steps.length === 0 ? (
              <p className="muted">No follow-up direction was derived.</p>
            ) : (
              <ol className="inv-next-list">
                {orderNextSteps(result.response.next_steps).map((step, index) => (
                  <li key={`${index}-${step.action.slice(0, 24)}`} className={`inv-next inv-next-${step.priority}`}>
                    <div className="inv-next-head">
                      <span className={`badge badge-${step.priority === "high" ? "warn" : "muted"}`}>
                        {step.priority}
                      </span>
                      <strong>{step.action}</strong>
                    </div>
                    <p className="muted">{step.rationale}</p>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h3>Analytical basis — WHY surfaced, WHICH signals</h3>
              <span className="badge badge-muted">metrics measure structure, not criminality</span>
            </div>
            <ul className="kv">
              <li><dt>Scope</dt><dd>{result.analysis.scope_label}</dd></li>
              <li><dt>Cases considered</dt><dd>{result.analysis.case_ids.length}</dd></li>
              <li><dt>Nodes considered</dt><dd>{result.analysis.graph.nodes}</dd></li>
              <li><dt>Edges considered</dt><dd>{result.analysis.graph.edges}</dd></li>
              <li><dt>Communities detected</dt><dd>{result.analysis.graph.communities}</dd></li>
              {(result.response as any).analytical_basis?.evidence_convergence?.convergence_type && (
                <li>
                  <dt>Evidence convergence</dt>
                  <dd>{(result.response as any).analytical_basis.evidence_convergence.convergence_type}</dd>
                </li>
              )}
            </ul>
            <p className="muted">
              Degree, weighted degree, betweenness and PageRank describe the shape of the network.
              HIGH CENTRALITY DOES NOT MEAN CRIMINAL. Criminal status is read only from source
              documents (star nodes); network role and investigative relevance are separate
              dimensions from legal status.
            </p>
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h3>Provenance — finding → evidence → source document → location</h3>
            </div>
            <LabelLegend labels={labelsPresent(result.response)} />
            {result.response.provenance.length === 0 ? (
              <p className="muted">
                No source document is in scope for this analysis — a data gap, not a clean result.
              </p>
            ) : (
              <ul className="inv-provenance-list">
                {result.response.provenance.map((pointer) => (
                  <li key={`${pointer.kind}-${pointer.ref}`}>
                    <ProvenanceChip pointer={pointer} caseId="" />
                    {pointer.detail && <span className="muted"> · {pointer.detail}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Sections                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * KEY FINDINGS — the "why did CrimeLink surface this?" chain per finding.
 *
 * Renders exactly the structured findings the backend computed: title,
 * entity + type, WHY, analytical basis (only the metrics actually used),
 * supporting vs contradictory evidence, alternatives, data gaps and next
 * direction.  When the backend produced none, it says so rather than
 * inventing cards.
 */
function StructuredFindingsSection({ findings }: { findings: StructuredFinding[] }) {
  if (findings.length === 0) return null;
  return (
    <section className="panel inv-key-findings">
      <div className="inv-objective-head">
        <h3>Key findings — WHY each was surfaced</h3>
        <span className="badge badge-muted">{findings.length} finding(s)</span>
      </div>
      <p className="muted">
        Every finding below answers the same question: which records and structural measures caused
        the deterministic detectors to surface it. Metrics describe network shape, never criminality.
      </p>
      {findings.map((finding) => (
        <article key={finding.finding_id} className="inv-finding-card">
          <header className="inv-finding-card-head">
            <span className="badge badge-navy">{finding.finding_id}</span>
            <strong>{finding.title}</strong>
            <span className="badge badge-muted">{finding.finding_type.replaceAll("_", " ")}</span>
          </header>

          <p className="inv-finding-why">
            <strong>Why:</strong> {finding.why || "No mechanism was recorded for this finding."}
          </p>

          {finding.entities.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Type</th>
                  <th>Legal / source status</th>
                  <th>Network role</th>
                </tr>
              </thead>
              <tbody>
                {finding.entities.map((entity) => (
                  <tr key={entity.canonical_id}>
                    <td>{entity.display_name}</td>
                    <td><Badge value={entity.entity_type || entity.label} /></td>
                    <td>
                      {entity.legal_status || entity.criminal_status ? (
                        <Badge value={entity.legal_status || entity.criminal_status || ""} />
                      ) : (
                        <span className="muted">not recorded in source</span>
                      )}
                    </td>
                    <td className="muted">{entity.network_role || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {finding.analytical_basis && (
            <details className="technical-details">
              <summary className="technical-details-toggle">View analytical basis</summary>
              <div className="technical-details-content">
                {(() => {
                  const lines = analyticalBasisLines(finding.analytical_basis);
                  if (lines.length === 0) {
                    return <p className="muted">No structural metric was used for this finding.</p>;
                  }
                  return (
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Method used</th>
                          <th>Value</th>
                          <th>Why it mattered</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lines.map((line) => (
                          <tr key={line.metric}>
                            <td>{line.metric}</td>
                            <td>{line.value}</td>
                            <td className="muted">{line.why}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  );
                })()}
              </div>
            </details>
          )}

          <div className="inv-debate">
            <section className="inv-debate-col inv-debate-supports">
              <h4>Supporting evidence</h4>
              <EvidenceList
                items={finding.supporting_evidence}
                empty="No supporting record is attached to this finding."
              />
            </section>
            <section className="inv-debate-col inv-debate-contradicts">
              <h4>Contradictory evidence</h4>
              <EvidenceList
                items={finding.contradictory_evidence}
                empty="No contradicting record was found — which is not the same as confirmation."
              />
            </section>
          </div>

          {finding.alternative_explanations.length > 0 && (
            <p className="muted">
              <strong>Alternative explanation:</strong> {finding.alternative_explanations.join(" · ")}
            </p>
          )}

          {finding.data_gaps.length > 0 && (
            <ul className="inv-gap-list" style={{ marginTop: "var(--space-2)" }}>
              {finding.data_gaps.map((gap, index) => (
                <li key={`${gap.category}-${index}`} className="inv-gap">
                  <strong>{gapHeading(gap)}:</strong> {gapSentence(gap)}
                </li>
              ))}
            </ul>
          )}

          {finding.next_investigative_direction && (
            <p className="muted">
              <strong>Next direction:</strong> {finding.next_investigative_direction}
            </p>
          )}
        </article>
      ))}
    </section>
  );
}

/**
 * Low-visibility, structurally important intermediaries — deterministic and
 * explainable, never "silent criminal".
 */
function SilentIntermediariesSection({ items }: { items: NonNullable<NetworkAnalysisResult["response"]["silent_intermediaries"]> }) {
  if (items.length === 0) return null;
  return (
    <section className="panel">
      <div className="inv-objective-head">
        <h3>Low-visibility intermediaries</h3>
        <span className="badge badge-muted">structural position, not a verdict</span>
      </div>
      <p className="muted">
        People whose network position matters more than their direct-connection count suggests:
        higher betweenness, community/cross-case bridging, few direct ties. Surfaced deterministically.
      </p>
      <ul>
        {items.map((item) => (
          <li key={item.entity_id} className="inv-silent-item">
            <strong>{item.display_name}</strong>{" "}
            <Badge value={item.entity_type} />{" "}
            <span className="badge badge-navy">{item.network_role.replaceAll("_", " ")}</span>
            <p className="muted">{item.why_surfaced}</p>
            {item.analytical_basis && (
              <p className="muted">
                Betweenness {item.analytical_basis.betweenness_centrality ?? "—"} · cross-case{" "}
                {item.analytical_basis.cross_case_count ?? 0}
              </p>
            )}
            <p className="inv-boundary-note">{item.disclaimer}</p>
            {item.supporting_evidence.length > 0 && (
              <EvidenceList items={item.supporting_evidence} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function MetricsSection({ analysis }: { analysis: NetworkAnalysisResult }) {
  const { metrics } = analysis.analysis;
  const all: { metric: string; key: string; name: string; label: string; value: number; case_count: number; is_criminal: boolean }[] = [
    ...metrics.betweenness.map((r) => ({ metric: "betweenness", key: r.key, name: r.name, label: r.label, value: r.value, case_count: r.case_count ?? 0, is_criminal: r.is_criminal })),
    ...metrics.degree.map((r) => ({ metric: "degree", key: r.key, name: r.name, label: r.label, value: r.value, case_count: r.case_count ?? 0, is_criminal: r.is_criminal })),
    ...metrics.weighted_degree.map((r) => ({ metric: "weighted_degree", key: r.key, name: r.name, label: r.label, value: r.value, case_count: r.case_count ?? 0, is_criminal: r.is_criminal })),
    ...metrics.pagerank.map((r) => ({ metric: "pagerank", key: r.key, name: r.name, label: r.label, value: r.value, case_count: r.case_count ?? 0, is_criminal: r.is_criminal })),
  ];
  const explanations = metrics.explanations ?? {};
  return (
    <section className="panel">
      <div className="inv-objective-head">
        <h3>Network metrics — structural, with explanations</h3>
        <span className="badge badge-muted">not a criminal-probability score</span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Entity</th>
            <th>Type</th>
            <th>Value</th>
            <th>Cases</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {all.map((row, i) => (
            <tr key={`${row.metric}-${row.key}-${i}`}>
              <td className="muted">{METRIC_HEADINGS[row.metric] ?? row.metric}</td>
              <td>{row.name}</td>
              <td><Badge value={row.label} /></td>
              <td>{row.value.toFixed(6)}</td>
              <td>{row.case_count ?? 0}</td>
              <td>{row.is_criminal ? <Badge value="confirmed criminal" /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {all.length === 0 && <Empty message="No metric could be computed for this scope." />}
      <TechnicalDetails label="what each metric means">
        <ul className="kv" style={{ fontSize: "var(--text-xs)" }}>
          {Object.entries(explanations).map(([key, text]) => (
            <li key={key}>
              <dt>{key}</dt>
              <dd>{text}</dd>
            </li>
          ))}
        </ul>
        <p className="muted">
          These are deterministic graph-structure measures. They prioritise where to look; they
          never establish or imply criminal status.
        </p>
      </TechnicalDetails>
    </section>
  );
}

function CommunitiesSection({ analysis }: { analysis: NetworkAnalysisResult }) {
  const communities = analysis.analysis.communities;
  return (
    <section className="panel">
      <div className="inv-objective-head">
        <h3>Communities</h3>
        <span className="badge badge-muted">{communities.length} detected</span>
      </div>
      {communities.length === 0 ? (
        <Empty message="No community structure was detected in this scope." />
      ) : (
        <ul>
          {communities.map((community) => (
            <li key={community.id}>
              <strong>Community {community.id}</strong>{" "}
              <span className="muted">({community.size} member(s))</span> —{" "}
              {community.top_members.map((m) => `${m.name} (${m.label})`).join(", ")}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function CrossCaseSection({ analysis }: { analysis: NetworkAnalysisResult }) {
  const rows = analysis.analysis.cross_case;
  return (
    <section className="panel">
      <div className="inv-objective-head">
        <h3>Cross-case entities</h3>
        <span className="badge badge-muted">{rows.length} across case boundaries</span>
      </div>
      {rows.length === 0 ? (
        <p className="muted">No entity spans more than one case in this scope.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Type</th>
              <th>Cases</th>
              <th>Betweenness</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.name}</td>
                <td><Badge value={row.label} /></td>
                <td>{row.case_count}</td>
                <td>{row.betweenness.toFixed(6)}</td>
                <td>{row.is_criminal ? <Badge value="confirmed criminal" /> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">
        Appearing in several cases is a cross-case link to investigate — it is not, on its own,
        evidence of criminality.
      </p>
    </section>
  );
}

// Default export kept for the lazy/Suspense call sites that already use it.
export default NetworkAnalysisPanel;
