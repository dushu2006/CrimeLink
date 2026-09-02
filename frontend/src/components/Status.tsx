import { t } from "../i18n";

/** Four states on every screen: loading, empty, error, data (PRD 14.3). */

export function Spinner() {
  return (
    <div className="state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{t("state.loading")}</span>
    </div>
  );
}

export function Empty({ message }: { message?: string }) {
  return <div className="state state-empty">{message ?? t("state.empty")}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <div>
        <strong>{t("state.error")}</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          {t("state.retry")}
        </button>
      )}
    </div>
  );
}

const TONE: Record<string, string> = {
  COMPLETE: "ok",
  PENDING: "warn",
  PROCESSING: "busy",
  QUEUED: "busy",
  FAILED: "bad",
  QUARANTINED: "bad",
  NEW: "warn",
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
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge badge-${TONE[value] ?? "muted"}`}>{value.replace(/_/g, " ")}</span>;
}
