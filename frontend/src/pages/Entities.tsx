/**
 * Entity explorer — every resolved entity in the cases the caller may see.
 *
 * Counts come from the graph, so an empty list means the graph is genuinely
 * empty for those cases rather than that the screen is unfinished.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Empty, ErrorState, Spinner } from "../components/Status";
import { EvidencePointerLink, EvidencePointer } from "../components/EvidenceLink";

interface EntityRow {
  provenance_key: string;
  label: string;
  name: string;
  confidence: number;
  staging: boolean;
  case_count: number;
  document_count: number;
  evidence?: EvidencePointer | null;
}

interface Response {
  items: EntityRow[];
  total: number;
  limit: number;
  offset: number;
  labels: Record<string, number>;
}

const PAGE = 50;

export default function Entities() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);

  const label = params.get("label") ?? "";
  const q = params.get("q") ?? "";
  const caseId = params.get("case_id") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const load = useCallback(() => {
    setData(null);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (label) query.set("label", label);
    if (q) query.set("q", q);
    if (caseId) query.set("case_id", caseId);
    api<Response>(`/explore/entities?${query.toString()}`)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [label, q, caseId, offset]);

  useEffect(load, [load]);

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    if (!("offset" in next)) merged.delete("offset");
    setParams(merged);
  }

  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Entities</h1>
          {data && <p className="muted">{data.total.toLocaleString()} entities</p>}
        </div>
      </header>

      <div className="toolbar">
        <input
          className="filter-input"
          placeholder="Search by name…"
          defaultValue={q}
          onKeyDown={(event) => {
            if (event.key === "Enter") update({ q: event.currentTarget.value });
          }}
        />
        <select value={label} onChange={(event) => update({ label: event.target.value })}>
          <option value="">All types</option>
          {Object.entries(data?.labels ?? {}).map(([name, count]) => (
            <option key={name} value={name}>
              {name} ({count})
            </option>
          ))}
        </select>
      </div>

      {!data && <Spinner />}
      {data && data.items.length === 0 && (
        <Empty message="No entities match these filters." />
      )}

      {data && data.items.length > 0 && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th className="num">Confidence</th>
                <th className="num">Cases</th>
                <th className="num">Documents</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.provenance_key}>
                  <td>
                    <Link to={`/entities/${encodeURIComponent(row.provenance_key)}`}>
                      {row.name}
                    </Link>
                  </td>
                  <td>{row.label}</td>
                  <td className="num">{row.confidence.toFixed(2)}</td>
                  <td className="num">{row.case_count}</td>
                  <td className="num">{row.document_count}</td>
                  <td>
                    <EvidencePointerLink pointer={row.evidence} emptyMessage="—" />
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
