/**
 * Investigation workspace — the explicit, investigator-driven workflow.
 *
 * Eight stages divided into:
 *   1. Preparation & Ingestion strip (Stages 1–5): compact status badges, one-line
 *      summaries, and collapsed technical details.
 *   2. Graph Analysis hero panel (Stages 6–8): primary "Analyze this case's network"
 *      action, AI narrative summary, and reviewable evidence-backed findings.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  caseFindings,
  investigationState,
  reviewFinding,
  runInvestigationStage,
  type Finding,
  type InvestigationStage,
} from "../api/client";
import { t } from "../i18n";
import { stageSummary } from "../lib/investigation";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { TechnicalDetails } from "../components/TechnicalDetails";

/** One evidence entry of a finding, rendered honestly by its kind. */
function FindingEvidence({ item }: { item: Record<string, unknown> }) {
  const kind = String(item["kind"] ?? "unknown");
  if (kind === "relationship") {
    const rel = String(item["rel_type"] ?? "").replaceAll("_", " ").toLowerCase();
    const a = String(item["source"] ?? item["from_account"] ?? "");
    const b = String(item["target"] ?? item["to_account"] ?? "");
    const docs = Array.isArray(item["source_doc_ids"]) ? (item["source_doc_ids"] as string[]) : [];
    return (
      <li>
        {rel && <span>{rel}</span>}
        {a && b && (
          <span>
            {" — "}
            <code>{String(a).slice(0, 12)}</code> → <code>{String(b).slice(0, 12)}</code>
          </span>
        )}
        {typeof item["transfer_count"] === "number" && (
          <span> · {String(item["transfer_count"])} transfers</span>
        )}
        {typeof item["total_amount"] === "number" && (
          <span> · total ₹{String(item["total_amount"])}</span>
        )}
        {docs.length > 0 && (
          <span>
            {" · "}
            {docs.map((docId, index) => (
              <span key={docId}>
                {index > 0 && ", "}
                <Link to={`/documents/${docId}`}>{docId.slice(0, 8)}</Link>
              </span>
            ))}
          </span>
        )}
      </li>
    );
  }
  if (kind === "analysis") {
    return (
      <li>
        analysis: {String(item["method"] ?? "")}
        {typeof item["betweenness"] === "number" && ` · betweenness ${String(item["betweenness"])}`}
        {typeof item["degree"] === "number" && ` · degree ${String(item["degree"])}`}
        {typeof item["rank_in_case"] === "number" && ` · rank ${String(item["rank_in_case"])}`}
      </li>
    );
  }
  return <li>{kind}</li>;
}

