/**
 * Evidence Drawer — Claim → Evidence → Source record → Original file → Case.
 *
 * Every field in this panel is read from the backend.  The previous version
 * rendered hardcoded fallbacks ("Communication record", "Case record",
 * "Supports relationship") and three permanent green provenance ticks that
 * were never checked against anything, and its "Open Original Record" and
 * "Pin Evidence" buttons called optional callbacks that no caller supplied —
 * decorative buttons that did nothing.
 *
 * Now:
 *  - opening the drawer fetches GET /api/v1/evidence/{doc_id}/provenance;
 *  - the provenance ticks are the server-computed checks, and a check that
 *    fails says why instead of pretending;
 *  - "Open Original Record" opens the real stored file (PDF / CSV / image) in
 *    the SourceViewer;
 *  - "Verify integrity" calls the real hash-verification endpoint;
 *  - the findings that cite this evidence and the people it is recorded
 *    against are listed and linked;
 *  - a chain step that cannot be resolved is rendered as "Provenance
 *    unavailable" rather than as a link to nothing.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { Badge, ErrorState, Spinner } from "../Status";
import SourceViewer, { type SourceTarget } from "../SourceViewer";

export interface EvidenceDrawerData {
  /** CaseDocument id — the only thing this panel requires. */
  id: string;
  title?: string;
  type?: string;
  caseId?: string;
  timestamp?: string;
  date?: string;
  location?: string;
  supports?: string;
  source?: string;
  evidenceRole?: string;
  linkedEntities?: Array<{ id: string; name: string; type: string }>;
  content?: string;
  confidence?: number;
  evidenceLevel?: "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN";
  docIds?: string[];
  nodeIds?: string[];
  edgeIds?: string[];
  raw?: any;
}

interface ProvenanceCheck {
  ok: boolean | null;
  detail: string;
}

interface ProvenancePayload {
  document: {
    id: string;
    filename: string;
    document_type: string;
    media_type: string | null;
    size_bytes: number | null;
    content_hash: string | null;
    source_confidence: string;
    ingestion_status: string;
    classification: string;
    quarantined: boolean;
    created_at: string | null;
    evidence_id?: string | null;
    title?: string | null;
  };
  case: {
    id: string;
    case_number: string;
    title: string;
    status: string;
    jurisdiction_id: string;
  } | null;
  file: {
    storage_key: string | null;
    relative_path: string | null;
    available: boolean;
    size_bytes: number | null;
    media_type: string | null;
    hash_matches: boolean | null;
    detail: string;
    preview_url?: string | null;
    raw_url?: string | null;
  };
  source_references: Array<{
    id: string;
    origin_file: string;
    record_id: string | null;
    row_number: number | null;
    line_start: number | null;
    line_end: number | null;
    excerpt: string | null;
  }>;
  dataset_file: { id: string; relative_path: string } | null;
  findings: Array<{
    id: string;
    title: string;
    finding_type: string;
    status: string;
    confidence: number | null;
    narrative: string | null;
  }>;
  people: Array<{
    provenance_key: string;
    name: string;
    role: string | null;
    criminal_status: string | null;
    is_criminal: boolean;
  }>;
  checks: Record<string, ProvenanceCheck>;
  chain: Array<{ step: string; resolved: boolean; ref: string | null }>;
}

interface VerifyResult {
  document_id: string;
  recorded_hash: string;
  computed_hash: string;
  match: boolean;
  size_bytes: number;
  verified_at: string;
}

interface Props {
  data?: EvidenceDrawerData | null;
  evidence?: EvidenceDrawerData | null;
  open?: boolean;
  onClose: () => void;
  onViewGraph?: (nodeIds: string[]) => void;
  /** Kept for callers that still supply it; the drawer no longer depends on it. */
  onOpenSource?: (docId: string) => void;
  onPin?: (id: string) => void;
}

const CHECK_LABELS: Record<string, string> = {
  source_verified: "Source verified",
  record_available: "Record available",
  traceable_to_original: "Traceable to original",
  hash_matches: "Integrity hash matches",
};

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

