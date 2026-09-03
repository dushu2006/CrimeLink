/**
 * Document explorer across every case the investigator may see.
 *
 * Each row shows its real ingestion state — including failures and quarantine,
 * which are surfaced rather than hidden — and how many source references were
 * recorded for it, so it is obvious which documents are traceable.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";

export interface DocumentRow {
  id: string;
  case_id: string;
  case_number: string | null;
  filename: string;
  document_type: string;
  ingestion_status: string;
  source_confidence: string;
  quarantined: boolean;
  failure_reason: string | null;
  size_bytes: number;
  language: string | null;
  reference_count: number;
  relative_path: string | null;
  created_at: string | null;
}

interface Response {
  items: DocumentRow[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE = 50;

export default function Documents() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const caseId = params.get("case_id") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const load = useCallback(() => {
    setData(null);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (status) query.set("status", status);
    if (q) query.set("q", q);
    if (caseId) query.set("case_id", caseId);
    api<Response>(`/explore/documents?${query.toString()}`)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [status, q, caseId, offset]);

  useEffect(load, [load]);

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    // Any filter change resets paging, otherwise page 3 of a new filter is empty.
    if (!("offset" in next)) merged.delete("offset");
    setParams(merged);
  }

  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Documents</h1>
          {data && <p className="muted">{data.total.toLocaleString()} documents</p>}
        </div>
      </header>

      <div className="toolbar">
        <input
          className="filter-input"
          placeholder="Filter by filename…"
          defaultValue={q}
          onKeyDown={(event) => {
            if (event.key === "Enter") update({ q: event.currentTarget.value });
          }}
        />
        <select value={status} onChange={(event) => update({ status: event.target.value })}>
          <option value="">All statuses</option>
          <option value="COMPLETE">COMPLETE</option>
          <option value="PENDING">PENDING</option>
          <option value="PROCESSING">PROCESSING</option>
          <option value="FAILED">FAILED</option>
        </select>
        {caseId && (
          <button className="btn btn-small" onClick={() => update({ case_id: "" })}>
            Clear case filter
          </button>
        )}
      </div>

      {!data && <Spinner />}
      {data && data.items.length === 0 && (
        <Empty message="No documents match these filters." />
      )}

      {data && data.items.length > 0 && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Case</th>
                <th>Type</th>
                <th>Status</th>
                <th className="num">Source refs</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <Link to={`/documents/${row.id}`}>{row.filename}</Link>
                    {row.failure_reason && (
                      <div className="hint hint-bad">{row.failure_reason}</div>
                    )}
                  </td>
                  <td>
                    {row.case_number ? (
                      <Link to={`/cases/${row.case_id}`}>{row.case_number}</Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>{row.document_type}</td>
                  <td>
                    <Badge value={row.ingestion_status} />
                    {row.quarantined && <Badge value="QUARANTINED" />}
                  </td>
                  <td className="num">
                    {row.reference_count ? (
                      row.reference_count.toLocaleString()
                    ) : (
                      <span className="muted">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pager">
            <button
              className="btn btn-small"
              disabled={offset <= 0}
              onClick={() => update({ offset: String(Math.max(0, offset - PAGE)) })}
            >
              Previous
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of{" "}
              {data.total.toLocaleString()}
            </span>
            <button
              className="btn btn-small"
              disabled={offset + PAGE >= data.total}
              onClick={() => update({ offset: String(offset + PAGE) })}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