export default function InvestigationPage() {
  const { caseId = "" } = useParams();
  const [stages, setStages] = useState<InvestigationStage[] | null>(null);
  const [documents, setDocuments] = useState<{ total: number; processed: number; pending: number } | null>(null);
  const [graphBackend, setGraphBackend] = useState<string>("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [runningKey, setRunningKey] = useState<string | null>(null);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => {
    investigationState(caseId)
      .then((state) => {
        setStages(state.stages);
        setDocuments(state.documents);
        setGraphBackend(String(state.graph_backend));
      })
      .catch((err: Error) => setError(err.message));
    caseFindings(caseId)
      .then((res) => setFindings(res.items))
      .catch(() => setFindings(null));
  }, [caseId]);

  useEffect(refresh, [refresh]);

  const runStage = useCallback(
    async (stage: InvestigationStage) => {
      setRunningKey(stage.key);
      setError(null);
      setNotice(null);
      try {
        const result = await runInvestigationStage(caseId, stage.key);
        if (result.status === "COMPLETED") {
          setNotice(`${stage.label}: completed.`);
        } else {
          setError(`${stage.label}: ${result.status}`);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(`${stage.label}: ${message}`);
      } finally {
        setRunningKey(null);
        refresh();
      }
    },
    [caseId, refresh],
  );

  /** Runs the centerpiece analysis pipeline: stages 6, 7, and 8. */
  const runAnalysisPipeline = useCallback(async () => {
    if (!stages || pipelineRunning) return;
    setPipelineRunning(true);
    setError(null);
    setNotice(null);
    try {
      const analysisStages = stages.filter((s) => s.stage >= 6);
      const allDone = analysisStages.every((s) => s.status === "COMPLETED");
      const toRun = allDone
        ? analysisStages
        : analysisStages.filter((s) => s.status !== "COMPLETED");

      for (const stage of toRun) {
        setRunningKey(stage.key);
        const res = await runInvestigationStage(caseId, stage.key);
        if (res.status !== "COMPLETED") {
          setError(`${stage.label}: ${res.status}`);
          break;
        }
      }
      setNotice("Case network analysis and findings generation complete.");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setRunningKey(null);
      setPipelineRunning(false);
      refresh();
    }
  }, [caseId, stages, pipelineRunning, refresh]);

  const review = useCallback(
    async (finding: Finding, decision: "CONFIRMED" | "DISMISSED") => {
      try {
        await reviewFinding(caseId, finding.id, decision);
        refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [caseId, refresh],
  );

  const prepStages = stages?.slice(0, 5) ?? [];
  const analysisStages = stages?.slice(5) ?? [];
  const stage7 = stages?.find((s) => s.stage === 7);
  const stage7Detail = stage7?.detail as Record<string, unknown> | undefined;
  const aiAvailable = stage7Detail?.ai_available !== false;
  const aiUnavailableMsg = (stage7Detail?.message as string | undefined) ?? (stage7Detail?.reason as string | undefined);
  const aiAssessedFindings = findings?.filter((f) => f.method === "ai_assisted") ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t("investigation.title")}</h1>
          <p className="muted">{t("investigation.subtitle")}</p>
        </div>
        <div className="row-actions">
          <Link className="btn btn-secondary" to={`/cases/${caseId}/review`}>
            {t("nav.review")}
          </Link>
          <Link className="btn btn-secondary" to={`/cases/${caseId}/graph`}>
            {t("investigation.openGraph")}
          </Link>
        </div>
      </header>

      {error && <ErrorState message={error} />}
      {notice && <p className="badge badge-ok" style={{ marginBottom: "var(--space-3)" }}>{notice}</p>}

      {/* --- Preparation & Ingestion Strip (Stages 1–5) --- */}
      <section className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2>Preparation &amp; Ingestion</h2>
          {documents && (
            <span className="hint">
              {documents.processed}/{documents.total} documents complete
              {documents.pending > 0 && ` (${documents.pending} pending)`}
            </span>
          )}
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          Document parsing, text extraction, entity resolution, and initial graph ingestion.
        </p>

        {stages === null ? (
          <Spinner />
        ) : (
          <div className="prep-strip">
            {prepStages.map((stage) => {
              const busy = runningKey === stage.key;
              const locked = !stage.runnable && stage.status !== "COMPLETED";
              return (
                <article key={stage.key} className={`prep-stage-card ${busy ? "running" : ""}`}>
                  <div className="prep-stage-head">
                    <Badge value={busy ? "RUNNING" : stage.status} />
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      disabled={busy || locked || pipelineRunning}
                      title={locked ? `Locked until stage ${stage.blocked_by.join(", ")} completes` : undefined}
                      onClick={() => runStage(stage)}
                    >
                      {stage.status === "COMPLETED" ? t("investigation.rerun") : t("investigation.run")}
                    </button>
                  </div>
                  <strong style={{ fontSize: "var(--text-sm)" }}>
                    {stage.stage}. {stage.label}
                  </strong>
                  <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--muted)" }}>
                    {stageSummary(stage) || `Requires: ${stage.requires.map((r) => `stage ${r}`).join(", ") || "—"}`}
                  </p>
                  <TechnicalDetails label={`Stage ${stage.stage} details`}>
                    <div style={{ fontSize: "var(--text-xs)" }}>
                      {stage.attempt_count > 0 && (
                        <p style={{ margin: "0 0 4px" }}>
                          Attempts: {stage.attempt_count}
                          {stage.duration_ms !== null && ` · Duration: ${stage.duration_ms} ms`}
                        </p>
                      )}
                      {stage.error && (
                        <p style={{ margin: "0 0 4px", color: "var(--bad)" }}>
                          Error: {stage.error}
                        </p>
                      )}
                      <pre style={{ margin: 0, maxHeight: 140, overflow: "auto" }}>
                        {JSON.stringify(stage.detail, null, 2)}
                      </pre>
                    </div>
                  </TechnicalDetails>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {/* --- Centerpiece Hero Panel: Graph Analysis (Stages 6–8) --- */}
      <section className="panel">
        <div className="hero-analysis-header">
          <div>
            <h2 style={{ fontSize: "var(--text-lg)", margin: 0 }}>Graph Analysis</h2>
            <p className="muted" style={{ margin: "4px 0 0", fontSize: "var(--text-sm)" }}>
              Network centrality algorithms, AI-assisted reasoning, and evidence-backed investigative findings.
              {graphBackend && ` · Graph store: ${graphBackend}`}
            </p>
            <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)", flexWrap: "wrap" }}>
              {analysisStages.map((s) => (
                <Badge
                  key={s.key}
                  value={runningKey === s.key ? `${s.stage}. ${s.label} (RUNNING)` : `${s.stage}. ${s.label}: ${s.status}`}
                />
              ))}
            </div>
          </div>
          <div>
            <button
              type="button"
              className="btn btn-primary"
              disabled={pipelineRunning || runningKey !== null}
              onClick={runAnalysisPipeline}
            >
              {pipelineRunning
                ? (runningKey ? `Running ${runningKey}…` : "Analyzing case network…")
                : "Analyze this case's network"}
            </button>
          </div>
        </div>

        {/* AI narrative summary banner or honest fallback */}
        {stage7 && stage7.status === "COMPLETED" && (
          <div style={{ marginBottom: "var(--space-4)" }}>
            {!aiAvailable ? (
              <div className="banner" style={{ borderLeft: "3px solid var(--navy)" }}>
                <strong>AI Reasoning Unavailable:</strong>{" "}
                <span className="muted">
                  {aiUnavailableMsg || "No API key configured for reasoning model. Deterministic network analysis and pattern detection active."}
                </span>
              </div>
            ) : aiAssessedFindings.length > 0 ? (
              <div className="banner alert-ok" style={{ borderLeft: "3px solid var(--ok)" }}>
                <strong>AI Network Reasoning Summary:</strong>
                <p style={{ margin: "4px 0 0" }}>
                  {aiAssessedFindings[0].narrative}
                </p>
              </div>
            ) : null}
          </div>
        )}

        <TechnicalDetails label="analysis pipeline details">
          <div style={{ fontSize: "var(--text-xs)" }}>
            {analysisStages.map((s) => (
              <div key={s.key} style={{ marginBottom: "var(--space-2)" }}>
                <strong>Stage {s.stage}: {s.label}</strong> ({s.status})
                {s.duration_ms !== null && ` · ${s.duration_ms} ms`}
                {s.error && <span style={{ color: "var(--bad)" }}> · Error: {s.error}</span>}
                <pre style={{ margin: "2px 0 0", maxHeight: 120, overflow: "auto" }}>
                  {JSON.stringify(s.detail, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        </TechnicalDetails>

        {/* --- Findings List --- */}
        <div style={{ marginTop: "var(--space-5)" }}>
          <h3 style={{ fontSize: "var(--text-md)", margin: "0 0 var(--space-3)" }}>
            Investigative Findings {findings !== null && `(${findings.length})`}
          </h3>

          {findings === null && <Spinner />}
          {findings !== null && findings.length === 0 && (
            <Empty message={t("investigation.noFindings")} />
          )}

          {findings?.map((finding) => (
            <article key={finding.id} className="finding-card">
              <header>
                <h3>{finding.title}</h3>
                <span className={`badge band-${finding.confidence_band.toLowerCase()}`}>
                  {finding.confidence_band} · {Math.round(finding.confidence * 100)}%
                </span>
                <Badge value={finding.status} />
              </header>
              <p style={{ margin: "8px 0 4px" }}>{finding.narrative}</p>
              <p className="muted" style={{ margin: "0 0 4px", fontSize: "var(--text-xs)" }}>{finding.reason}</p>
              <p className="muted" style={{ margin: "0 0 4px", fontSize: "var(--text-xs)" }}>
                method: {finding.method} ·{" "}
                {finding.entity_keys.length > 0 &&
                  `${t("investigation.entities")}: ${finding.entity_keys.length}`}
              </p>
              {finding.evidence.length > 0 && (
                <details className="finding-evidence">
                  <summary>
                    {t("investigation.evidence")} ({finding.evidence.length})
                  </summary>
                  <ul>
                    {finding.evidence.map((ev, index) => (
                      <FindingEvidence key={index} item={ev} />
                    ))}
                  </ul>
                </details>
              )}
              {finding.status === "NEW" ? (
                <footer className="finding-review">
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => review(finding, "CONFIRMED")}
                  >
                    {t("investigation.confirm")}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => review(finding, "DISMISSED")}
                  >
                    {t("investigation.dismiss")}
                  </button>
                </footer>
              ) : (
                finding.review_note && (
                  <p className="muted" style={{ margin: "6px 0 0", fontSize: "var(--text-xs)" }}>
                    note: {finding.review_note}
                  </p>
                )
              )}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
