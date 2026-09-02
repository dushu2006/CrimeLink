import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, download, jobSocket, uploadDocument } from "../api/client";
import { t } from "../i18n";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";

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
const CONFIDENCE = ["OFFICIAL", "SEMI_OFFICIAL", "UNVERIFIED"];

export default function CaseDetail() {
  const { caseId = "" } = useParams();
  const [caseRow, setCaseRow] = useState<CaseRow | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [jobs, setJobs] = useState<Record<string, JobRow>>({});
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [docType, setDocType] = useState("FIR");
  const [confidence, setConfidence] = useState("OFFICIAL");
  const fileRef = useRef<HTMLInputElement>(null);

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

  // Live processing status: "Stage 3/6 — NLP extraction" without polling.
  useEffect(() => {
    return jobSocket(caseId, () => {
      api<{ items: JobRow[] }>(`/cases/${caseId}/jobs?limit=50`)
        .then((data) => {
          setJobs(Object.fromEntries(data.items.map((j) => [j.doc_id, j])));
          return api<{ items: DocRow[] }>(`/cases/${caseId}/documents`);
        })
        .then((data) => setDocs(data.items))
        .catch(() => undefined);
    });
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
        <header className="page-head">
          <div>
            <h1>{caseRow.case_number}</h1>
            <p className="muted">{caseRow.title}</p>
          </div>
          <div className="row-actions">
            <Link className="btn" to={`/cases/${caseId}/graph`}>
              {t("case.openGraph")}
            </Link>
            <Link className="btn" to={`/cases/${caseId}/review`}>
              {t("case.review")}
              {caseRow.pending_review_count > 0 && (
                <span className="pill pill-warn">{caseRow.pending_review_count}</span>
              )}
            </Link>
            <button
              className="btn"
              onClick={() =>
                download(`/cases/${caseId}/export`, `case-brief-${caseRow!.case_number.replace(/\//g, "-")}.pdf`)
                  .catch((err: Error) => setError(err.message))
              }
            >
              {t("case.export")}
            </button>
          </div>
        </header>
      )}

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

      <section className="panel">
        <h2>{t("case.timeline")}</h2>
        {!timeline && <Spinner />}
        {timeline && timeline.length === 0 && <Empty />}
        {timeline && timeline.length > 0 && (
          <ol className="timeline">
            {timeline.map((event, index) => (
              <li key={`${event.at}-${index}`}>
                <span className="timeline-when">{event.at?.slice(0, 16).replace("T", " ") ?? "—"}</span>
                <span className="timeline-what">
                  {event.name} — {event.description}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
