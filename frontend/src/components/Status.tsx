import { t } from "../i18n";

/** Four states on every screen: loading, empty, error, data — plus permission, unavailable, deterministic fallback */

/** Professional skeleton — not generic Loading... */
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton-container" role="status" aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className={`skeleton-line ${i % 2 === 0 ? "w-80" : "w-60"}`} />
      ))}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="skeleton-card" role="status" aria-label="Loading">
      <div className="skeleton-line w-60" />
      <div className="skeleton-line w-40" />
      <div className="skeleton-line w-80" />
    </div>
  );
}

export function SkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="skeleton-grid" role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label ?? t("state.loading")}</span>
    </div>
  );
}

/** Professional empty states — intentional, not "No data found" */
export function Empty({
  message,
  actionLabel,
  onAction,
  title,
  description,
}: {
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
  title?: string;
  description?: string;
}) {
  return (
    <div className="cl-empty" role="status">
      <div className="cl-empty-title">{title || message || t("state.empty")}</div>
      {description && <div className="cl-empty-desc">{description}</div>}
      {message && !title && <div className="cl-empty-desc">{message}</div>}
      {actionLabel && onAction && (
        <button type="button" className="cl-btn cl-btn-primary" onClick={onAction} style={{ marginTop: "12px" }}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}

export function EmptyCase({ onAddEvidence }: { onAddEvidence?: () => void }) {
  return (
    <div className="cl-empty">
      <div className="cl-empty-title">No investigation data yet</div>
      <div className="cl-empty-desc">This case currently contains no relationship evidence. Add or import case records to begin investigation.</div>
      <button className="cl-btn cl-btn-primary" onClick={onAddEvidence} style={{ marginTop: "12px" }}>Add Evidence</button>
    </div>
  );
}

export function EmptyPeople() {
  return (
    <div className="cl-empty">
      <div className="cl-empty-title">No people identified yet</div>
      <div className="cl-empty-desc">Search for people to begin investigation. People are the center of CrimeLink.</div>
    </div>
  );
}

export function EmptyRelationships() {
  return (
    <div className="cl-empty">
      <div className="cl-empty-title">No relationships found</div>
      <div className="cl-empty-desc">No person-to-person relationships established from available evidence. Try expanding search or importing more evidence.</div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <div>
        <strong>{t("state.error")}</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button type="button" className="cl-btn cl-btn-secondary" onClick={onRetry}>
          {t("state.retry")}
        </button>
      )}
    </div>
  );
}

export function PermissionDenied({ message, role }: { message?: string; role?: string }) {
  return (
    <div className="cl-empty" role="alert" style={{ borderLeft: "4px solid #e2e8f0", paddingLeft: "16px" }}>
      <div className="cl-empty-title">Permission denied</div>
      <div className="cl-empty-desc" style={{ marginTop: "8px" }}>
        {message || "You don't have permission to view this investigation."}
      </div>
      <div className="cl-empty-desc" style={{ marginTop: "8px", fontSize: "11px", fontFamily: "var(--font-mono)" }}>
        {role === "VIEWER" ? "Viewer role is read-only — investigation actions require Investigator role. Contact administrator if you need additional access." : "Contact administrator if you believe you should have access. Case existence is not disclosed for unauthorized resources."}
      </div>
      <div style={{ marginTop: "12px", display: "flex", gap: "8px" }}>
        <button className="cl-btn cl-btn-sm" onClick={() => window.location.assign("/cases")}>View Cases</button>
        <button className="cl-btn cl-btn-sm" onClick={() => window.location.assign("/people")}>View People</button>
      </div>
    </div>
  );
}

export function Unavailable({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="cl-empty" role="status">
      <div className="cl-empty-title">Service unavailable</div>
      <div className="cl-empty-desc">{message || "This service is temporarily unavailable. Deterministic evidence remains usable."}</div>
      {onRetry && <button className="cl-btn cl-btn-sm" onClick={onRetry} style={{ marginTop: "12px" }}>Retry</button>}
    </div>
  );
}

const TONE: Record<string, string> = {
  COMPLETE: "ok",
  COMPLETED: "ok",
  PENDING: "warn",
  PROCESSING: "busy",
  QUEUED: "busy",
  RUNNING: "busy",
  FAILED: "bad",
  QUARANTINED: "bad",
  NEW: "navy",
  CONFIRMED: "ok",
  DISMISSED: "muted",
  PENDING_REVIEW: "warn",
  MERGED: "ok",
  REJECTED: "muted",
  VERIFIED: "ok",
  OFFICIAL: "ok",
  SEMI_OFFICIAL: "warn",
  UNVERIFIED: "muted",
  ADMIN: "navy",
  INVESTIGATOR: "navy",
  VIEWER: "info",
  OPEN: "ok",
  CLOSED: "muted",
  FACT: "success",
  INFERENCE: "info",
  HYPOTHESIS: "warn",
  UNKNOWN: "muted",
  STRONG: "success",
  MODERATE: "info",
  WEAK: "warn",
  INSUFFICIENT: "muted",
  HIGH: "success",
  MEDIUM: "info",
  LOW: "warn",
};

export function Badge({ value, tone }: { value: string | null | undefined; tone?: string }) {
  if (!value) return <span className="muted">—</span>;
  const badgeTone = tone || TONE[value.toUpperCase()] || "muted";
  return <span className={`badge badge-${badgeTone} cl-badge cl-badge-${badgeTone}`} title={value}>{value.replace(/_/g, " ")}</span>;
}

/** Common primitives — Button, EvidenceChip, Person pill */
export function EvidenceChip({ id, onClick }: { id: string; onClick?: () => void }) {
  return (
    <button className="evidence-chip clickable" onClick={onClick} title={`Open evidence ${id}`} aria-label={`Open evidence ${id}`}>
      {id} ↗
    </button>
  );
}

export function PersonPill({ id, name, onClick }: { id: string; name?: string; onClick?: () => void }) {
  return (
    <button className="person-pill" onClick={onClick} title={`Focus ${name || id}`} aria-label={`Focus ${name || id}`}>
      <span className="person-pill-icon">👤</span>
      <span>{name || id.slice(0, 12)}</span>
    </button>
  );
}