export function EvidenceDrawer({ data, evidence, open, onClose, onViewGraph, onOpenSource, onPin }: Props) {
  const resolvedData = (data ?? evidence) as EvidenceDrawerData | null;
  const isOpen = open === undefined || open;

  const docId = resolvedData?.docIds?.[0] ?? resolvedData?.id ?? null;
  const [payload, setPayload] = useState<ProvenancePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [viewerTarget, setViewerTarget] = useState<SourceTarget | null>(null);

  const load = useCallback(() => {
    if (!docId) return;
    setLoading(true);
    setError(null);
    setVerify(null);
    api<ProvenancePayload>(`/evidence/${encodeURIComponent(docId)}/provenance`)
      .then(setPayload)
      .catch((err: Error) => {
        setPayload(null);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [docId]);

  useEffect(() => {
    if (!isOpen || !docId) return;
    load();
  }, [isOpen, docId, load]);

  useEffect(() => {
    if (!isOpen) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose, isOpen]);

  if (!isOpen || !resolvedData) return null;

  const doc = payload?.document ?? null;
  const file = payload?.file ?? null;
  const checks = payload?.checks ?? {};
  const canOpenOriginal = Boolean(file?.available && (file?.relative_path || file?.storage_key));

  function openOriginalRecord() {
    if (!docId || !canOpenOriginal) return;
    const path = file!.relative_path || file!.storage_key!;
    // Prefer the caller's own handler when it provides one; otherwise open the
    // real file in the SourceViewer here.
    if (onOpenSource) {
      onOpenSource(docId);
      return;
    }
    setViewerTarget({
      kind: "file",
      path,
      docId,
      datasetFileId: payload?.dataset_file?.id ?? null,
    });
  }

  function runVerify() {
    if (!docId) return;
    setVerifying(true);
    api<VerifyResult>(`/evidence/${encodeURIComponent(docId)}/verify`)
      .then(setVerify)
      .catch((err: Error) => setVerify({
        document_id: docId, recorded_hash: "", computed_hash: "",
        match: false, size_bytes: 0, verified_at: err.message,
      }))
      .finally(() => setVerifying(false));
  }

  return (
    <div className="evidence-drawer-backdrop" onClick={onClose}>
      <div className="evidence-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="evidence-drawer-header">
          <div>
            <span className="evidence-drawer-kicker">EVIDENCE</span>
            <h2 className="evidence-drawer-title">
              {doc?.evidence_id || doc?.id || resolvedData.id}
            </h2>
          </div>
          <button className="evidence-drawer-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="evidence-drawer-body">
          {loading && <Spinner label="Loading evidence record..." />}
          {error && !loading && (
            <ErrorState message={error} onRetry={load} />
          )}

          {!loading && !error && !payload && (
            <p className="muted">
              No evidence record could be resolved for <code>{docId}</code>. Nothing is shown
              rather than a placeholder.
            </p>
          )}

          {payload && doc && (
            <>
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">File</span>
                <span className="evidence-drawer-value evidence-drawer-mono">{doc.filename}</span>
              </div>

              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Type</span>
                <span className="evidence-drawer-value">{humanType(doc.document_type)}</span>
              </div>

              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Date</span>
                <span className="evidence-drawer-value">
                  {doc.created_at
                    ? new Date(doc.created_at).toLocaleString()
                    : "Timestamp unavailable"}
                </span>
              </div>

              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Size / media type</span>
                <span className="evidence-drawer-value">
                  {formatBytes(file?.size_bytes ?? doc.size_bytes)} · {doc.media_type ?? "unknown"}
                </span>
              </div>

              {/* CASE — real, from the resolved document */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Case</span>
                {payload.case ? (
                  <span className="evidence-drawer-value">
                    <Link to={`/cases/${payload.case.id}`}>{payload.case.case_number}</Link>
                    {" — "}{payload.case.title} <Badge value={payload.case.status} />
                  </span>
                ) : (
                  <span className="evidence-drawer-value muted">Provenance unavailable — no case resolved</span>
                )}
              </div>

              {/* PEOPLE this document is recorded against */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Recorded against</span>
                {payload.people.length > 0 ? (
                  <div className="linked-entities-chips">
                    {payload.people.slice(0, 12).map((person) => (
                      <Link
                        key={person.provenance_key}
                        to={`/entities/${encodeURIComponent(person.provenance_key)}`}
                        className="linked-entity-chip"
                      >
                        <span className="entity-chip-type">
                          {person.is_criminal ? "★ PERSON" : "PERSON"}
                        </span>
                        <span className="entity-chip-name">{person.name}</span>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <span className="evidence-drawer-value muted">
                    Provenance unavailable — no person record cites this document
                  </span>
                )}
              </div>

              {/* SOURCE RECORDS */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Source records</span>
                {payload.source_references.length > 0 ? (
                  <ul className="evidence-drawer-list">
                    {payload.source_references.slice(0, 6).map((ref) => (
                      <li key={ref.id} className="evidence-drawer-mono">
                        {ref.origin_file}
                        {ref.row_number ? ` · row ${ref.row_number}` : ""}
                        {ref.record_id ? ` · ${ref.record_id}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="evidence-drawer-value muted">
                    Provenance unavailable — no source reference recorded
                  </span>
                )}
              </div>

              {/* FINDINGS this evidence supports */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Supports findings</span>
                {payload.findings.length > 0 ? (
                  <ul className="evidence-drawer-list">
                    {payload.findings.slice(0, 6).map((finding) => (
                      <li key={finding.id}>
                        {finding.title} <Badge value={finding.status} />
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="evidence-drawer-value muted">
                    Provenance unavailable — no finding cites this evidence yet
                  </span>
                )}
              </div>

              {/* PROVENANCE — computed, never asserted */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Provenance</span>
                <div className="provenance-checks">
                  {Object.entries(CHECK_LABELS).map(([key, label]) => {
                    const check = checks[key];
                    if (!check) {
                      return (
                        <span key={key} className="provenance-check">
                          ? {label} — not evaluated
                        </span>
                      );
                    }
                    const cls = check.ok ? "verified" : "failed";
                    return (
                      <span key={key} className={`provenance-check ${cls}`} title={check.detail}>
                        {check.ok ? "✓" : "✗"} {label}
                      </span>
                    );
                  })}
                </div>
                {Object.values(checks).some((c) => c.ok === false) && (
                  <p className="evidence-drawer-note muted">
                    {Object.entries(checks)
                      .filter(([, c]) => c.ok === false)
                      .map(([k, c]) => `${CHECK_LABELS[k] ?? k}: ${c.detail}`)
                      .join(" · ")}
                  </p>
                )}
              </div>

              {/* CHAIN */}
              <div className="evidence-drawer-field">
                <span className="evidence-drawer-label">Chain</span>
                <div className="evidence-drawer-chain">
                  {payload.chain.map((step) => (
                    <span
                      key={step.step}
                      className={`chain-step ${step.resolved ? "resolved" : "unresolved"}`}
                      title={step.ref ?? "not resolved"}
                    >
                      {step.resolved ? "✓" : "—"} {step.step.replace(/_/g, " ").toLowerCase()}
                    </span>
                  ))}
                </div>
              </div>

              {resolvedData.evidenceLevel && (
                <div className="evidence-drawer-field">
                  <span className="evidence-drawer-label">Classification</span>
                  <span className="evidence-drawer-value">
                    <Badge value={resolvedData.evidenceLevel} />
                  </span>
                </div>
              )}

              {verify && (
                <div className="evidence-drawer-field">
                  <span className="evidence-drawer-label">Integrity check</span>
                  <span className="evidence-drawer-value">
                    <Badge value={verify.match ? "HASH MATCHES" : "HASH MISMATCH"} />
                  </span>
                  <span className="evidence-drawer-desc evidence-drawer-mono">
                    recorded {verify.recorded_hash?.slice(0, 16) || "—"} · computed{" "}
                    {verify.computed_hash?.slice(0, 16) || "—"} · {formatBytes(verify.size_bytes)}
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        <div className="evidence-drawer-footer">
          <button
            className="cl-btn cl-btn-primary w-full"
            onClick={openOriginalRecord}
            disabled={!canOpenOriginal}
            title={
              canOpenOriginal
                ? "Open the stored file"
                : "The stored file could not be read, so there is nothing to open"
            }
          >
            {canOpenOriginal ? "Open Original Record" : "Original record unavailable"}
          </button>
          <div className="evidence-drawer-trust">
            <span>Case → Evidence → Source record → Original file → Finding</span>
            <span>Every link above is resolved from stored data</span>
          </div>
          <div className="evidence-drawer-actions">
            <button className="cl-btn cl-btn-sm" onClick={runVerify} disabled={verifying || !docId}>
              {verifying ? "Verifying…" : "Verify integrity"}
            </button>
            {payload?.case && (
              <Link className="cl-btn cl-btn-sm" to={`/cases/${payload.case.id}`}>
                Open case
              </Link>
            )}
            {resolvedData.nodeIds && resolvedData.nodeIds.length > 0 && onViewGraph && (
              <button className="cl-btn cl-btn-sm" onClick={() => onViewGraph(resolvedData.nodeIds!)}>
                View Graph
              </button>
            )}
            {onPin && (
              <button className="cl-btn cl-btn-sm" onClick={() => onPin(resolvedData.id)}>
                Pin Evidence
              </button>
            )}
          </div>
        </div>
      </div>

      {viewerTarget && (
        <SourceViewer
          target={viewerTarget}
          subtitle={doc?.filename}
          onClose={() => setViewerTarget(null)}
        />
      )}
    </div>
  );
}

export default EvidenceDrawer;
