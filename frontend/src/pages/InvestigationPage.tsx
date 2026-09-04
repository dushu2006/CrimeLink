/**
 * Investigation workspace — the explicit, investigator-driven workflow.
 *
 * Eight stages, each a real button that calls one real backend operation.
 * A stage is locked until its prerequisites are COMPLETED; failures are
 * shown as failures with the backend's own error text.  Nothing runs on
 * page load, and the page never claims an operation succeeded when the
 * backend reported otherwise.
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
import { Empty, ErrorState, Spinner } from "../components/Status";

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

const STAGE_STATUS_CLASS: Record<string, string> = {
  PENDING: "badge",
  RUNNING: "badge badge-warn",
  COMPLETED: "badge badge-ok",
  FAILED: "badge badge-err",
};

export default function InvestigationPage() {
  const { caseId = "" } = useParams();
  const [stages, setStages] = useState<InvestigationStage[] | null>(null);
  const [documents, setDocuments] = useState<{ total: number; processed: number; pending: number } | null>(null);
  const [graphBackend, setGraphBackend] = useState<string>("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [runningKey, setRunningKey] = useState<string | null>(null);
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
          // The backend says the stage did NOT complete — say so.
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

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t("investigation.title")}</h1>
          <p className="muted">{t("investigation.subtitle")}</p>
        </div>
        <div className="graph-meta">
          <Link className="btn" to={`/cases/${caseId}/review`}>
            {t("nav.review")}
          </Link>
          <Link className="btn" to={`/cases/${caseId}/graph`}>
            {t("investigation.openGraph")}
          </Link>
        </div>
      </header>

      {error && <ErrorState message={error} />}
      {notice && <p className="badge badge-ok">{notice}</p>}

      <section className="stage-list">
        <h2>{t("investigation.stages")}</h2>
        {stages === null && <Spinner />}
        {stages?.map((stage) => {
          const busy = runningKey === stage.key;
          const locked = !stage.runnable && stage.status !== "COMPLETED";
          return (
            <article key={stage.key} className={`stage-row ${busy ? "running" : ""}`}>
              <div className="stage-info">
                <h3>
                  {stage.stage}. {stage.label}
                </h3>
                <p className="muted">{stageSummary(stage) || `Requires: ${stage.requires.map((r) => `stage ${r}`).join(", ") || "—"}`}</p>
                {stage.attempt_count > 0 && (
                  <p className="muted">
                    attempts: {stage.attempt_count}
                    {stage.duration_ms !== null && ` · last run ${stage.duration_ms} ms`}
                  </p>
                )}
              </div>
              <div className="stage-actions">
                <span className={STAGE_STATUS_CLASS[stage.status] ?? "badge"}>
                  {busy ? "RUNNING…" : stage.status}
                </span>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy || locked}
                  title={locked ? `Locked until stage ${stage.blocked_by.join(", ")} completes` : undefined}
                  onClick={() => runStage(stage)}
                >
                  {stage.status === "COMPLETED" ? t("investigation.rerun") : t("investigation.run")}
                </button>
              </div>
            </article>
          );
        })}
      </section>

      <section className="findings-list">
        <h2>{t("investigation.findings")}</h2>
        {documents && documents.pending > 0 && (
          <p className="badge badge-warn">
            {documents.pending} {t("investigation.pendingDocs")}
          </p>
        )}
        {graphBackend && (
          <p className="muted">
            {t("graph.backend")}: {graphBackend}
          </p>
        )}
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
              <span className="badge">{finding.status}</span>
            </header>
            <p>{finding.narrative}</p>
            <p className="muted">{finding.reason}</p>
            <p className="muted">
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
                <button type="button" className="btn" onClick={() => review(finding, "CONFIRMED")}>
                  {t("investigation.confirm")}
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => review(finding, "DISMISSED")}
                >
                  {t("investigation.dismiss")}
                </button>
              </footer>
            ) : (
              finding.review_note && <p className="muted">note: {finding.review_note}</p>
            )}
          </article>
        ))}
      </section>
    </div>
  );
}
