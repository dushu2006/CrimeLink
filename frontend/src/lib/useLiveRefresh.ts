/**
 * Bounded live refresh for the investigator views.
 *
 * CrimeLink already has two real-time mechanisms: a per-case job WebSocket for
 * ingest progress, and ``watchActiveDataset``, which polls the active dataset
 * and remounts the route tree when it is replaced.  Neither covers the common
 * case — the *same* dataset gaining new evidence, findings, patterns or
 * relationship records while an investigator has a page open.  Those pages used
 * to show whatever they fetched on mount until the user navigated away, with no
 * indication that the data had gone stale.
 *
 * This hook closes that gap without turning into a polling storm:
 *
 *  - it refreshes on the ``crimelink:dataset-changed`` event immediately;
 *  - it refreshes when the tab becomes visible again, but at most once per
 *    ``minIntervalMs`` (a user who alt-tabs ten times does not fire ten
 *    refreshes);
 *  - it polls only while the document is visible, and only if the caller asks
 *    for an interval — the default is event-driven, which costs nothing;
 *  - a failed refresh is reported to the caller instead of being swallowed, so
 *    a page can mark itself stale rather than silently keeping old data.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export const DATASET_CHANGED_EVENT = "crimelink:dataset-changed";

/** Minimum gap between two visibility-triggered refreshes. */
export const DEFAULT_MIN_INTERVAL_MS = 15_000;

export interface LiveRefreshOptions {
  /** Re-run ``refresh`` on this cadence while the tab is visible. Omit for none. */
  intervalMs?: number;
  /** Minimum gap between visibility-triggered refreshes. */
  minIntervalMs?: number;
  /** Skip all automatic refreshing (e.g. the user is editing). */
  paused?: boolean;
}

export interface LiveRefreshState {
  /** Millisecond timestamp of the last *successful* refresh, or null. */
  lastRefreshedAt: number | null;
  /** The last failure, or null. Cleared on the next success. */
  error: string | null;
  /** True while a refresh is in flight. */
  refreshing: boolean;
  /** Force a refresh now, bypassing the throttle. */
  refreshNow: () => void;
}

/**
 * @param refresh An idempotent loader.  It must reject on failure — a loader
 *   that catches its own errors defeats the staleness reporting.
 */
export function useLiveRefresh(
  refresh: () => Promise<unknown> | void,
  options: LiveRefreshOptions = {},
): LiveRefreshState {
  const { intervalMs, minIntervalMs = DEFAULT_MIN_INTERVAL_MS, paused = false } = options;

  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  const lastRunRef = useRef(0);
  const inFlightRef = useRef(false);

  const run = useCallback(
    async (reason: "event" | "visibility" | "interval" | "manual") => {
      if (inFlightRef.current) return;
      const now = Date.now();
      // Manual and event-driven refreshes always run; the throttled ones
      // (visibility, interval) must respect the minimum gap.
      if (reason !== "manual" && reason !== "event" && now - lastRunRef.current < minIntervalMs) {
        return;
      }
      lastRunRef.current = now;
      inFlightRef.current = true;
      setRefreshing(true);
      try {
        await refreshRef.current();
        setError(null);
        setLastRefreshedAt(Date.now());
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        inFlightRef.current = false;
        setRefreshing(false);
      }
    },
    [minIntervalMs],
  );

  useEffect(() => {
    if (paused) return;

    const onDatasetChanged = () => void run("event");
    window.addEventListener(DATASET_CHANGED_EVENT, onDatasetChanged);

    const onVisible = () => {
      if (document.visibilityState === "visible") void run("visibility");
    };
    document.addEventListener("visibilitychange", onVisible);

    let timer: ReturnType<typeof setInterval> | null = null;
    if (intervalMs && intervalMs > 0) {
      timer = setInterval(() => {
        // Never poll a background tab: nobody is looking, and the backend is
        // shared with every other investigator.
        if (document.visibilityState === "visible") void run("interval");
      }, Math.max(intervalMs, minIntervalMs));
    }

    return () => {
      window.removeEventListener(DATASET_CHANGED_EVENT, onDatasetChanged);
      document.removeEventListener("visibilitychange", onVisible);
      if (timer !== null) clearInterval(timer);
    };
  }, [run, intervalMs, minIntervalMs, paused]);

  return {
    lastRefreshedAt,
    error,
    refreshing,
    refreshNow: () => void run("manual"),
  };
}
