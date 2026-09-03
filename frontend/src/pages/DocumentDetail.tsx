/**
 * One document: its ingestion state, the entities extracted from it, and every
 * source reference recorded during ingestion.
 *
 * The reference list is the point of this screen — each row opens the exact
 * position in the original corpus file that produced the ingested content.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { ReferenceLink } from "../components/EvidenceLink";
import { SourceReference } from "../components/SourceViewer";

interface Detail {
  id: string;
  case: { id: string; case_number: string; title: string };
  filename: string;
  document_type: string;
  ingestion_status: string;
  ingestion_stage: number;
  source_confidence: string;
  quarantined: boolean;
  failure_reason: string | null;
  content_hash: string;
  size_bytes: number;
  language: string | null;
  origin: { file: string; row?: number | null; record_id?: string | null } | null;
  relative_path: string | null;
  reference_count: number;
  entities: {
    provenance_key: string;
    label: string;
    name: string;
    confidence: number;
  }[];
  entity_count: number;
}

interface RefResponse {
  items: SourceReference[];
  total: number;
}

export default function DocumentDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [refs, setRefs] = useState<RefResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!docId) return;
    setDetail(null);
    setError(null);
    api<Detail>(`/explore/documents/${docId}`)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
    api<RefResponse>(`/sources/documents/${docId}/references?limit=100`)
      .then(setRefs)
      .catch(() => setRefs({ items: [], total: 0 }));
  }, [docId]);

  useEffect(load, [load]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!detail) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{detail.filename}</h1>
          <p className="muted">
            {detail.document_type} ·{" "}
            <Link to={`/cases/${detail.case.id}`}>{detail.case.case_number}</Link>
          </p>
        </div>
        <div className="head-actions">
          <Badge value={detail.ingestion_status} />
          {detail.quarantined && <Badge value="QUARANTINED" />}
        </div>
      </header>

      {detail.failure_reason && (
        <div className="alert alert-bad">
          <strong>Ingestion failed.</strong> {detail.failure_reason}
        </div>
      )}

      <section className="card">
        <h2>Document</h2>
        <dl className="kv">
          <dt>Type</dt>
          <dd>{detail.document_type}</dd>
          <dt>Source confidence</dt>
          <dd>
            <Badge value={detail.source_confidence} />
          </dd>
          <dt>Language</dt>
          <dd>{detail.language ?? "—"}</dd>
          <dt>SHA-256</dt>
          <dd>
            <code className="hash">{detail.content_hash}</code>
          </dd>
          <dt>Origin</dt>
          <dd>
            {detail.origin?.file ? (
              <ReferenceLink
                reference={{
                  origin_file: detail.origin.file,
                  row_number: detail.origin.row ?? null,
                  record_id: detail.origin.record_id ?? null,
                }}
              />
            ) : detail.relative_path ? (
              <ReferenceLink reference={{ origin_file: detail.relative_path }} />
            ) : (
              <span className="muted">Uploaded directly; no corpus origin.</span>
            )}
          </dd>
        </dl>
      </section>

      <section className="card">
        <h2>
          Source references{" "}
          <span className="muted">({detail.reference_count.toLocaleString()})</span>
        </h2>
        <p className="hint">
          Each reference addresses the exact position in the original dataset file
          that produced part of this document.
        </p>
        {!refs && <Spinner />}
        {refs && refs.items.length === 0 && (
          <Empty message="No source references were recorded for this document." />
        )}
        {refs && refs.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Origin</th>
                <th>Record</th>
                <th>Evidence fields</th>
                <th>Excerpt</th>
              </tr>
            </thead>
            <tbody>
              {refs.items.map((reference) => (
                <tr key={reference.id}>
                  <td>
                    <ReferenceLink reference={reference} />
                  </td>
                  <td>
                    {reference.record_id ? (
                      <code>{reference.record_id}</code>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    {(reference.field_names ?? []).length ? (
                      (reference.field_names ?? []).map((field) => (
                        <code key={field} className="field-chip">
                          {field}
                        </code>
                      ))
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="excerpt">
                    {reference.excerpt ?? <span className="muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {refs && refs.total > refs.items.length && (
          <p className="hint">
            Showing {refs.items.length} of {refs.total.toLocaleString()} references.
          </p>
        )}
      </section>

      <section className="card">
        <h2>
          Entities extracted{" "}
          <span className="muted">({detail.entity_count.toLocaleString()})</span>
        </h2>
        {detail.entities.length === 0 ? (
          <Empty message="No entities were extracted from this document." />
        ) : (
          <ul className="chip-list">
            {detail.entities.map((entity) => (
              <li key={entity.provenance_key}>
                <Link
                  className="chip"
                  to={`/entities/${encodeURIComponent(entity.provenance_key)}`}
                >
                  <span className="chip-label">{entity.label}</span>
                  {entity.name}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
