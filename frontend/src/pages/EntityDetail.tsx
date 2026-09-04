/**
 * One entity: identity, relationships, evidencing documents and provenance.
 *
 * Relationships come from the graph, so only edges that genuinely exist are
 * shown. Each one exposes the evidence that justifies it (guarantee G1), and
 * that evidence opens the exact source position.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { EvidencePointer, EvidencePointerLink, ReferenceLink } from "../components/EvidenceLink";
import { SourceReference } from "../components/SourceViewer";

interface EntityRow {
  provenance_key: string;
  label: string;
  name: string;
  confidence: number;
  case_ids: string[];
  source_doc_ids: string[];
  aliases: string[];
  staging: boolean;
  properties: Record<string, unknown>;
  evidence?: EvidencePointer | null;
}

interface RelationshipRow {
  key: string;
  rel_type: string;
  confidence: number;
  direction: "in" | "out";
  other: { provenance_key: string; label: string; name: string } | null;
  evidence?: EvidencePointer | null;
}

interface Detail {
  entity: EntityRow;
  relationships: RelationshipRow[];
  relationship_count: number;
  relationships_truncated: boolean;
  documents: {
    id: string;
    filename: string;
    document_type: string;
    case_id: string;
    ingestion_status: string;
  }[];
  references: SourceReference[];
}

const HIDDEN_PROPS = new Set([
  "name", "confidence", "created_at", "source_doc_id", "case_id",
  "source_types", "source_type", "mentions", "extractor", "language",
]);

export default function EntityDetail() {
  const { entityKey } = useParams<{ entityKey: string }>();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!entityKey) return;
    setDetail(null);
    setError(null);
    api<Detail>(`/explore/entities/${encodeURIComponent(entityKey)}`)
      .then(setDetail)
      .catch((err: Error) => setError(err.message));
  }, [entityKey]);

  useEffect(load, [load]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!detail) return <Spinner />;

  const { entity } = detail;
  const extra = Object.entries(entity.properties ?? {}).filter(
    ([key, value]) => !HIDDEN_PROPS.has(key) && value !== null && value !== "",
  );

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{entity.name}</h1>
          <p className="muted">
            {entity.label} · confidence {entity.confidence.toFixed(2)}
          </p>
        </div>
        {entity.staging && <Badge value="PENDING_REVIEW" />}
      </header>

      <section className="card">
        <h2>Identity</h2>
        <dl className="kv">
          <dt>Type</dt>
          <dd>{entity.label}</dd>
          {entity.aliases.length > 0 && (
            <>
              <dt>Aliases</dt>
              <dd>{entity.aliases.join(", ")}</dd>
            </>
          )}
          {extra.map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt>{key.replace(/_/g, " ")}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
          <dt>First evidence</dt>
          <dd>
            <EvidencePointerLink
              pointer={entity.evidence}
              emptyMessage="No exact source position recorded."
            />
          </dd>
        </dl>
      </section>

      <section className="card">
        <h2>
          Relationships{" "}
          <span className="muted">({detail.relationship_count.toLocaleString()})</span>
        </h2>
        {detail.relationships.length === 0 ? (
          <Empty message="No relationships exist for this entity in the graph." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Relationship</th>
                <th>Connected entity</th>
                <th className="num">Confidence</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {detail.relationships.map((row) => (
                <tr key={row.key}>
                  <td>
                    <span className="rel-dir">
                      {row.direction === "out" ? "→" : "←"}
                    </span>{" "}
                    {row.rel_type.replace(/_/g, " ")}
                  </td>
                  <td>
                    {row.other ? (
                      <Link
                        to={`/entities/${encodeURIComponent(row.other.provenance_key)}`}
                      >
                        {row.other.name}
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
        )}
        {detail.relationships_truncated && (
          <p className="hint">
            Showing the {detail.relationships.length} highest-confidence of{" "}
            {detail.relationship_count.toLocaleString()} relationships.{" "}
            <Link to={`/relationships?entity=${encodeURIComponent(entity.provenance_key)}`}>
              Open the full list
            </Link>
            .
          </p>
        )}
      </section>

      <section className="card">
        <h2>Supporting documents</h2>
        {detail.documents.length === 0 ? (
          <Empty message="No documents are linked to this entity." />
        ) : (
          <ul className="chip-list">
            {detail.documents.map((doc) => (
              <li key={doc.id}>
                <Link className="chip" to={`/documents/${doc.id}`}>
                  <span className="chip-label">{doc.document_type}</span>
                  {doc.filename}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {detail.references.length > 0 && (
        <section className="card">
          <h2>Source references</h2>
          <ul className="chip-list">
            {detail.references.slice(0, 40).map((reference) => (
              <li key={reference.id}>
                <ReferenceLink reference={reference} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
