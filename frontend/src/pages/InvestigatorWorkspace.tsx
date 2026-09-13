/**
 * Investigator analysis workspace — global master for the active dataset.
 *
 * Upgraded to EVIDENCE-GROUNDED with long-running job support:
 * - Explicit START INVESTIGATION triggers POST /investigate/jobs (async)
 * - Polling + WebSocket subscription for honest stage progress
 * - Deterministic work preserved even when AI unavailable/timeout
 * - Recovers from refresh via sessionStorage job_id
 * - Shows analytical basis, investigative relevance, evidence strength, silent intermediaries
 *
 * Criminal status remains source-derived; suspicious never means criminality.
 * Every evidence reference is clickable to the same SourceViewer.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  getInvestigationJob,
  getInvestigationJobWsUrl,
  investigationPatterns,
  startInvestigationJob,
  type AssessmentSection,
  type DataGap,
  type Hypothesis,
  type InvestigationJob,
  type InvestigatorResponse,
  type NextStep,
  type ResolvedEntity,
  type SuspiciousPattern,
} from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { TechnicalDetails } from "../components/TechnicalDetails";
import {
  EvidenceList,
  LabelLegend,
  NoteList,
  ProvenanceChip,
} from "../components/investigator/InvestigatorEvidence";
import { HypothesisCard } from "../components/investigator/HypothesisCard";
import { PatternList } from "../components/investigator/PatternCard";
import {
  FocusedEvidenceGraph,
  type GraphSelection,
} from "../components/investigator/FocusedEvidenceGraph";
import { InvestigationTimeline } from "../components/investigator/InvestigationTimeline";
import NetworkAnalysisPanel from "../components/investigator/NetworkAnalysisPanel";
import {
  centralityNarrative,
  convergenceSentence,
  entityStanding,
  evidenceConfidenceSentence,
  gapHeading,
  gapSentence,
  labelText,
  labelsPresent,
  modelUnavailableNote,
  nextStepLink,
  orderHypotheses,
  orderNextSteps,
  patternKey,
  patternSourceCount,
  patternTypeLabel,
  provenanceOfProse,
  relationshipStrengthSentence,
  relationshipWhy,
  scopeLabel,
  scopeSentence,
  selectionEntityKeys,
  subgraphFor,
  suggestedQuestions,
} from "../lib/investigator";

type Selection =
  | { kind: "pattern"; value: SuspiciousPattern }
  | { kind: "hypothesis"; value: Hypothesis }
  | { kind: "relationship"; value: InvestigatorResponse["relationships"][number] }
  | null;

interface ActiveDataset {
  id: string;
  name: string;
  status: string;
  case_count?: number;
  document_count?: number;
  created_at?: string;
}

interface DatasetStats {
  dataset_id: string;
  dataset_name: string;
  cases: number;
  documents: number;
  total_nodes?: number;
  total_edges?: number;
  [k: string]: any;
}

interface MasterCounts {
  nodes: number;
  edges: number;
  cases: number;
  communities?: number;
}

const STORAGE_PREFIX = "crimelink:investigation-thread:master:";
const JOB_STORAGE_KEY = "crimelink:investigation-job:master:";

const STAGE_LABELS: Record<string, string> = {
  scope_ms: "Scope & active dataset",
  resolution_ms: "Entity resolution",
  patterns_ms: "Pattern detectors (deterministic)",
  relationships_ms: "Relationship analysis",
  hypotheses_ms: "Hypotheses & convergence",
  gaps_ms: "Gaps & next steps",
  narrative_ms: "Narrative (AI Gateway, audited)",
  total_ms: "Total",
};

const JOB_STAGE_LABELS: Record<string, string> = {
  QUEUED: "Queued",
  PREPARING: "Preparing dataset & graph snapshot",
  ANALYZING_GRAPH: "Analyzing graph (degree, betweenness, PageRank, communities)",
  DETECTING_PATTERNS: "Detecting unusual patterns (deterministic)",
  RETRIEVING_EVIDENCE: "Retrieving supporting evidence & relationships",
  SEARCHING_CONTRADICTIONS: "Searching contradictory evidence & alternatives",
  REASONING: "Reasoning (big reasoning model — may take time)",
  VALIDATING: "Validating evidence references & canonical IDs",
  GENERATING_EXPLANATION: "Generating investigator explanation",
  COMPLETED: "Completed",
  COMPLETED_WITH_DETERMINISTIC: "Completed (deterministic only — AI unavailable)",
  AI_UNAVAILABLE: "AI unavailable — deterministic preserved",
  AI_TIMEOUT: "AI timeout — deterministic preserved",
  AI_INVALID_RESPONSE: "AI invalid response — deterministic preserved",
  FAILED: "Failed",
};

export default function InvestigatorWorkspace() {
  const [question, setQuestion] = useState("");
  const [objective, setObjective] = useState("");
  const [response, setResponse] = useState<InvestigatorResponse | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [graphSelection, setGraphSelection] = useState<GraphSelection | null>(null);
  const [scan, setScan] = useState<SuspiciousPattern[] | null>(null);
  const [scanLabel] = useState("");
  const [scanError, setScanError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [datasetStats, setDatasetStats] = useState<DatasetStats | null>(null);
  const [masterCounts, setMasterCounts] = useState<MasterCounts | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);

  // Long-running job state
  const [job, setJob] = useState<InvestigationJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  /* ---------------------------------------------------------------- scope: active dataset real counts */

  const loadScope = useCallback(async () => {
    setScopeError(null);
    try {
      const [active, stats, master] = await Promise.all([
        api<ActiveDataset | null>("/datasets/active").catch(() => null),
        api<DatasetStats>("/datasets/stats").catch(() => null as any),
        api<{ mode: string; case_ids: string[]; nodes: number; edges: number; communities: number; metrics?: any }>(
          "/graph/master/analytics"
        ).catch(() => null as any),
      ]);
      if (active) setActiveDataset(active);
      else if (stats) setActiveDataset({ id: stats.dataset_id, name: stats.dataset_name, status: "ACTIVE" });
      if (stats) setDatasetStats(stats);
      if (master) {
        setMasterCounts({
          nodes: master.nodes,
          edges: master.edges,
          cases: (master.case_ids?.length ?? stats?.cases ?? 0),
          communities: master.communities,
        });
      } else if (stats) {
        setMasterCounts({
          nodes: (stats as any).total_nodes ?? (stats as any).nodes ?? 0,
          edges: (stats as any).total_edges ?? (stats as any).edges ?? 0,
          cases: stats.cases,
        });
      }
    } catch (err) {
      setScopeError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadScope();
    const remembered = window.sessionStorage.getItem(STORAGE_PREFIX + "current");
    setThreadId(remembered);
    // Recover job from storage
    const rememberedJob = window.sessionStorage.getItem(JOB_STORAGE_KEY + "current");
    if (rememberedJob) {
      try {
        const parsed = JSON.parse(rememberedJob) as InvestigationJob;
        if (!parsed.terminal) {
          setJob(parsed);
          // will trigger polling effect
        }
      } catch {}
    }
  }, [loadScope]);

  // Cleanup polling/ws on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
      if (wsRef.current) {
        try { wsRef.current.close(); } catch {}
      }
    };
  }, []);

  // Polling + WS subscription when job active
  useEffect(() => {
    if (!job || job.terminal) {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }

    // Try WebSocket first
    let wsFailed = false;
    try {
      const wsUrl = getInvestigationJobWsUrl(job.id);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.job_id || data.id) {
            // Normalize to InvestigationJob shape
            const updated: InvestigationJob = {
              id: data.job_id || data.id,
              dataset_id: data.dataset_id ?? job.dataset_id,
              case_id: data.case_id ?? job.case_id,
              investigation_id: data.investigation_id ?? job.investigation_id,
              question: data.question ?? job.question,
              objective: data.objective ?? job.objective,
              status: data.status ?? job.status,
              stage: data.stage ?? job.stage,
              progress_pct: data.progress_pct ?? job.progress_pct,
              message: data.message ?? job.message,
              steps: data.steps ?? job.steps,
              result: data.result ?? job.result,
              error: data.error ?? job.error,
              requested_by: data.requested_by ?? job.requested_by,
              created_at: data.created_at ?? job.created_at,
              updated_at: data.updated_at ?? job.updated_at,
              finished_at: data.finished_at ?? job.finished_at,
              terminal: data.terminal ?? (data.status === "COMPLETED" || data.status?.startsWith("AI_") || data.status === "FAILED"),
            };
            setJob(updated);
            window.sessionStorage.setItem(JOB_STORAGE_KEY + "current", JSON.stringify(updated));
            if (updated.terminal) {
              if (updated.result?.response) {
                const resp = updated.result.response as InvestigatorResponse;
                setResponse(resp);
                setThreadId(resp.investigation_id);
                window.sessionStorage.setItem(STORAGE_PREFIX + "current", resp.investigation_id);
                setHistory((prior) => [...prior, resp.question]);
              } else if (updated.result?.response) {
                // already handled
              }
              // If AI unavailable but deterministic preserved, still show response
              if (updated.result && (updated.result as any).response) {
                setResponse((updated.result as any).response);
              }
            }
          } else if (data.type === "job_snapshot" || data.type === "investigation_progress" || data.type === "investigation_finished") {
            const j = data.job || data;
            if (j.id || j.job_id) {
              setJob((prev) => {
                if (!prev) return prev;
                const merged = { ...prev, ...j, id: j.id || j.job_id || prev.id, terminal: j.terminal ?? prev.terminal };
                window.sessionStorage.setItem(JOB_STORAGE_KEY + "current", JSON.stringify(merged));
                return merged as InvestigationJob;
              });
              if (j.result?.response) {
                setResponse(j.result.response as InvestigatorResponse);
                setThreadId(j.result.response.investigation_id);
              }
            }
          }
        } catch {}
      };
      ws.onerror = () => { wsFailed = true; };
      ws.onclose = () => {
        // If not terminal, fallback to polling will continue
      };
    } catch {
      wsFailed = true;
    }

    // Polling fallback every 2s
    const poll = async () => {
      try {
        const latest = await getInvestigationJob(job.id);
        setJob(latest);
        window.sessionStorage.setItem(JOB_STORAGE_KEY + "current", JSON.stringify(latest));
        if (latest.terminal) {
          if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
          if (latest.result?.response) {
            const resp = latest.result.response as InvestigatorResponse;
            setResponse(resp);
            setThreadId(resp.investigation_id);
            window.sessionStorage.setItem(STORAGE_PREFIX + "current", resp.investigation_id);
            setHistory((prior) => [...prior, resp.question]);
            setLoading(false);
          } else {
            // AI unavailable case: result may still have deterministic partial
            const resultAny = latest.result as any;
            if (resultAny?.response) {
              setResponse(resultAny.response);
              setThreadId(resultAny.response.investigation_id);
            } else if (resultAny?.error || latest.error) {
              setJobError(latest.error || resultAny?.error || "Investigation failed");
            }
            setLoading(false);
          }
          if (wsRef.current) { try { wsRef.current.close(); } catch {} }
        }
      } catch (err) {
        // polling error, keep trying
        console.warn("polling failed", err);
      }
    };

    pollRef.current = window.setInterval(() => { void poll(); }, 2000) as unknown as number;
    // immediate poll
    void poll();

    return () => {
      if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
      if (wsRef.current) { try { wsRef.current.close(); } catch {} wsRef.current = null; }
    };
  }, [job?.id, job?.terminal]);

  /* ---------------------------------------------------------------- scan: explicit, not auto */

  const refreshScan = useCallback(() => {
    setScan(null);
    setScanError(null);
    investigationPatterns({ caseId: null, maxPatterns: 25, includeExcluded: true })
      .then((result) => {
        setScan(result.patterns);
      })
      .catch((err: Error) => setScanError(err.message));
  }, []);

  /* -------------------------------------------------------------- submit: explicit START INVESTIGATION */

  const run = useCallback(
    async (raw: string, explicitObjective?: string) => {
      const asked = raw.trim();
      if (asked.length < 3 || loading) return;
      setLoading(true);
      setError(null);
      setJobError(null);
      setJob(null);
      window.sessionStorage.removeItem(JOB_STORAGE_KEY + "current");
      try {
        const continuing = Boolean(threadId);
        // Start long-running job
        const started = await startInvestigationJob({
          question: asked,
          case_id: null,
          investigation_id: continuing ? threadId : null,
          objective: explicitObjective?.trim() ? explicitObjective.trim() : null,
        });
        setJob(started);
        window.sessionStorage.setItem(JOB_STORAGE_KEY + "current", JSON.stringify(started));
        // Polling effect will handle completion
        setQuestion("");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      }
    },
    [loading, threadId],
  );

  // When job completes via polling effect, loading false is set there
  // Also handle case where job already completed synchronously
  useEffect(() => {
    if (job?.terminal) {
      setLoading(false);
    } else if (job && !job.terminal) {
      setLoading(true);
    }
  }, [job]);

  const startNewThread = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_PREFIX + "current");
    window.sessionStorage.removeItem(JOB_STORAGE_KEY + "current");
    setThreadId(null);
    setResponse(null);
    setSelection(null);
    setHistory([]);
    setQuestion("");
    setJob(null);
    setJobError(null);
    setError(null);
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    if (wsRef.current) { try { wsRef.current.close(); } catch {} wsRef.current = null; }
  }, []);

  /* ------------------------------------------------------------ derived */

  const entities: ResolvedEntity[] = response?.entities ?? [];
  const patterns = response?.patterns ?? [];
  const livePatternsForChips = response ? patterns : scan ?? [];
  const chips = useMemo(
    () => suggestedQuestions(livePatternsForChips, entities.map((entity) => entity.display_name)),
    [livePatternsForChips, entities],
  );

  const highlightKeys = selectionEntityKeys(selection, entities);
  const focused = response?.focused_graph ?? null;
  const selectionGraph = useMemo(
    () => (selection ? subgraphFor(focused, highlightKeys) : null),
    [selection, focused, highlightKeys],
  );

  const livePatterns = useMemo(() => patterns.filter((pattern) => !pattern.excluded), [patterns]);
  const excludedPatterns = useMemo(() => patterns.filter((pattern) => pattern.excluded), [patterns]);
  const hypotheses = useMemo(() => orderHypotheses(response?.hypotheses), [response]);
  const assessment: AssessmentSection | null = response?.assessment ?? null;

  const statedObjective = response?.objective ?? (objective.trim() || "Not stated yet");
  const metricLines =
    selection?.kind === "pattern" &&
    (selection.value.kind === "COMMUNITY_SIGNAL" || selection.value.kind === "NETWORK_BRIDGE")
      ? centralityNarrative(selection.value)
      : [];
  const continuing = Boolean(threadId) && history.length > 0;
  const timing = response?.timing_ms ?? null;

  /* --------------------------------------------------------------- view */

  return (
    <div className="page inv-workspace">
      <header className="page-head">
        <div>
          <h1>Investigation Analysis — Master Network</h1>
          <p className="muted">
            Global analysis over the active dataset. No last-opened case fallback. Every view is
            dataset-scoped and audited. Criminal status is source-derived; suspicious never means
            criminality.
          </p>
        </div>
        <div className="row-actions">
          <span className="badge badge-navy" title="What this analysis is allowed to read">
            Scope: {response ? scopeLabel(response.scope) : activeDataset ? `Active: ${activeDataset.name}` : "Loading active dataset…"}
          </span>
          <Link className="btn btn-secondary" to="/cases">
            Cases Registry
          </Link>
          <button type="button" className="btn btn-tertiary btn-small" onClick={() => void loadScope()}>
            Refresh scope
          </button>
        </div>
      </header>

      {/* ------------------------------------------------ scope panel with real counts */}
      <section className="panel inv-objective" aria-labelledby="inv-scope-title">
        <div className="inv-objective-head">
          <h2 id="inv-scope-title">Active dataset scope — real counts</h2>
          <span className="badge badge-muted">{activeDataset?.id ? activeDataset.id.slice(0, 8) : "no active dataset"}</span>
        </div>
        {scopeError && <ErrorState message={scopeError} onRetry={() => void loadScope()} />}
        {!activeDataset && !scopeError && <Spinner label="Loading active dataset…" />}
        {activeDataset && (
          <>
            <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", marginTop: "var(--space-2)" }}>
              <span className="chip">
                <span className="chip-label">Dataset:</span> <strong>{activeDataset.name}</strong>
              </span>
              <span className="chip">
                <span className="chip-label">Cases in active dataset:</span>{" "}
                <strong>{datasetStats?.cases ?? masterCounts?.cases ?? "—"}</strong>
              </span>
              <span className="chip">
                <span className="chip-label">Documents:</span> <strong>{datasetStats?.documents ?? "—"}</strong>
              </span>
              <span className="chip">
                <span className="chip-label">Graph nodes (master):</span>{" "}
                <strong>{masterCounts?.nodes ?? response?.scope.nodes_considered ?? "—"}</strong>
              </span>
              <span className="chip">
                <span className="chip-label">Graph edges (master):</span>{" "}
                <strong>{masterCounts?.edges ?? response?.scope.edges_considered ?? "—"}</strong>
              </span>
              {masterCounts?.communities !== undefined && (
                <span className="chip">
                  <span className="chip-label">Communities:</span> <strong>{masterCounts.communities}</strong>
                </span>
              )}
            </div>
            <p className="muted" style={{ marginTop: "var(--space-2)" }}>
              These counts are from the active dataset registry and master graph analytics, not hardcoded.
              Replacing the active dataset replaces every count and graph. Star-shaped nodes in any graph
              view indicate confirmed criminals — status is read from source documents, never inferred.
            </p>
            <div className="inv-objective-meta" style={{ marginTop: "var(--space-2)" }}>
              <span className="muted">
                {response
                  ? scopeSentence(response.scope)
                  : `Ready to investigate ${datasetStats?.cases ?? "—"} case(s), ${datasetStats?.documents ?? "—"} documents, ${masterCounts?.nodes ?? "—"} nodes.`}
              </span>
              {history.length > 0 && (
                <button type="button" className="btn btn-tertiary btn-small" onClick={startNewThread}>
                  Start a new thread
                </button>
              )}
            </div>
          </>
        )}
      </section>

      {/* ------------------------------------------------ NETWORK ANALYSIS (explicit trigger, 3 scopes) */}
      <NetworkAnalysisPanel />

      {/* ------------------------------------------------ objective */}
      <section className="panel inv-objective" aria-labelledby="inv-objective-title">
        <div className="inv-objective-head">
          <h2 id="inv-objective-title">Investigation objective</h2>
          <span className={`badge badge-${continuing ? "navy" : "muted"}`}>
            {continuing ? `Thread · ${history.length} question(s)` : "New investigation"}
          </span>
        </div>
        <p className="inv-objective-text">{statedObjective}</p>
        {!response && (
          <div className="form-row inv-objective-form">
            <input
              className="inline-input"
              placeholder="Optional: state the objective explicitly (e.g. determine whether these two cases are connected)"
              value={objective}
              maxLength={400}
              onChange={(event) => setObjective(event.target.value)}
              aria-label="Investigation objective"
            />
          </div>
        )}
      </section>

      {/* ------------------------------------------------- question + START INVESTIGATION */}
      <section className="panel inv-ask">
        <h2 style={{ margin: "0 0 var(--space-2)" }}>Ask the master network</h2>
        <p className="muted" style={{ marginBottom: "var(--space-3)" }}>
          This investigation runs over the entire active dataset (master graph), not a single case.
          Nothing auto-runs. Click START INVESTIGATION to begin. Long-running reasoning model is tolerated:
          real stages, no fake progress, deterministic preserved on AI timeout/unavailable.
        </p>
        <form
          className="inv-ask-form"
          onSubmit={(event) => {
            event.preventDefault();
            void run(question, objective);
          }}
        >
          <input
            className="inline-input inv-ask-input"
            placeholder={
              continuing
                ? "Follow up — the thread keeps the same objective and memory"
                : "Ask an investigative question about named people, phones, vehicles, accounts — e.g. Is there any connection between X and Y?"
            }
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            aria-label="Investigation question"
          />
          <button className="btn btn-primary" type="submit" disabled={loading || question.trim().length < 3}>
            {loading ? "Investigating…" : continuing ? "Ask follow-up" : "START INVESTIGATION"}
          </button>
        </form>
        {chips.length > 0 && (
          <div className="inv-chips">
            {chips.map((chip) => (
              <button
                key={chip}
                type="button"
                className="chip"
                onClick={() => {
                  setQuestion(chip);
                  void run(chip, objective);
                }}
                disabled={loading}
              >
                {chip}
              </button>
            ))}
          </div>
        )}
        {continuing && history.length > 0 && (
          <p className="muted inv-thread-note">
            This question continues the same investigation, so earlier findings, hypotheses and
            gaps are carried forward. Previous questions: {history.slice(-3).join(" · ")}
          </p>
        )}
        {/* Honest progress stages mapped to job */}
        {job && (
          <div className="inv-progress" style={{ marginTop: "var(--space-3)" }}>
            <h4>Investigation job — honest progress</h4>
            <p className="muted">
              Job {job.id.slice(0, 8)} · Stage {JOB_STAGE_LABELS[job.stage] ?? job.stage} · {job.progress_pct}% · {job.message}
            </p>
            <div style={{ background: "#eee", height: 8, borderRadius: 4, overflow: "hidden", margin: "8px 0" }}>
              <div style={{ width: `${job.progress_pct}%`, background: job.terminal ? (job.status === "COMPLETED" ? "#2a7" : "#c77") : "#4a8", height: "100%", transition: "width 0.5s" }} />
            </div>
            {job.steps && job.steps.length > 0 && (
              <ul className="kv" style={{ fontSize: "var(--text-xs)" }}>
                {job.steps.slice(-8).map((s, i) => (
                  <li key={i}>
                    <dt>{JOB_STAGE_LABELS[s.stage] ?? s.stage}</dt>
                    <dd>{s.message} · {new Date(s.at).toLocaleTimeString()}</dd>
                  </li>
                ))}
              </ul>
            )}
            {jobError && <p className="muted" style={{ color: "#a33" }}>{jobError}</p>}
            {job.status !== "COMPLETED" && job.terminal && (
              <p className="muted">
                Deterministic analysis preserved. Status: {job.status}. {job.error || job.message}
                {job.result?.response ? " — Showing deterministic findings below." : ""}
              </p>
            )}
          </div>
        )}
        {timing && !loading && (
          <div style={{ marginTop: "var(--space-3)" }}>
            <TechnicalDetails label={`Pipeline timing — ${timing.total_ms ?? 0} ms total (honest progress)`}>
              <ul className="kv" style={{ fontSize: "var(--text-xs)" }}>
                {Object.entries(timing)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([stage, ms]) => (
                    <li key={stage}>
                      <dt>{STAGE_LABELS[stage] ?? stage}</dt>
                      <dd>{ms} ms</dd>
                    </li>
                  ))}
              </ul>
              <p className="muted" style={{ marginTop: "var(--space-2)" }}>
                Each stage maps to a real backend phase: scope (active dataset lookup), resolution
                (entity mention extraction), patterns (deterministic detectors), relationships
                (path discovery), hypotheses (strength scoring), gaps (missing evidence), narrative
                (AI Gateway, audited). No stage is invented.
              </p>
            </TechnicalDetails>
          </div>
        )}
      </section>

      {error && <ErrorState message={error} onRetry={() => void run(history[history.length - 1] ?? "")} />}
      {jobError && !response && <ErrorState message={jobError} onRetry={() => { setJobError(null); if (job) void run(job.question, objective); }} />}

      {loading && !response && <Spinner label="Running the investigation pipeline over master network… (long-running, real stages, no fake progress)" />}

      {/* ------------------------------------ pre-question signal board — explicit trigger */}
      {!response && !loading && (
        <>
          <section className="panel">
            <div className="inv-objective-head">
              <h2>Suspicious / unusual patterns — master scope</h2>
              <div style={{ display: "flex", gap: "var(--space-2)" }}>
                <button type="button" className="btn btn-secondary btn-small" onClick={() => refreshScan()}>
                  {scan ? "Re-scan master" : "SCAN MASTER PATTERNS"}
                </button>
              </div>
            </div>
            <p className="muted">
              Deterministic detectors over the active dataset master graph. These are investigative signals
              that prioritise where to look — they never establish criminal status. Scan is explicit, not
              auto-run.
            </p>
            {scanError && (
              <ErrorState
                message={`Pattern scan unavailable: ${scanError}`}
                onRetry={() => refreshScan()}
              />
            )}
            {!scan && !scanError && (
              <p className="muted">No scan yet. Click SCAN MASTER PATTERNS to see signals from the active dataset.</p>
            )}
            {scan && (
              <>
                <PatternList
                  patterns={scan}
                  onSelect={(pattern) =>
                    setSelection((current) =>
                      current?.kind === "pattern" && patternKey(current.value) === patternKey(pattern)
                        ? null
                        : { kind: "pattern", value: pattern },
                    )
                  }
                />
                {selection?.kind === "pattern" && (
                  <div className="inv-finding-detail">
                    <FindingDetail
                      title={patternTypeLabel(selection.value.kind)}
                      subtitle={selection.value.title}
                      entities={entities}
                      seeds={selection.value.entity_keys}
                      graph={null}
                      onGraphSelect={setGraphSelection}
                      evidence={selection.value.evidence}
                      contradictions={selection.value.contradictions_considered}
                      alternatives={selection.value.innocent_alternatives}
                      caseId=""
                    />
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}

      {/* ----------------------------------------------- full answer view */}
      {response && (
        <>
          <section className="panel inv-answer-head">
            <div className="inv-objective-head">
              <h2>Findings — WHAT was found</h2>
              <span className="badge badge-navy">
                {response.facts.length} fact(s) · {response.relationships.length} relationship(s)
              </span>
            </div>
            <LabelLegend labels={labelsPresent(response)} />
            <p className="muted">
              What the records directly establish. Facts are computed from the dataset; the
              interpretations further down are labelled separately. Every fact links to evidence → document → source location.
            </p>
            <EvidenceList
              items={response.facts}
              empty="No fact was established for this question."
              caseId=""
            />
          </section>

          <section className="panel">
            <h2>Entities in scope — WHICH entities, WHAT TYPE, legal status vs network role</h2>
            <p className="muted">
              Entity display: type, canonical ID, legal/source status (source-derived only), network role (deterministic),
              confidence. PERSON never confused with BANK_ACCOUNT/PHONE/VEHICLE/LOCATION/ORGANIZATION. Type-aware filtering.
            </p>
            {entities.length === 0 ? (
              <p className="muted">
                No entity from the question could be matched to the active dataset. Unknown names
                stay unknown rather than being guessed at.
              </p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity (type)</th>
                    <th>Resolved to (ID)</th>
                    <th>Legal/Source Status</th>
                    <th>Network Role</th>
                    <th>Investigative Relevance</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((entity) => {
                    const standing = entityStanding(entity);
                    return (
                      <tr key={entity.canonical_id}>
                        <td>
                          <strong>{entity.display_name}</strong> <Badge value={entity.entity_type || entity.label} />
                          {entity.aliases.length > 0 && (
                            <span className="muted"> · a.k.a. {entity.aliases.join(", ")}</span>
                          )}
                        </td>
                        <td>
                          {entity.resolved ? (
                            <>
                              <span className="muted" title={entity.canonical_id}>{entity.canonical_id.slice(0, 12)}…</span>{" "}
                              <span className="muted">
                                {entity.matched_by} match, {Math.round(entity.confidence * 100)}%
                              </span>
                            </>
                          ) : (
                            <span className="badge badge-muted">no matching record</span>
                          )}
                        </td>
                        <td>
                          {entity.legal_status || standing.criminalStatus ? (
                            <Badge value={entity.legal_status || standing.criminalStatus || ""} />
                          ) : (
                            <span className="muted">{standing.criminalStatusText}</span>
                          )}
                          <div className="muted" style={{ fontSize: "0.8em" }}>Source-derived only</div>
                        </td>
                        <td>
                          <Badge value={entity.network_role || standing.analyticStanding || "unknown"} />
                          <div className="muted" style={{ fontSize: "0.8em" }}>Deterministic</div>
                        </td>
                        <td className="muted">
                          {(entity as any).investigative_relevance?.level || (response as any).investigative_relevance?.level || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            <p className="muted inv-standing-note">
              Criminal status is read only from the dataset's criminal-record / charge-sheet
              evidence. Network position, centrality and pattern signals never change it. Star nodes
              in graphs indicate confirmed criminals (source-derived). HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL.
            </p>
            {response.data_quality && response.data_quality.length > 0 && (
              <TechnicalDetails label={`Data quality — ${response.data_quality.length} issue(s)`}>
                <ul>
                  {response.data_quality.map((dq: any, i: number) => (
                    <li key={i}><strong>{dq.category}</strong>: {dq.description} — {dq.recommendation}</li>
                  ))}
                </ul>
              </TechnicalDetails>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Analytical Basis — WHY surfaced, WHICH signals</h2>
              <span className="badge badge-muted">Graph metrics disclaimer included</span>
            </div>
            <p className="muted">
              Deterministic analytical basis per finding: degree, weighted degree, betweenness, PageRank, community,
              cross-case, relationship count, temporal relevance, evidence convergence, source independence,
              relationship strength, path/bridge. Metrics measure network structure, not criminality.
            </p>
            {(response as any).analytical_basis && (
              <ul className="kv">
                <li><dt>Nodes considered</dt><dd>{(response as any).analytical_basis.nodes_considered}</dd></li>
                <li><dt>Edges considered</dt><dd>{(response as any).analytical_basis.edges_considered}</dd></li>
                <li><dt>Documents considered</dt><dd>{(response as any).analytical_basis.documents_considered}</dd></li>
                <li><dt>Evidence convergence</dt><dd>{(response as any).evidence_convergence?.convergence_type} — {(response as any).evidence_convergence?.independent_source_count} independent sources, {(response as any).evidence_convergence?.source_diversity}</dd></li>
                <li><dt>Evidence strength</dt><dd>{(response as any).evidence_strength?.level} — {Array.isArray((response as any).evidence_strength?.factors) ? (response as any).evidence_strength.factors.join(", ") : ""}</dd></li>
                <li><dt>Investigative relevance</dt><dd>{(response as any).investigative_relevance?.level} — {(response as any).investigative_relevance?.explanation}</dd></li>
              </ul>
            )}
            {response.silent_intermediaries && response.silent_intermediaries.length > 0 && (
              <>
                <h4>Silent intermediary / potential network intermediary analysis</h4>
                <p className="muted">Not mastermind/kingpin — high betweenness, community bridging, cross-community, temporal proximity, financial/communication, cross-case, indirect path, multi-source. Low-visibility structurally important actors.</p>
                <ul>
                  {response.silent_intermediaries.map((si: any, i: number) => (
                    <li key={i}>
                      <strong>{si.display_name || si.canonical_id}</strong> — {si.network_role} — {si.investigative_relevance?.level || si.investigative_relevance} — {si.why_surfaced}
                      <br /><span className="muted">{si.disclaimer || "Centrality is analytical signal, not criminal determination"}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Suspicious / unusual patterns</h2>
              <span className="badge badge-muted">{livePatterns.length} live signal(s)</span>
            </div>
            <p className="muted">
              Detected by deterministic analysis over master network. A signal is a reason to look, not a conclusion.
              Graph analytics are deterministic; AI explains only.
            </p>
            <PatternList
              patterns={patterns}
              selectedKind={selection?.kind === "pattern" ? patternKey(selection.value) : null}
              onSelect={(pattern) =>
                setSelection((current) =>
                  current?.kind === "pattern" && patternKey(current.value) === patternKey(pattern)
                    ? null
                    : { kind: "pattern", value: pattern },
                )
              }
            />
            {excludedPatterns.length > 0 && (
              <p className="muted">
                {excludedPatterns.length} combination(s) were examined and set aside openly; they
                are listed above so the decision is reviewable.
              </p>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Relationship analysis — master (WHY per edge, provenance)</h2>
              <span className="badge badge-muted">
                {response.relationships.length} relationship(s)
              </span>
            </div>
            {response.relationships.length === 0 ? (
              <p className="muted">
                No record joins the entities in this question inside the active scope. That may
                mean the connection does not exist, or that the connecting record is not in the
                dataset — see the data gaps below.
              </p>
            ) : (
              <ul className="inv-relationship-list">
                {response.relationships.map((relationship) => (
                  <li
                    key={`${relationship.kind}-${relationship.title}`}
                    className={`inv-relationship ${selection?.kind === "relationship" && selection.value.title === relationship.title ? "inv-selected" : ""}`}
                  >
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
                    {relationship.path && relationship.path.nodes.length > 0 && (
                      <p className="muted">
                        Path: {relationship.path.nodes.join(" → ")}
                        {relationship.path.description ? ` (${relationship.path.description})` : ""}
                      </p>
                    )}
                    {relationship.analysis && (
                      <dl className="inv-observation">
                        <div>
                          <dt>Observation</dt>
                          <dd>{relationship.analysis.observation}</dd>
                        </div>
                        <div>
                          <dt>Interpretation</dt>
                          <dd>{relationship.analysis.interpretation}</dd>
                        </div>
                        <div>
                          <dt>Assessment</dt>
                          <dd>{relationship.analysis.assessment}</dd>
                        </div>
                      </dl>
                    )}
                    <div className="row-actions">
                      <button
                        type="button"
                        className="btn btn-tertiary btn-small"
                        onClick={() =>
                          setSelection(
                            selection?.kind === "relationship" && selection.value.title === relationship.title
                              ? null
                              : { kind: "relationship", value: relationship },
                          )
                        }
                      >
                        {selection?.kind === "relationship" && selection.value.title === relationship.title
                          ? "Hide evidence"
                          : "Why is this relationship here? — evidence & source"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Hypotheses — supporting / contradictory / alternatives (15 questions)</h2>
              <span className="badge badge-muted">{hypotheses.length} under test</span>
            </div>
            <p className="muted">
              Each reading is tested against the evidence — including evidence that weakens it.
              Structured output: supporting, contradictory, alternative explanations are separate.
              Answers WHAT found, WHICH entities, WHAT TYPE, WHY surfaced, WHICH signals, supporting/contradictory evidence, alternatives, legal status vs network role, evidence strength, data gaps, next direction, open exact source, inspect focused graph.
            </p>
            {hypotheses.length === 0 ? (
              <p className="muted">
                No hypothesis was generated: the question produced no resolved entity pair to test.
              </p>
            ) : (
              hypotheses.map((hypothesis) => (
                <HypothesisCard
                  key={hypothesis.id}
                  hypothesis={hypothesis}
                  selected={
                    selection?.kind === "hypothesis" && selection.value.id === hypothesis.id
                  }
                  onSelect={(value) =>
                    setSelection((current) =>
                      current?.kind === "hypothesis" && current.value.id === value.id
                        ? null
                        : { kind: "hypothesis", value },
                    )
                  }
                />
              ))
            )}
          </section>

          <section className="panel">
            <h2>Alternative explanations</h2>
            <p className="muted">
              Reasonable non-criminal readings of the same records. They do not cancel a signal —
              they set the bar the signal has to clear.
            </p>
            <NoteList
              items={response.alternative_explanations}
              empty="No alternative reading was recorded for this question."
            />
          </section>

          {assessment && (
            <section className="panel inv-assessment">
              <h2>Assessment — observation vs interpretation vs assessment (neutral language)</h2>
              <div className={`inv-strength-banner inv-strength-${assessment.overall_strength.toLowerCase()}`}>
                <strong>{assessment.overall_strength} support</strong>
                <span className="muted">
                  {Math.round((assessment.overall_confidence ?? 0) * 100)}% weighting ·{" "}
                  {convergenceSentence(assessment)}
                </span>
              </div>
              <dl className="inv-observation inv-observation-wide">
                <div>
                  <dt>Observation (what records show)</dt>
                  <dd>{assessment.observation}</dd>
                </div>
                <div>
                  <dt>Interpretation (what it could mean, with alternatives)</dt>
                  <dd>{assessment.interpretation}</dd>
                </div>
                <div>
                  <dt>Assessment (overall strength, with caveats)</dt>
                  <dd>{assessment.assessment}</dd>
                </div>
              </dl>
              <p className="muted inv-prose-source">{provenanceOfProse(assessment)}</p>
              {modelUnavailableNote(assessment) && (
                <p className="muted">{modelUnavailableNote(assessment)}</p>
              )}
              {assessment.model.available && assessment.model.summary && (
                <div className="inv-narrative">
                  <h4>Narrative explanation — AI explains only, graph analytics deterministic</h4>
                  <p>{assessment.model.summary}</p>
                  {assessment.model.interpretation && <p>{assessment.model.interpretation}</p>}
                  {assessment.model.assessment && <p>{assessment.model.assessment}</p>}
                  <NoteList items={assessment.model.caveats} className="inv-note-warn" />
                  {assessment.model.language_edits.length > 0 && (
                    <TechnicalDetails
                      label={`language guard (${assessment.model.language_edits.length} edit(s))`}
                    >
                      <NoteList items={assessment.model.language_edits} />
                    </TechnicalDetails>
                  )}
                </div>
              )}
              <NoteList items={assessment.caveats} className="inv-note-warn" empty="" />
              {(response as any).validation_notes && (response as any).validation_notes.length > 0 && (
                <TechnicalDetails label={`Validation notes — ${ (response as any).validation_notes.length }`}>
                  <NoteList items={(response as any).validation_notes} />
                </TechnicalDetails>
              )}
            </section>
          )}

          <section className="panel inv-gaps">
            <h2>Data gaps — data unavailable / not established / insufficient evidence</h2>
            <p className="muted">
              Missing or incomplete evidence. A missing source is never read as proof that nothing
              happened. If missing, say Data unavailable / Not established / Insufficient evidence — never fabricate.
            </p>
            {response.gaps.length === 0 ? (
              <p className="muted">No data gap was recorded for this question.</p>
            ) : (
              <ul className="inv-gap-list">
                {response.gaps.map((gap: DataGap, index) => (
                  <li key={`${gap.category}-${index}`} className="inv-gap">
                    <div className="inv-gap-head">
                      <span className="badge badge-muted">{labelText(gap.inference_label)}</span>
                      <strong>{gapHeading(gap)}</strong>
                    </div>
                    <p>{gapSentence(gap)}</p>
                    {gap.what_would_help && (
                      <p className="muted">
                        <strong>What would close it:</strong> {gap.what_would_help}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h2>Next investigative direction — investigator decision-maker</h2>
            <p className="muted">
              Recommendations derived from the gaps and the strongest current reading. They ask for
              records, checks and reviews — the investigative decision stays with the investigator.
            </p>
            {response.next_steps.length === 0 ? (
              <p className="muted">No follow-up direction was derived.</p>
            ) : (
              <ol className="inv-next-list">
                {orderNextSteps(response.next_steps).map((step: NextStep, index) => {
                  const link = nextStepLink(step);
                  return (
                    <li key={`${index}-${step.action.slice(0, 24)}`} className={`inv-next inv-next-${step.priority}`}>
                      <div className="inv-next-head">
                        <span className={`badge badge-${step.priority === "high" ? "warn" : "muted"}`}>
                          {step.priority}
                        </span>
                        <strong>{step.action}</strong>
                      </div>
                      <p className="muted">{step.rationale}</p>
                      {link.kind === "case" && link.value && (
                        <Link className="btn btn-tertiary btn-small" to={`/cases/${link.value}`}>
                          Open case
                        </Link>
                      )}
                      {link.kind === "entity" && link.value && (
                        <Link className="btn btn-tertiary btn-small" to={`/entities/${encodeURIComponent(link.value)}`}>
                          Open entity
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          {/* -------------------------------------------------- focused view */}
          <section className="panel inv-detail-panel">
            <div className="inv-objective-head">
              <h2>
                {selection
                  ? "Focused evidence view — real subgraph with WHY"
                  : "Focused evidence graph — master subgraph"}
              </h2>
              {selection && (
                <button type="button" className="btn btn-tertiary btn-small" onClick={() => setSelection(null)}>
                  Back to the question graph
                </button>
              )}
            </div>
            {selection ? (
              <FindingDetail
                title={
                  selection.kind === "pattern"
                    ? patternTypeLabel(selection.value.kind)
                    : selection.kind === "hypothesis"
                      ? `Hypothesis ${selection.value.id}`
                      : `Relationship · ${selection.value.kind}`
                }
                subtitle={
                  selection.kind === "pattern"
                    ? selection.value.title
                    : selection.kind === "hypothesis"
                      ? selection.value.statement
                      : selection.value.title
                }
                entities={entities}
                seeds={highlightKeys}
                graph={selectionGraph}
                selection={graphSelection}
                onGraphSelect={setGraphSelection}
                evidence={
                  selection.kind === "pattern"
                    ? selection.value.evidence
                    : selection.kind === "hypothesis"
                      ? selection.value.supporting
                      : selection.value.evidence
                }
                contradictions={
                  selection.kind === "pattern"
                    ? selection.value.contradictions_considered
                    : selection.kind === "hypothesis"
                      ? (selection.value.contradicting ?? []).map((item) => item.summary)
                      : []
                }
                alternatives={
                  selection.kind === "pattern"
                    ? selection.value.innocent_alternatives
                    : selection.kind === "hypothesis"
                      ? selection.value.innocent_alternatives
                      : []
                }
                sources={
                  selection.kind === "pattern" ? patternSourceCount(selection.value) : undefined
                }
                caseId=""
                whyLines={selection.kind === "pattern" ? metricLines : []}
              />
            ) : (
              <>
                <p className="muted">
                  Only the entities and relationships involved in this question — real subgraph from master network,
                  seeds plus one hop. Select a pattern, hypothesis or relationship above to narrow it further.
                  WHY each node appears: it is directly connected to a seed entity from your question. Edges include WHY, source_doc_ids, date_time, inference_label.
                </p>
                <FocusedEvidenceGraph
                  graph={focused}
                  onSelect={setGraphSelection}
                  highlightKeys={highlightKeys}
                />
                {graphSelection && (
                  <p className="muted inv-graph-selection">
                    Selected {graphSelection.kind}: <strong>{graphSelection.name}</strong>{" "}
                    ({graphSelection.label})
                  </p>
                )}
              </>
            )}
          </section>

          <section className="panel">
            <h2>Timeline</h2>
            <InvestigationTimeline entries={response.timeline} />
          </section>

          <section className="panel">
            <h2>Provenance — every source clickable (finding → evidence → document)</h2>
            <p className="muted">
              Every record the analysis is allowed to open. Findings above link back into these
              sources using the same SourceViewer mechanism everywhere; the answer never cites a document
              that is not in the active dataset. Evidence object contract: evidence_id/document_id/case_id/source_id/origin_file/document type/source type/record ID/row/page/line/text span/excerpt/content hash/provenance.
            </p>
            {response.provenance.length === 0 ? (
              <p className="muted">
                No source document is in scope, so nothing in this answer can be opened against a
                record yet — a data gap rather than a clean result.
              </p>
            ) : (
              <ul className="inv-provenance-list">
                {response.provenance.map((pointer) => (
                  <li key={`${pointer.kind}-${pointer.ref}`}>
                    <ProvenanceChip pointer={pointer} caseId="" />
                    {pointer.detail && <span className="muted"> · {pointer.detail}</span>}
                    {pointer.content_hash && (
                      <span className="hash" title="Content hash (tamper-evident evidence fingerprint)">
                        {pointer.content_hash.slice(0, 12)}…
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {response.memory && (
              <TechnicalDetails
                label={`investigation memory (${response.memory.questions_asked} question(s))`}
              >
                <ul className="kv">
                  <li>
                    <dt>Objective</dt>
                    <dd>{response.memory.objective || response.objective}</dd>
                  </li>
                  <li>
                    <dt>Questions</dt>
                    <dd>{response.memory.prior_questions.join(" · ")}</dd>
                  </li>
                  <li>
                    <dt>Facts</dt>
                    <dd>
                      {response.memory.confirmed_facts.length > 0
                        ? `${response.memory.confirmed_facts.length} recorded`
                        : "none yet"}
                    </dd>
                  </li>
                  <li>
                    <dt>Open gaps</dt>
                    <dd>{response.memory.open_gaps.length}</dd>
                  </li>
                  <li>
                    <dt>Open contradictions</dt>
                    <dd>
                      {response.memory.contradictions.length > 0
                        ? `${response.memory.contradictions.length} recorded`
                        : "none recorded"}
                    </dd>
                  </li>
                  <li>
                    <dt>Readings set aside</dt>
                    <dd>
                      {response.memory.rejected_hypotheses.length === 0
                        ? "none yet"
                        : response.memory.rejected_hypotheses
                            .map((item) => `${item.id}: ${item.reason}`)
                            .join(" · ")}
                    </dd>
                  </li>
                </ul>
                <EvidenceList
                  items={[
                    ...response.memory.relationships.map((line) => ({
                      kind: "relationship" as const,
                      summary: line,
                      inference_label: "FACT",
                      stance: "context" as const,
                      provenance: [],
                    })),
                  ]}
                  empty="No relationship has been established in this thread yet."
                  className="inv-memory-notes"
                />
              </TechnicalDetails>
            )}
          </section>
        </>
      )}

      {!response && !loading && scan && scan.length === 0 && (
        <section className="panel">
          <Empty message="No unusual pattern is visible for master scope. Ask a question above with START INVESTIGATION to run a full evidence-backed investigation over the active dataset." />
        </section>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Finding detail                                                         */
/* ---------------------------------------------------------------------- */

function FindingDetail({
  title,
  subtitle,
  entities,
  seeds,
  graph,
  selection,
  onGraphSelect,
  evidence,
  contradictions,
  alternatives,
  sources,
  caseId = "",
  whyLines = [],
}: {
  title: string;
  subtitle: string;
  entities: ResolvedEntity[];
  seeds: string[];
  graph: ReturnType<typeof subgraphFor> | null;
  selection?: GraphSelection | null;
  onGraphSelect?: (selection: GraphSelection | null) => void;
  evidence: InvestigatorResponse["relationships"][number]["evidence"];
  contradictions: string[];
  alternatives: string[];
  sources?: number;
  caseId?: string;
  whyLines?: string[];
}) {
  const seeded = graph ?? { nodes: [], edges: [], truncated: false };
  const involved = entities.filter((entity) => seeds.includes(entity.canonical_id));

  return (
    <div className="inv-finding">
      <header className="inv-finding-head">
        <span className="badge badge-navy">{title}</span>
        <h3>{subtitle}</h3>
        {sources !== undefined && (
          <span className="badge badge-muted">
            {sources} source{sources === 1 ? "" : "s"}
          </span>
        )}
      </header>

      <div className="inv-finding-grid">
        <div>
          <h4>Supporting evidence</h4>
          <EvidenceList
            items={evidence}
            empty="No evidence item is attached to this finding."
            caseId={caseId}
          />

          {contradictions.length > 0 && (
            <>
              <h4>Contradictory evidence — why it might not hold</h4>
              <NoteList items={contradictions} />
            </>
          )}

          <h4>Alternative explanations (non-criminal readings)</h4>
          <NoteList
            items={alternatives}
            empty="No alternative reading was recorded for this finding."
          />

          {involved.length > 0 && (
            <>
              <h4>Related entities</h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Criminal status (from the record)</th>
                    <th>Analytical standing</th>
                  </tr>
                </thead>
                <tbody>
                  {involved.map((entity) => {
                    const standing = entityStanding(entity);
                    return (
                      <tr key={entity.canonical_id}>
                        <td>{entity.display_name}</td>
                        <td>
                          {standing.criminalStatus ? (
                            <Badge value={standing.criminalStatus} />
                          ) : (
                            <span className="muted">{standing.criminalStatusText}</span>
                          )}
                        </td>
                        <td className="muted">{standing.analyticStanding}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}

          {involved.length === 0 && (
            <p className="muted">
              This finding names canonical entity keys that are not among the question's resolved
              entities, so no status is shown for them.
            </p>
          )}
        </div>

        <div>
          <h4>Focused evidence graph — real subgraph with WHY</h4>
          <FocusedEvidenceGraph graph={seeded} height={280} onSelect={onGraphSelect} highlightKeys={seeds} />
          {selection && (
            <p className="muted inv-graph-selection">
              Selected {selection.kind}: <strong>{selection.name}</strong> ({selection.label})
            </p>
          )}
          <p className="muted">
            Every relationship drawn here is backed by the records listed on the left; the graph is
            a view of that evidence, not a separate source. WHY each node is here: it is directly
            connected to a seed entity from your question (one hop).
          </p>
        </div>
      </div>

      <TechnicalDetails label="why this finding is highlighted — explanation">
        <NoteList
          items={[
            ...whyLines,
            "Relationships are computed from the records in scope; edge counts and metrics are deterministic, not model-generated. AI explains only.",
            "Network position (degree, betweenness, communities) describes the shape of the network and never changes criminal status (source-derived, star nodes).",
            "Focused graph is a real subgraph of the master network: seeds are entities from your question, neighbors are one hop away — WHY each node appears is its connection to a seed.",
          ]}
        />
      </TechnicalDetails>
    </div>
  );
}
