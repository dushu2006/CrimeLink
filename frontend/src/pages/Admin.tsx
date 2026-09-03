import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../store/auth";
import { t } from "../i18n";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";

interface AuditRow {
  id: string;
  at?: string;
  created_at?: string;
  action: string;
  actor_badge?: string;
  actor_name?: string;
  target?: string;
  case_id?: string;
  details?: Record<string, unknown>;
  row_hash?: string;
  prev_hash?: string;
}

interface VerifyResult {
  valid: boolean;
  checked: number;
  first_tampered_id?: string | null;
  head_hash?: string;
}

interface ThresholdRow {
  key: string;
  value: number;
  description: string;
}

interface OverviewStats {
  users: number;
  cases: number;
  documents: number;
  pending_matches: number;
  new_patterns: number;
  graph: { backend: string; nodes: number; edges: number; version: number };
  audit_head: string;
}

interface UserRow {
  id: string;
  badge_number: string;
  full_name: string;
  role: string;
  jurisdiction_id: string;
  station_id: string;
  is_active: boolean;
}

interface DatabaseSummary {
  postgres: Record<string, number>;
  graph: { backend: string; nodes: number; edges: number; labels?: Record<string, number>; relationships?: Record<string, number> };
  infra: Record<string, unknown>;
}

interface HealthInfo {
  postgres: { ok: boolean; backend: string };
  graph: { ok: boolean; backend: string };
  redis: { ok: boolean; backend: string };
  object_store: { ok: boolean; backend: string };
  broker: { ok: boolean };
  nlp_provider: string;
  ai_roles: Record<string, boolean>;
}

interface NodeRow { id: string; label: string; name: string; confidence: number; case_count: number; source_doc_count: number; is_active: boolean }
interface EdgeRow { key: string; source: string; target: string; rel_type: string; confidence: number; source_doc_count: number }
interface CaseRow { id: string; case_number: string; title: string; jurisdiction_id: string; status: string; created_at?: string }
interface DocRow { id: string; case_id: string; document_type: string; filename: string; size_bytes: number; ingestion_status: string; quarantined: boolean; created_at?: string }

const TABS = ["dataset", "overview", "database", "cases", "documents", "entities", "relationships", "ai", "health", "audit", "users", "thresholds", "quarantine"] as const;

interface DatasetScan {
  ok: boolean;
  issues: string[];
  warnings: string[];
  counts: Record<string, number>;
  files_discovered: number;
  operational_files: number;
  document_files: number;
  accepted_files: number;
  reference_files: number;
  excluded_evaluation_files: number;
  unsupported_files: number;
  ground_truth_excluded: boolean;
  schema_tables: string[];
  files?: {
    relative_path: string;
    status: string;
    document_type: string | null;
    size_bytes: number;
    reason: string | null;
  }[];
}

interface DatasetStatus {
  mode: string;
  root: string;
  root_exists: boolean;
  dataset_name: string;
  jurisdiction_id: string;
  scan: DatasetScan;
  cases: number;
  documents_total: number;
  documents_by_status: Record<string, number>;
  pending_jobs: number | null;
  pending_matches: number;
  new_patterns: number;
  graph: { nodes?: number; edges?: number; error?: string; backend?: string };
  busy: boolean;
  stage_hint: string;
}

