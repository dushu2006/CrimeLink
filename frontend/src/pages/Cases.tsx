import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
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
  created_at: string | null;
}

export default function Cases() {
  const [rows, setRows] = useState<CaseRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [number, setNumber] = useState("");
  const [title, setTitle] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");

  const load = useCallback(() => {
    setRows(null);
    setError(null);
    api<{ items: CaseRow[] }>("/cases")
      .then((data) => setRows(data.items))
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    try {
      await api("/cases", {
        method: "POST",
        body: JSON.stringify({
          case_number: number,
          title,
          jurisdiction_id: jurisdiction || undefined,
        }),
      });
      setCreating(false);
      setNumber("");
      setTitle("");
      setJurisdiction("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (error && !rows) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="page">
      <header className="page-head">
        <h1>{t("cases.title")}</h1>
        <button className="btn" onClick={() => setCreating((v) => !v)}>
          {t("cases.new")}
        </button>
      </header>

      {creating && (
        <form className="panel form-row" onSubmit={create}>
          <input
            placeholder={t("cases.number")}
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            required
          />
          <input placeholder={t("cases.name")} value={title} onChange={(e) => setTitle(e.target.value)} required />
          <input
            placeholder={t("cases.jurisdiction")}
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
          />
          <button className="btn btn-primary" type="submit">
            {t("cases.new")}
          </button>
        </form>
      )}

      {!rows && <Spinner />}
      {rows && rows.length === 0 && <Empty message={t("cases.empty")} />}

      {rows && rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>{t("cases.number")}</th>
              <th>{t("cases.name")}</th>
              <th>{t("cases.jurisdiction")}</th>
              <th>{t("cases.documents")}</th>
              <th>{t("cases.pending")}</th>
              <th>{t("cases.status")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <Link to={`/cases/${row.id}`}>{row.case_number}</Link>
                </td>
                <td>{row.title}</td>
                <td>{row.jurisdiction_id}</td>
                <td>{row.document_count}</td>
                <td>
                  {row.pending_review_count > 0 ? (
                    <span className="pill pill-warn">{row.pending_review_count}</span>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
                <td>
                  <Badge value={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
