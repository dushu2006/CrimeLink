import { t } from "../i18n";

/** Four states on every screen: loading, empty, error, data (PRD 14.3 & WS 6.3). */

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label ?? t("state.loading")}</span>
    </div>
  );
}

export function Empty({
  message,
  actionLabel,
  onAction,
}: {
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="state state-empty">
      <p>{message ?? t("state.empty")}</p>
      {actionLabel && onAction && (
        <button type="button" className="btn btn-secondary" onClick={onAction}>
          {actionLabel}
        </button>
      )}
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
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          {t("state.retry")}
        </button>
      )}
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
  VIEWER: "muted",
  OPEN: "ok",
  CLOSED: "muted",
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  const tone = TONE[value.toUpperCase()] ?? "muted";
  return <span className={`badge badge-${tone}`}>{value.replace(/_/g, " ")}</span>;
}