function DatasetPanel({ jurisdictionId }: { jurisdictionId?: string }) {
  const [status, setStatus] = useState<DatasetStatus | null>(null);
  const [preview, setPreview] = useState<DatasetScan | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<"validate" | "import" | null>(null);

  const loadStatus = useCallback(() => {
    api<DatasetStatus>("/admin/synthetic/status")
      .then((data) => {
        setStatus(data);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(loadStatus, [loadStatus]);

  useEffect(() => {
    if (!status?.busy) return;
    const timer = window.setInterval(loadStatus, 2000);
    return () => window.clearInterval(timer);
  }, [status?.busy, loadStatus]);

  async function validate() {
    setBusyAction("validate");
    setError(null);
    try {
      const data = await api<DatasetScan>("/admin/synthetic/external/preview");
      setPreview(data);
      loadStatus();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  async function importDataset() {
    setBusyAction("import");
    setError(null);
    setResult(null);
    try {
      const data = await api<Record<string, unknown>>("/admin/synthetic/ingest", {
        method: "POST",
        body: JSON.stringify({ adapter: "external" }),
      });
      setResult(data);
      loadStatus();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  const scan = preview ?? status?.scan;
  const jurisdictionMismatch =
    jurisdictionId && status?.jurisdiction_id && jurisdictionId !== status.jurisdiction_id;

  return (
    <section className="panel">
      <h2>{t("admin.dataset")}</h2>
      <p className="hint">
        Import is explicit. Startup never loads the corpus. Ground truth and metadata stay on disk.
      </p>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {jurisdictionMismatch && (
        <div className="alert" role="status">
          Your account jurisdiction is {jurisdictionId}. Imported cases use {status?.jurisdiction_id}.
          Cases will not appear on the Cases page until you sign in with a {status?.jurisdiction_id} account.
        </div>
      )}
      <div className="row-actions">
        <button className="btn" onClick={loadStatus} disabled={busyAction !== null}>
          {t("dataset.refresh")}
        </button>
        <button className="btn" onClick={() => void validate()} disabled={busyAction !== null}>
          {busyAction === "validate" ? t("state.loading") : t("dataset.validate")}
        </button>
        <button
          className="btn btn-primary"
          onClick={() => void importDataset()}
          disabled={busyAction !== null || !status?.root_exists || status?.scan.ok === false}
        >
          {busyAction === "import" ? t("state.loading") : t("dataset.import")}
        </button>
      </div>

      {!status && !error && <Spinner />}
      {status && (
        <>
          <div className="cards" style={{ marginTop: 16 }}>
            <div className="card">
              <span className="card-value">{status.root_exists ? "Found" : "Missing"}</span>
              <span className="card-label">dataset</span>
            </div>
            <div className="card">
              <span className="card-value">{status.cases}</span>
              <span className="card-label">imported cases</span>
            </div>
            <div className="card">
              <span className="card-value">{status.documents_total}</span>
              <span className="card-label">documents</span>
            </div>
            <div className="card">
              <span className="card-value">{status.graph.nodes ?? "—"}</span>
              <span className="card-label">graph nodes</span>
            </div>
            <div className="card">
              <span className="card-value">{status.graph.edges ?? "—"}</span>
              <span className="card-label">graph edges</span>
            </div>
            <div className="card">
              <span className="card-value">{status.pending_matches}</span>
              <span className="card-label">pending matches</span>
            </div>
          </div>
          <p className="hint">
            Path: <code>{status.root}</code>
            {status.root_exists ? "" : " — directory not found"} · mode {status.mode} ·
            jurisdiction {status.jurisdiction_id}
          </p>
          <p className="hint">
            {status.busy
              ? `${status.stage_hint}${status.pending_jobs ? ` · ${status.pending_jobs} job(s) pending` : ""}`
              : t("dataset.idle")}
          </p>

          {Object.keys(status.documents_by_status).length > 0 && (
            <p className="hint">
              Document statuses:{" "}
              {Object.entries(status.documents_by_status)
                .map(([name, count]) => `${name} ${count}`)
                .join(" · ")}
            </p>
          )}
        </>
      )}

      {scan && (
        <>
          <h3>Validation</h3>
          {!scan.ok && (
            <div className="alert" role="alert">
              {(scan.issues || []).join(" ")}
            </div>
          )}
          {scan.ok && <p className="hint">Dataset layout is valid. Ground truth excluded: {scan.ground_truth_excluded ? "yes" : "no files found"}.</p>}
          <table className="table">
            <thead>
              <tr>
                <th>Measure</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Files discovered</td><td>{scan.files_discovered}</td></tr>
              <tr><td>Operational files</td><td>{scan.operational_files}</td></tr>
              <tr><td>Document files</td><td>{scan.document_files}</td></tr>
              <tr><td>Accepted for ingest</td><td>{scan.accepted_files}</td></tr>
              <tr><td>Reference tables</td><td>{scan.reference_files}</td></tr>
              <tr><td>Unsupported / unreadable</td><td>{scan.unsupported_files}</td></tr>
              <tr><td>Excluded evaluation / metadata</td><td>{scan.excluded_evaluation_files}</td></tr>
            </tbody>
          </table>
          {scan.schema_tables?.length > 0 && (
            <p className="hint">Tables: {scan.schema_tables.join(", ")}</p>
          )}
          {(scan.warnings || []).map((warning) => (
            <p className="hint" key={warning}>
              warning: {warning}
            </p>
          ))}
          {scan.files && scan.files.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Status</th>
                  <th>Type</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {scan.files.slice(0, 80).map((file) => (
                  <tr key={file.relative_path}>
                    <td><code>{file.relative_path}</code></td>
                    <td><Badge value={file.status} /></td>
                    <td>{file.document_type ?? "—"}</td>
                    <td className="hint">{file.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {result && (
        <>
          <h3>Last import</h3>
          <p className="hint">
            ingested {String(result.records_ingested ?? 0)} · duplicates{" "}
            {String(result.records_skipped_duplicates ?? 0)} · rejected{" "}
            {String(result.records_rejected ?? 0)} · failed {String(result.records_failed ?? 0)} ·
            excluded {String(result.excluded_evaluation_files ?? 0)}
          </p>
          {Array.isArray(result.warnings) &&
            (result.warnings as string[]).map((warning) => (
              <p className="hint" key={warning}>
                warning: {warning}
              </p>
            ))}
        </>
      )}
    </section>
  );
}

export default function Admin() {
  const session = useAuth((state) => state.session);
  const [tab, setTab] = useState<(typeof TABS)[number]>("dataset");
  const [overview, setOverview] = useState<OverviewStats | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [thresholds, setThresholds] = useState<ThresholdRow[] | null>(null);
  const [quarantine, setQuarantine] = useState<{ id: string; filename: string; failure_reason: string | null }[] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [newBadge, setNewBadge] = useState("");
  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState("INVESTIGATOR");
  const [newStation, setNewStation] = useState("");
  const [newJurisdiction, setNewJurisdiction] = useState("");
  const [creating, setCreating] = useState(false);
  const [dbSummary, setDbSummary] = useState<DatabaseSummary | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [cases, setCases] = useState<CaseRow[] | null>(null);
  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [entities, setEntities] = useState<NodeRow[] | null>(null);
  const [rels, setRels] = useState<EdgeRow[] | null>(null);
  const [entitiesTotal, setEntitiesTotal] = useState(0);
  const [relsTotal, setRelsTotal] = useState(0);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      api<OverviewStats>("/admin/overview"),
      api<{ items: AuditRow[] }>("/admin/audit/search?limit=100"),
      api<{ items: UserRow[] }>("/admin/users"),
      api<{ items: ThresholdRow[] }>("/admin/thresholds"),
      api<{ items: { id: string; filename: string; failure_reason: string | null }[] }>("/admin/quarantine"),
      api<DatabaseSummary>("/admin/database/summary"),
      api<HealthInfo>("/admin/database/health"),
      api<{ items: CaseRow[]; total: number }>("/admin/database/cases?limit=50"),
      api<{ items: DocRow[]; total: number }>("/admin/database/documents?limit=50"),
      api<{ items: NodeRow[]; total: number }>("/admin/database/entities?limit=50"),
      api<{ items: EdgeRow[]; total: number }>("/admin/database/relationships?limit=50"),
    ])
      .then(([o, a, u, th, q, ds, h, c, d, e, r]) => {
        setOverview(o);
        setAudit(a.items);
        setUsers(u.items);
        setThresholds(th.items);
        setQuarantine(q.items);
        setDbSummary(ds);
        setHealth(h);
        setCases(c.items);
        setDocs(d.items);
        setEntities(e.items);
        setEntitiesTotal(e.total);
        setRels(r.items);
        setRelsTotal(r.total);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  // The server enforces this too; the guard only avoids a screen of 403s.
  if (session?.role !== "ADMIN") {
    return (
      <div className="page">
        <div className="state state-error" role="alert">
          {t("state.forbidden")}
        </div>
      </div>
    );
  }

  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="page">
      <header className="page-head">
        <h1>{t("admin.title")}</h1>
      </header>

      <div className="tabs">
        {TABS.map((name) => (
          <button key={name} className={tab === name ? "tab tab-on" : "tab"} onClick={() => setTab(name)}>
            {t(`admin.${name}`)}
          </button>
        ))}
      </div>

      {message && <div className="alert">{message}</div>}

      {tab === "dataset" && <DatasetPanel jurisdictionId={session?.jurisdiction_id} />}

      {tab === "overview" && (
        <>
          <div className="cards">
            {!overview && <Spinner />}
            {overview &&
              (
                [
                  ["users", overview.users],
                  ["cases", overview.cases],
                  ["documents", overview.documents],
                  ["pending matches", overview.pending_matches],
                  ["new patterns", overview.new_patterns],
                ] as const
              ).map(([label, value]) => (
                <div className="card" key={label}>
                  <span className="card-value">{value}</span>
                  <span className="card-label">{label}</span>
                </div>
              ))}
          </div>
          {overview && (
            <section className="panel">
              <h2>Graph</h2>
              <p className="hint">
                {overview.graph.backend} · {overview.graph.nodes} nodes · {overview.graph.edges}{" "}
                relationships · version {overview.graph.version}
              </p>
              <h3>Audit chain head</h3>
              <code>{overview.audit_head}</code>
            </section>
          )}
        </>
      )}

      {tab === "database" && dbSummary && (
        <section className="panel">
          <h2>Database overview (live)</h2>
          <div className="cards">
            {Object.entries(dbSummary.postgres).map(([k, v]) => (
              <div className="card" key={k}><span className="card-value">{v}</span><span className="card-label">{k}</span></div>
            ))}
          </div>
          <h3>Graph store ({dbSummary.graph.backend})</h3>
          <p className="hint">{dbSummary.graph.nodes} nodes · {dbSummary.graph.edges} relationships</p>
          {dbSummary.graph.labels && (
            <table className="table">
              <thead><tr><th>Node label</th><th>Count</th></tr></thead>
              <tbody>
                {Object.entries(dbSummary.graph.labels).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
              </tbody>
            </table>
          )}
          {dbSummary.graph.relationships && (
            <table className="table">
              <thead><tr><th>Relationship type</th><th>Count</th></tr></thead>
              <tbody>
                {Object.entries(dbSummary.graph.relationships).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
              </tbody>
            </table>
          )}
          <h3>Infrastructure</h3>
          <table className="table">
            <tbody>
              {Object.entries(dbSummary.infra).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td><code>{typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</code></td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === "health" && health && (
        <section className="panel">
          <h2>System health</h2>
          <table className="table">
            <thead><tr><th>Component</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              <tr><td>PostgreSQL</td><td>{health.postgres.ok ? "OK" : "FAIL"}</td><td>{health.postgres.backend}</td></tr>
              <tr><td>Graph</td><td>{health.graph.ok ? "OK" : "FAIL"}</td><td>{health.graph.backend}</td></tr>
              <tr><td>Redis/broker</td><td>{health.redis.ok ? "OK" : "FAIL"}</td><td>{health.redis.backend}</td></tr>
              <tr><td>Object store</td><td>{health.object_store.ok ? "OK" : "FAIL"}</td><td>{health.object_store.backend}</td></tr>
              <tr><td>Job broker</td><td>{health.broker.ok ? "OK" : "FAIL"}</td><td></td></tr>
              <tr><td>NLP provider</td><td>OK</td><td>{health.nlp_provider}</td></tr>
            </tbody>
          </table>
          <h3>AI model roles</h3>
          <table className="table">
            <tbody>
              {Object.entries(health.ai_roles).map(([role, on]) => (
                <tr key={role}><td>{role}</td><td>{on ? "configured" : "unavailable (offline mode)"}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === "cases" && (
        <section className="panel">
          {!cases && <Spinner />}
          {cases && (
            <table className="table">
              <thead><tr><th>Case number</th><th>Title</th><th>Jurisdiction</th><th>Status</th><th>Created</th></tr></thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id}><td>{c.case_number}</td><td>{c.title}</td><td>{c.jurisdiction_id}</td><td><Badge value={c.status} /></td><td>{(c.created_at ?? "").slice(0, 10)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "documents" && (
        <section className="panel">
          {!docs && <Spinner />}
          {docs && (
            <table className="table">
              <thead><tr><th>File</th><th>Type</th><th>Case</th><th>Status</th><th>Size</th></tr></thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}><td>{d.filename}</td><td>{d.document_type}</td><td>{d.case_id.slice(0, 8)}…</td><td><Badge value={d.ingestion_status} /></td><td>{(d.size_bytes / 1024).toFixed(1)} KB</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "entities" && (
        <section className="panel">
          {!entities && <Spinner />}
          {entities && (
            <>
              <p className="hint">Showing {entities.length} of {entitiesTotal} entities.</p>
              <table className="table">
                <thead><tr><th>ID</th><th>Label</th><th>Display</th><th>Confidence</th><th>Cases</th><th>Evidence docs</th></tr></thead>
                <tbody>
                  {entities.map((n) => (
                    <tr key={n.id}><td><code>{n.id.slice(0, 16)}…</code></td><td>{n.label}</td><td>{n.name}</td><td>{n.confidence.toFixed(2)}</td><td>{n.case_count}</td><td>{n.source_doc_count}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}

      {tab === "relationships" && (
        <section className="panel">
          {!rels && <Spinner />}
          {rels && (
            <>
              <p className="hint">Showing {rels.length} of {relsTotal} relationships.</p>
              <table className="table">
                <thead><tr><th>Source</th><th>Type</th><th>Target</th><th>Confidence</th><th>Evidence</th></tr></thead>
                <tbody>
                  {rels.map((r) => (
                    <tr key={r.key}><td><code>{r.source.slice(0, 12)}…</code></td><td><Badge value={r.rel_type} /></td><td><code>{r.target.slice(0, 12)}…</code></td><td>{r.confidence.toFixed(2)}</td><td>{r.source_doc_count}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}

      {tab === "ai" && (
        <section className="panel">
          <h2>AI activity</h2>
          <p className="hint">
            Every AI request is audited — see the Audit tab (action type <code>AI_QUERY</code>).
            The AI gateway pseudonymizes identifiers before sending context to models
            (e.g. PERSON_023 instead of real names) and the trusted backend retains
            the mapping only for authorized de-pseudonymization.
          </p>
          {health && (
            <ul>
              {Object.entries(health.ai_roles).map(([role, on]) => (
                <li key={role}><code>{role}</code>: {on ? "configured" : "unavailable"}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === "audit" && (
        <section className="panel">
          <div className="row-actions">
            <button
              className="btn btn-primary"
              onClick={() =>
                api<VerifyResult>("/admin/audit/verify")
                  .then(setVerify)
                  .catch((err: Error) => setMessage(err.message))
              }
            >
              {t("admin.verify")}
            </button>
          </div>
          {verify && (
            <div className={verify.valid ? "alert alert-ok" : "alert"}>
              {verify.valid
                ? `Chain intact across ${verify.checked} entries. Head: ${String(
                    verify.head_hash ?? "",
                  ).slice(0, 16)}…`
                : `Chain broken${
                    verify.first_tampered_id ? ` at entry ${verify.first_tampered_id}` : ""
                  } — tampering detected.`}
            </div>
          )}
          {!audit && <Spinner />}
          {audit && audit.length === 0 && <Empty />}
          {audit && audit.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Target</th>
                  <th>Row hash</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((row) => (
                  <tr key={row.id}>
                    <td>{String(row.at ?? row.created_at ?? "").slice(0, 19).replace("T", " ")}</td>
                    <td>
                      {row.actor_name ?? row.actor_badge ?? "—"}{" "}
                      <span className="muted">{row.actor_badge}</span>
                    </td>
                    <td>
                      <Badge value={row.action} />
                    </td>
                    <td>{row.target ?? "—"}</td>
                    <td>
                      <code title={row.row_hash}>{String(row.row_hash ?? "").slice(0, 10)}…</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "users" && (
        <section className="panel">
          <form
            className="form-row"
            onSubmit={(event) => {
              event.preventDefault();
              setCreating(true);
              api("/admin/users", {
                method: "POST",
                body: JSON.stringify({
                  badge_number: newBadge.trim(),
                  full_name: newName.trim(),
                  password: newPassword,
                  role: newRole,
                  station_id: newStation.trim(),
                  jurisdiction_id: newJurisdiction.trim(),
                }),
              })
                .then(() => {
                  setMessage(`${newBadge.trim()} created.`);
                  setNewBadge("");
                  setNewName("");
                  setNewPassword("");
                  setNewStation("");
                  setNewJurisdiction("");
                  load();
                })
                .catch((err: Error) => setMessage(err.message))
                .finally(() => setCreating(false));
            }}
          >
            <input
              placeholder={t("login.badge")}
              value={newBadge}
              onChange={(e) => setNewBadge(e.target.value)}
              required
            />
            <input
              placeholder={t("setup.fullName")}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
            />
            <input
              placeholder={t("admin.password")}
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
            <select value={newRole} onChange={(e) => setNewRole(e.target.value)}>
              <option value="INVESTIGATOR">INVESTIGATOR</option>
              <option value="VIEWER">VIEWER</option>
              <option value="ADMIN">ADMIN</option>
            </select>
            <input
              placeholder={t("admin.station")}
              value={newStation}
              onChange={(e) => setNewStation(e.target.value)}
              required
            />
            <input
              placeholder={t("setup.jurisdiction")}
              value={newJurisdiction}
              onChange={(e) => setNewJurisdiction(e.target.value)}
              required
            />
            <button className="btn btn-primary" type="submit" disabled={creating}>
              {creating ? t("state.loading") : t("admin.createUser")}
            </button>
          </form>
          <p className="hint">Passwords must be at least 10 characters and mix letters with numbers or symbols.</p>
          {!users && <Spinner />}
          {users && users.length === 0 && <Empty />}
          {users && users.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Badge</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Jurisdiction</th>
                  <th>Station</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.badge_number}</td>
                    <td>{user.full_name}</td>
                    <td>
                      <Badge value={user.role} />
                    </td>
                    <td>{user.jurisdiction_id}</td>
                    <td>{user.station_id}</td>
                    <td>{user.is_active ? "ACTIVE" : "DISABLED"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "thresholds" && (
        <section className="panel">
          {!thresholds && <Spinner />}
          {thresholds && (
            <table className="table">
              <thead>
                <tr>
                  <th>Threshold</th>
                  <th>Value</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                {thresholds.map((row) => (
                  <tr key={row.key}>
                    <td>
                      <code>{row.key}</code>
                    </td>
                    <td>
                      <input
                        className="inline-input"
                        type="number"
                        defaultValue={row.value}
                        onBlur={(event) =>
                          api("/admin/thresholds", {
                            method: "POST",
                            body: JSON.stringify({ key: row.key, value: Number(event.target.value) }),
                          })
                            .then(() => setMessage(`${row.key} updated.`))
                            .catch((err: Error) => setMessage(err.message))
                        }
                      />
                    </td>
                    <td className="muted">{row.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === "quarantine" && (
        <section className="panel">
          {!quarantine && <Spinner />}
          {quarantine && quarantine.length === 0 && <Empty />}
          {quarantine && quarantine.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>{t("doc.file")}</th>
                  <th>Reason</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {quarantine.map((doc) => (
                  <tr key={doc.id}>
                    <td>{doc.filename}</td>
                    <td className="hint">{doc.failure_reason ?? "—"}</td>
                    <td>
                      <button
                        className="btn btn-small"
                        onClick={() =>
                          api(`/admin/quarantine/${doc.id}/release`, { method: "POST" })
                            .then(() => {
                              setMessage(`${doc.filename} released and re-queued.`);
                              load();
                            })
                            .catch((err: Error) => setMessage(err.message))
                        }
                      >
                        Release
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}
