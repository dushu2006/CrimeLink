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

const TABS = ["overview", "audit", "users", "thresholds", "quarantine"] as const;

export default function Admin() {
  const session = useAuth((state) => state.session);
  const [tab, setTab] = useState<(typeof TABS)[number]>("overview");
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

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      api<OverviewStats>("/admin/overview"),
      api<{ items: AuditRow[] }>("/admin/audit/search?limit=100"),
      api<UserRow[]>("/admin/users"),
      api<{ items: ThresholdRow[] }>("/admin/thresholds"),
      api<{ items: { id: string; filename: string; failure_reason: string | null }[] }>("/admin/quarantine"),
    ])
      .then(([o, a, u, th, q]) => {
        setOverview(o);
        setAudit(a.items);
        setUsers(Array.isArray(u) ? u : ((u as unknown as { items: UserRow[] }).items ?? []));
        setThresholds(th.items ?? (th as unknown as ThresholdRow[]));
        setQuarantine(q.items);
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
