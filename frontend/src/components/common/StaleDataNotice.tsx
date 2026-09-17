/**
 * The notice a page shows when its last live refresh failed.
 *
 * Kept as one component so every page words it identically: data that could
 * not be refreshed is explicitly marked as possibly out of date, with the time
 * of the last good load and a retry.  It is never presented as current, and it
 * never replaces the data — an investigator reading a 20-minute-old graph is
 * told it is 20 minutes old rather than shown a blank panel.
 */

interface Props {
  /** The last refresh failure, or null when the view is current. */
  error: string | null;
  /** Millisecond timestamp of the last successful refresh, or null. */
  lastRefreshedAt: number | null;
  onRetry: () => void;
}

export function StaleDataNotice({ error, lastRefreshedAt, onRetry }: Props) {
  if (!error) return null;
  return (
    <div className="banner banner-error" role="alert">
      Could not refresh — showing data from{" "}
      {lastRefreshedAt ? new Date(lastRefreshedAt).toLocaleTimeString() : "the last load"}.{" "}
      {error}{" "}
      <button className="cl-btn cl-btn-sm" onClick={onRetry}>
        Retry now
      </button>
    </div>
  );
}

export default StaleDataNotice;
