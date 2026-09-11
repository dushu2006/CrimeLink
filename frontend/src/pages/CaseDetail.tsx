import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, askCaseStream, download, jobSocket, uploadDocument } from "../api/client";
import { t } from "../i18n";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { TechnicalDetails } from "../components/TechnicalDetails";

/** The stage messages the AI stream reports while an answer is produced. */
const AI_PHASE_LABEL: Record<string, string> = {
  started: "Request started…",
  retrieving: "Retrieving case context…",
  generating: "Generating answer…",
  validating: "Validating and attaching evidence…",
  fast_path: "Answering directly…",
};

interface CaseRow {
  id: string;
  case_number: string;
  title: string;
  jurisdiction_id: string;
  status: string;
  document_count: number;
  pending_review_count: number;
  review_sla?: { breached?: number; total?: number };
}

interface DocRow {
  id: string;
  document_type: string;
  filename: string;
  language: string | null;
  size_bytes: number;
  content_hash: string;
  ingestion_status: string;
  ingestion_stage: string | null;
  failure_reason: string | null;
  source_confidence: string;
  quarantined: boolean;
  created_at: string | null;
}

interface JobRow {
  job_id: string;
  doc_id: string;
  status: string;
  stage_name: string | null;
  progress_pct: number;
  total_stages: number;
  error: string | null;
}

interface TimelineEvent {
  at: string | null;
  name: string;
  description: string;
  event_type?: string;
}

const DOC_TYPES = [
  "FIR",
  "CDR",
  "FINANCIAL",
  "SURVEILLANCE",
  "SOCIAL_MEDIA",
  "CRIMINAL_HISTORY",
  "INTEL",
];
const CONFIDENCE = ["VERIFIED", "UNVERIFIED", "ANONYMOUS_TIP", "SYNTHETIC"];

/**
 * A clear, honest explanation for an unavailable AI role, derived from the
 * gateway's machine-readable fallback_reason — in plain investigator language.
 */
function aiUnavailableMessage(fallbackReason: unknown): string {
  const reason = String(fallbackReason ?? "");
  if (reason.startsWith("no_api_key_for_role_")) {
    const aiRole = reason.slice("no_api_key_for_role_".length) || "model";
    return (
      `No API key is configured for the ${aiRole} model. ` +
      `Configure CRIMELINK_AI_${aiRole.toUpperCase()}_API_KEY to enable this feature.`
    );
  }
  if (reason.includes("timeout") || reason.startsWith("invocation_failed:")) {
    return "The AI service did not respond in time. Try again, or ask about fewer entities.";
  }
  if (reason.startsWith("gateway_error:")) {
    return "AI processing failed. An investigator must review this case manually.";
  }
  if (reason === "openai_client_unavailable") {
    return "The AI client library is not installed on the server.";
  }
  if (reason.startsWith("stream_interrupted")) {
    return "The AI provider stopped mid-answer. Try again, or review the case manually.";
  }
  return reason ? `AI is currently unavailable (${reason}).` : "AI is currently unavailable.";
}

/**
 * Pull the human-readable answer out of the model's streaming JSON.
 */
function partialSummary(buffer: string): string {
  const key = buffer.indexOf('"summary"');
  if (key < 0) return buffer.slice(0, 800);
  const colon = buffer.indexOf(":", key + 9);
  if (colon < 0) return buffer.slice(0, 800);
  let i = colon + 1;
  while (i < buffer.length && (buffer[i] === " " || buffer[i] === "\n")) i += 1;
  if (buffer[i] !== '"') return buffer.slice(0, 800);
  let out = "";
  i += 1;
  for (; i < buffer.length; i += 1) {
    const ch = buffer[i];
    if (ch === "\\") {
      const next = buffer[i + 1];
      if (next === undefined) break;
      out += next === "n" ? "\n" : next;
      i += 1;
      continue;
    }
    if (ch === '"') break;
    out += ch;
  }
  return out || buffer.slice(0, 800);
}

