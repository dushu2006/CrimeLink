/**
 * Evidence Page — Source Records, Claim → Evidence → Original Record
 * RBAC: Both Investigator and Viewer can view (read-only)
 *
 * Every card is a real stored document.  Nothing here is synthesised: no
 * generated ids, no default confidence, no "verified" tick that was never
 * checked.  The provenance checks are computed by the drawer from stored
 * records, so a card makes no claim the backend has not made.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import {
  evidenceDocuments,
  type EvidenceDocumentRow,
} from "../api/client";
import { useAuth } from "../store/auth";
import { getRoleBadge } from "../lib/rbac";

const PAGE = 50;

function formatBytes(size: number | null | undefined): string {
  if (!size && size !== 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(2)} MB`;
}

function humanType(documentType: string | undefined): string {
  if (!documentType) return "Evidence record";
  return documentType
    .split("_")
    .map((part) => part.charAt(0) + part.slice(1).toLowerCase())
    .join(" ");
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function EvidencePage() {
  const [items, setItems] = useState<EvidenceDocumentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [showDrawer, setShowDrawer] = useState(false);
  const [drawerData, setDrawerData] = useState<{ id: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const session = useAuth((s) => s.session);
  const roleBadge = getRoleBadge(session?.role as any);

  const caseParam = searchParams.get("case") || "";
  const query = searchParams.get("q") || "";
  const offset = Number(searchParams.get("offset") ?? 0);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    evidenceDocuments({ caseId: caseParam || undefined, q: query || undefined, limit: PAGE, offset })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      // A failed request is a failure.  Rendering an empty grid instead would
      // tell the investigator there is no evidence when there is.
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [caseParam, query, offset]);

  useEffect(load, [load]);

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    if (!("offset" in next)) merged.delete("offset");
    setSearchParams(merged);
  }

  return (
    <div className="evidence-page">
      <div className="page-header">
        <div>
          <h1>Evidence</h1>
          <p className="page-subtitle">Source records — What evidence supports that connection?</p>
          <div className="cl-hierarchy">Claim → Evidence → Original Record</div>
          <div
            style={{
              fontSize: "11px",
              fontFamily: "var(--font-mono)",
              color: "var(--muted)",
              marginTop: "4px",
            }}
          >
            {loading ? "Loading…" : `${total.toLocaleString()} records`} ·{" "}
            <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span> · Read-only ·{" "}
            {caseParam ? `Case: ${caseParam}` : "All authorized cases"}
          </div>
        </div>
      </div>

      <div className="toolbar">
        <input
          type="search"
          value={query}
          placeholder="Filter by filename…"
          onChange={(event) => update({ q: event.target.value })}
        />
        <input
          type="search"
          value={caseParam}
          placeholder="Filter by case id…"
          onChange={(event) => update({ case: event.target.value })}
        />
        {(query || caseParam) && (
          <button className="cl-btn" onClick={() => update({ q: "", case: "" })}>
            Clear
          </button>
        )}
      </div>

      {error && (
        <div className="cl-error">
          <div className="cl-empty-title">Could not load evidence</div>
          <div className="cl-empty-desc">{error}</div>
          <button className="cl-btn" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="skeleton-list">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skeleton-line w-full" />
          ))}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="cl-empty">
          <div className="cl-empty-title">
            {query || caseParam ? "No evidence matches this filter" : "No evidence yet"}
          </div>
          <div className="cl-empty-desc">
            {query || caseParam
              ? "Clear the filter to see every record you are authorised to view."
              : "Evidence will appear here once imported. Viewer sees only authorized evidence."}
          </div>
        </div>
      )}

      {!loading && !error && items.length > 0 && (
        <>
          <div className="evidence-grid">
            {items.map((ev) => (
              <button
                key={ev.id}
                className="evidence-card"
                onClick={() => {
                  setDrawerData({ id: ev.id });
                  setShowDrawer(true);
                }}
              >
                <div className="evidence-card-header">
                  <span className="evidence-id">{ev.case_number ?? ev.case_id ?? "—"}</span>
                  <span className="evidence-type">{humanType(ev.document_type)}</span>
                </div>
                <div className="evidence-card-body">
                  <span>{ev.filename}</span>
                  <span className="evidence-timestamp">
                    {formatBytes(ev.size_bytes)} · {formatDate(ev.created_at)}
                  </span>
                </div>
                <div className="evidence-card-footer">
                  <span className="muted">{ev.ingestion_status}</span>
                  <span className="muted">{ev.source_confidence}</span>
                  <span className="muted">
                    {ev.reference_count} source reference{ev.reference_count === 1 ? "" : "s"}
                  </span>
                </div>
                {ev.quarantined && (
                  <div className="evidence-card-footer">
                    <span className="evidence-type">Quarantined</span>
                  </div>
                )}
              </button>
            ))}
          </div>

          <div className="pager">
            <button
              className="btn btn-small"
              disabled={offset <= 0}
              onClick={() => update({ offset: String(Math.max(0, offset - PAGE)) })}
            >
              Previous
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
            </span>
            <button
              className="btn btn-small"
              disabled={offset + PAGE >= total}
              onClick={() => update({ offset: String(offset + PAGE) })}
            >
              Next
            </button>
          </div>
        </>
      )}

      <EvidenceDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        data={drawerData}
        onOpenSource={(docId) => navigate(`/documents/${docId}`)}
      />
    </div>
  );
}
