/**
 * Relationship explorer.
 *
 * Shows source entity → type → target entity with the confidence the graph
 * actually holds, plus the evidence pointer that justifies the edge. Guarantee
 * G1 means no edge exists without evidence, so a missing pointer here means the
 * exact position was not recorded, never that the edge is unevidenced.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Empty, ErrorState, Spinner } from "../components/Status";
import { EvidencePointer, EvidencePointerLink } from "../components/EvidenceLink";

interface EntityRef {
  provenance_key: string;
  label: string;
  name: string;
}

interface RelationshipRow {
  key: string;
  rel_type: string;
  confidence: number;
  case_id: string;
  source_entity: EntityRef | null;
  target_entity: EntityRef | null;
  evidence?: EvidencePointer | null;
}

interface Response {
  items: RelationshipRow[];
  total: number;
  limit: number;
  offset: number;
  types: Record<string, number>;
}

const PAGE = 50;

export default function Relationships() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState<string | null>(null);

  const relType = params.get("rel_type") ?? "";
  const caseId = params.get("case_id") ?? "";
  const entity = params.get("entity") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const load = useCallback(() => {
    setData(null);
    setError(null);
    const query = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (relType) query.set("rel_type", relType);
    if (caseId) query.set("case_id", caseId);
    if (entity) query.set("entity", entity);
    api<Response>(`/explore/relationships?${query.toString()}`)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [relType, caseId, entity, offset]);

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
          <h1>Relationships</h1>
          {data && <p className="muted">{data.total.toLocaleString()} relationships</p>}
        </div>
      </header>

      {entity && (
        <p className="hint">
          Filtered to relationships involving one entity.{" "}
          <Link to={`/entities/${encodeURIComponent(entity)}`}>Back to that entity</Link> ·{" "}
          <button className="link-button" onClick={() => update({ entity: "" })}>
            Clear filter
          </button>
        </p>
      )}

      <div className="toolbar">
        <select
          value={relType}
          onChange={(event) => update({ rel_type: event.target.value })}
        >
          <option value="">All types</option>
          {Object.entries(data?.types ?? {}).map(([name, count]) => (
            <option key={name} value={name}>
              {name} ({count})
            </option>
          ))}
        </select>
      </div>

      {!data && <Spinner />}
      {data && data.items.length === 0 && (
        <Empty message="No relationships match these filters." />
      )}

      {data && data.items.length > 0 && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Relationship</th>
                <th>Target</th>
                <th className="num">Confidence</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.key}>
                  <td>
                    {row.source_entity ? (
                      <Link
                        to={`/entities/${encodeURIComponent(row.source_entity.provenance_key)}`}
                      >
                        {row.source_entity.name}
                      </Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <span className="rel-type">{row.rel_type.replace(/_/g, " ")}</span>
                  </td>
                  <td>
                    {row.target_entity ? (
                      <Link
                        to={`/entities/${encodeURIComponent(row.target_entity.provenance_key)}`}
                      >
                        {row.target_entity.name}
                      </Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="num">{row.confidence.toFixed(2)}</td>
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