export default function CaseDetail() {
  const { caseId = "" } = useParams();
  const [caseRow, setCaseRow] = useState<CaseRow | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [jobs, setJobs] = useState<Record<string, JobRow>>({});
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [docType, setDocType] = useState("FIR");
  const [confidence, setConfidence] = useState("UNVERIFIED");
  const [question, setQuestion] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiResult, setAiResult] = useState<Record<string, unknown> | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiPhase, setAiPhase] = useState<string | null>(null);
  const [aiStreamText, setAiStreamText] = useState("");
  const [aiTransport, setAiTransport] = useState<"stream" | "request">("stream");
  const [liveStatus, setLiveStatus] = useState<"connected" | "polling" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function askAi() {
    const text = question.trim();
    if (!text || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    setAiResult(null);
    setAiStreamText("");
    setAiPhase("started");
    let settled = false;
    let fellBack = false;

    const runPlain = async () => {
      fellBack = true;
      setAiTransport("request");
      setAiPhase("request");
      try {
        const result = await api<Record<string, unknown>>(`/ai/cases/${caseId}/ask`, {
          method: "POST",
          body: JSON.stringify({ question: text }),
        });
        setAiResult(result);
      } catch (err) {
        setAiError((err as Error).message);
      } finally {
        setAiPhase(null);
        setAiBusy(false);
      }
    };

    await askCaseStream(caseId, text, {
      onAck: () => setAiPhase("started"),
      onStage: (event) => setAiPhase(String(event.stage ?? "")),
      onDelta: (piece) => {
        setAiStreamText((previous) => previous + piece);
      },
      onDone: (response) => {
        settled = true;
        setAiResult(response);
        setAiPhase(null);
        setAiBusy(false);
      },
      onError: (event) => {
        settled = true;
        setAiPhase(null);
        setAiBusy(false);
        setAiError(String(event.message ?? "Unable to answer this question."));
      },
      onFallback: () => {
        if (!settled) void runPlain();
      },
    });
    if (!settled && !fellBack) await runPlain();
  }

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      api<CaseRow>(`/cases/${caseId}`),
      api<{ items: DocRow[] }>(`/cases/${caseId}/documents`),
      api<{ items: JobRow[] }>(`/cases/${caseId}/jobs?limit=50`),
      api<{ events: TimelineEvent[] }>(`/cases/${caseId}/timeline?limit=50`),
    ])
      .then(([caseData, docData, jobData, timelineData]) => {
        setCaseRow(caseData);
        setDocs(docData.items);
        setJobs(Object.fromEntries(jobData.items.map((j) => [j.doc_id, j])));
        setTimeline(timelineData.events);
      })
      .catch((err: Error) => setError(err.message));
  }, [caseId]);

  useEffect(load, [load]);

  useEffect(() => {
    return jobSocket(
      caseId,
      () => {
        api<{ items: JobRow[] }>(`/cases/${caseId}/jobs?limit=50`)
          .then((data) => {
            setJobs(Object.fromEntries(data.items.map((j) => [j.doc_id, j])));
            return api<{ items: DocRow[] }>(`/cases/${caseId}/documents`);
          })
          .then((data) => setDocs(data.items))
          .catch(() => undefined);
      },
      (status) => setLiveStatus(status.state),
    );
  }, [caseId]);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await uploadDocument(caseId, file, docType, confidence);
      if (fileRef.current) fileRef.current.value = "";
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !caseRow) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="page">
      {!caseRow ? (
        <Spinner />
      ) : (
        <>
          <header className="page-head">
            <div>
              <h1>{caseRow.case_number}</h1>
              <div className="form-row" style={{ gap: "var(--space-2)", alignItems: "center", marginTop: "var(--space-1)" }}>
                <Badge value={caseRow.status} />
                <span className="muted">·</span>
                <span className="muted">{caseRow.title}</span>
                <span className="muted">·</span>
                <span className="muted">Jurisdiction: {caseRow.jurisdiction_id}</span>
                <span className="muted">·</span>
                <span className="muted">{caseRow.document_count} documents</span>
              </div>
            </div>
            <div className="row-actions">
              <Link className="btn btn-secondary" to={`/cases/${caseId}/investigation`}>
                {t("investigation.workspaceLink")}
              </Link>
              <Link className="btn btn-secondary" to={`/cases/${caseId}/graph`}>
                {t("case.openGraph")}
              </Link>
              <Link className="btn btn-secondary" to={`/cases/${caseId}/review`}>
                {t("case.review")}
                {caseRow.pending_review_count > 0 && (
                  <span className="pill pill-warn">{caseRow.pending_review_count}</span>
                )}
              </Link>
              <button
                className="btn btn-secondary"
                type="button"
                onClick={() =>
                  download(`/cases/${caseId}/export`, `case-brief-${caseRow!.case_number.replace(/\//g, "-")}.pdf`)
                    .catch((err: Error) => setError(err.message))
                }
              >
                {t("case.export")}
              </button>
            </div>
          </header>

          {/* Slim stat chips row */}
          <div style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-4)", flexWrap: "wrap" }}>
            <span className="chip">
              <span className="chip-label">Documents:</span>
              <strong>{docs?.length ?? caseRow.document_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Review Items:</span>
              <strong>{caseRow.pending_review_count}</strong>
            </span>
            <span className="chip">
              <span className="chip-label">Status:</span>
              <Badge value={caseRow.status} />
            </span>
            {caseRow.review_sla?.breached !== undefined && (
              <span className="chip">
                <span className="chip-label">SLA Breached:</span>
                <strong>{caseRow.review_sla.breached}</strong>
              </span>
            )}
          </div>
        </>
      )}

      {liveStatus === "polling" && (
        <div className="banner banner-warn">
          {t("case.livePolling")}
        </div>
      )}

      {/* --- Document Upload --- */}
      <section className="panel">
        <h2>{t("case.upload")}</h2>
        <form className="form-row" onSubmit={upload}>
          <input type="file" ref={fileRef} required />
          <select value={docType} onChange={(e) => setDocType(e.target.value)}>
            {DOC_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
            {CONFIDENCE.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? t("state.loading") : t("case.upload")}
          </button>
        </form>
        <p className="hint">
          Uploaded documents are hashed with SHA-256 and stored write-once; re-ingesting the
          same file yields the same hash.
        </p>
      </section>

      {/* --- Ask AI Panel --- */}
      <section className="panel">
        <h2>Ask AI about this case</h2>
        <p className="hint">
          Questions go through the AI gateway. Only the case subgraph is sent, never the whole
          database. If no model key is configured, the response says so instead of inventing an answer.
        </p>
        <form
          className="form-row"
          onSubmit={(event) => {
            event.preventDefault();
            void askAi();
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What connections appear in this case?"
            style={{ minWidth: 280, flex: 1 }}
          />
          <button className="btn btn-primary" type="submit" disabled={aiBusy}>
            {aiBusy ? t("state.loading") : "Ask"}
          </button>
        </form>

        {aiError && (
          <div className="alert" role="alert" style={{ marginTop: "var(--space-3)" }}>
            {aiError}
          </div>
        )}

        {aiBusy && (
          <div className="ai-progress" role="status" style={{ marginTop: "var(--space-3)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <span className="spinner" aria-hidden="true" />
              <span>
                {aiPhase === "request"
                  ? "Request in progress…"
                  : AI_PHASE_LABEL[aiPhase ?? "started"] ?? "Thinking…"}
              </span>
            </div>
            {aiTransport === "request" && aiPhase === "request" && (
              <span className="hint">(live stream unavailable; plain request mode)</span>
            )}
            {aiStreamText && (
              <blockquote className="ai-streaming">{partialSummary(aiStreamText)}</blockquote>
            )}
          </div>
        )}

        {aiResult && (
          <div style={{ marginTop: "var(--space-4)" }}>
            {aiResult.available ? (
              <>
                <p style={{ margin: "0 0 var(--space-2)", fontWeight: 500, color: "var(--navy)" }}>
                  {aiResult.role === "conversational"
                    ? "CrimeLink answered directly (no graph retrieval needed)"
                    : (() => {
                        const ctx = aiResult.context as
                          | { nodes?: number; edges?: number; fast_path?: boolean }
                          | undefined;
                        if (ctx && !ctx.fast_path && (ctx.nodes !== undefined || ctx.edges !== undefined)) {
                          return `Based on ${ctx.nodes ?? 0} linked entities and ${ctx.edges ?? 0} relationships from this case`;
                        }
                        return "Model response";
                      })()}
                </p>
                <blockquote>
                  {String(
                    (aiResult.finding as { summary?: string } | undefined)?.summary ??
                      "No finding returned.",
                  )}
                </blockquote>
              </>
            ) : (
              <div className="banner banner-warn">
                <strong>AI Service Unavailable:</strong>{" "}
                {aiUnavailableMessage(aiResult.fallback_reason)}
              </div>
            )}

            <TechnicalDetails label="AI diagnostic details">
              <div style={{ fontSize: "var(--text-xs)" }}>
                {aiResult.model ? (
                  <p style={{ margin: "0 0 4px" }}>
                    <strong>Model:</strong> {String(aiResult.model)}
                    {aiResult.provider ? ` via ${String(aiResult.provider)}` : ""}
                    {typeof aiResult.latency_ms === "number" ? ` · ${aiResult.latency_ms} ms` : ""}
                  </p>
                ) : null}
                {aiResult.fallback_reason ? (
                  <p style={{ margin: "0 0 4px", color: "var(--bad)" }}>
                    <strong>Diagnostic code:</strong> {String(aiResult.fallback_reason)}
                  </p>
                ) : null}
                {(() => {
                  const ctx = aiResult.context as
                    | { nodes?: number; edges?: number; depth?: number; fast_path?: boolean; dataset_id?: string | null; graph_ready?: boolean }
                    | undefined;
                  if (!ctx) return null;
                  return (
                    <p style={{ margin: "0 0 4px" }}>
                      <strong>Context details:</strong> {ctx.nodes ?? 0} entities, {ctx.edges ?? 0} relationships
                      {ctx.depth ? `, depth ${ctx.depth}` : ""}
                      {ctx.fast_path ? " (fast path)" : ""}
                      {ctx.dataset_id ? ` · dataset ${String(ctx.dataset_id).slice(0, 8)}…` : ""}
                      {ctx.graph_ready === false ? " · case graph still building" : ""}
                    </p>
                  );
                })()}
                {(() => {
                  const timing = aiResult.timing as Record<string, number> | undefined;
                  if (!timing || Object.keys(timing).length === 0) return null;
                  const parts = Object.entries(timing).map(([stage, ms]) => `${stage.replace(/_/g, " ")}: ${ms}ms`);
                  return (
                    <p style={{ margin: "0 0 4px" }}>
                      <strong>Timing:</strong> {parts.join(" · ")}
                    </p>
                  );
                })()}
                {aiResult.request_id ? (
                  <p style={{ margin: "0 0 4px" }}>
                    <strong>Request ID:</strong> <code>{String(aiResult.request_id)}</code>
                  </p>
                ) : null}
              </div>
            </TechnicalDetails>
          </div>
        )}
      </section>

      {/* --- Documents (Plain High-Density Table) --- */}
      <section className="panel">
        <h2>{t("case.documents")}</h2>
        {!docs && <Spinner />}
        {docs && docs.length === 0 && <Empty />}
        {docs && docs.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>{t("doc.file")}</th>
                <th>{t("doc.type")}</th>
                <th>{t("doc.language")}</th>
                <th>{t("doc.confidence")}</th>
                <th>{t("case.processing")}</th>
                <th>{t("doc.status")}</th>
                <th>{t("doc.hash")}</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => {
                const job = jobs[doc.id];
                return (
                  <tr key={doc.id}>
                    <td>{doc.filename}</td>
                    <td>{doc.document_type}</td>
                    <td>{doc.language ?? "—"}</td>
                    <td>
                      <Badge value={doc.source_confidence} />
                    </td>
                    <td>
                      {job && job.status !== "COMPLETE" ? (
                        <div className="progress" title={job.stage_name ?? ""}>
                          <div className="progress-bar" style={{ width: `${job.progress_pct}%` }} />
                          <span>
                            Stage {job.stage_name ?? "?"} ({job.progress_pct}%)
                          </span>
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <Badge value={doc.ingestion_status} />
                      {doc.quarantined && <Badge value="QUARANTINED" />}
                      {doc.failure_reason && <div className="hint">{doc.failure_reason}</div>}
                    </td>
                    <td>
                      <code title={doc.content_hash}>{doc.content_hash.slice(0, 12)}…</code>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* --- Timeline (Plain High-Density Table) --- */}
      <section className="panel">
        <h2>{t("case.timeline")}</h2>
        {!timeline && <Spinner />}
        {timeline && timeline.length === 0 && <Empty />}
        {timeline && timeline.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 170 }}>When</th>
                <th style={{ width: 220 }}>Event</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {timeline.map((event, index) => (
                <tr key={`${event.at}-${index}`}>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {event.at?.slice(0, 16).replace("T", " ") ?? "—"}
                  </td>
                  <td><strong>{event.name}</strong></td>
                  <td>{event.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
