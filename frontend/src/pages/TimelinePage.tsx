/**
 * Timeline Page — Evidence-oriented, not a generic event feed.
 * RBAC: Both Investigator and Viewer can view (read-only)
 *
 * Every event comes from `/cases/{id}/timeline`, which is built from stored
 * graph records.  A timestamp is shown only when the record has one, and the
 * evidence chip opens the real document behind the event — never a synthesised
 * `E-042` that resolves to nothing.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, enhancedTimeline, type EnhancedTimelineEvent } from "../api/client";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { useAuth } from "../store/auth";
import { getRoleBadge } from "../lib/rbac";
import { useLiveRefresh } from "../lib/useLiveRefresh";
import StaleDataNotice from "../components/common/StaleDataNotice";

type EventRow = {
  key: string;
  timestamp: string | null;
  type: string;
  title: string;
  description: string;
  location: string | null;
  participants: string[];
  docIds: string[];
  confidence: number;
};

function toRow(event: EnhancedTimelineEvent, index: number): EventRow {
  const participants = (event.participants || [])
    .map((p: any) => (typeof p === "string" ? p : p?.name))
    .filter(Boolean);
  const docIds = [
    ...(event.evidence_doc_ids || []),
    ...(event.source_doc_id ? [event.source_doc_id] : []),
  ];
  return {
    key: event.event_key || `event-${index}`,
    timestamp: event.timestamp ?? event.at ?? null,
    type: event.event_type || event.type || "EVENT",
    title: event.name || event.description || "Untitled event",
    description: event.description || "",
    location: event.location ?? null,
    participants,
    docIds: Array.from(new Set(docIds)),
    confidence: typeof event.confidence === "number" ? event.confidence : 0,
  };
}

function dayKey(timestamp: string | null): string {
  if (!timestamp) return "No recorded timestamp";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "No recorded timestamp";
  return parsed.toLocaleDateString();
}

function timeLabel(timestamp: string | null): string {
  if (!timestamp) return "Timestamp unavailable";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "Timestamp unavailable";
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function TimelinePage() {
  const [rows, setRows] = useState<EventRow[]>([]);
  const [filterType, setFilterType] = useState("");
  const [filterParticipant, setFilterParticipant] = useState("");
  const [showDrawer, setShowDrawer] = useState(false);
  const [drawerDocId, setDrawerDocId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const session = useAuth((s) => s.session);
  const roleBadge = getRoleBadge(session?.role as any);
  const caseParam = searchParams.get("case") || "";
  const [autoCaseId, setAutoCaseId] = useState<string>("");
  const effectiveCaseParam = caseParam || autoCaseId;

  useEffect(() => {
    if (caseParam) return;
    api<{ items: Array<{ id: string }> }>("/cases?limit=1")
      .then((data) => {
        const first = data.items?.[0]?.id;
        if (first) {
          setAutoCaseId(first);
          const merged = new URLSearchParams(searchParams);
          merged.set("case", first);
          setSearchParams(merged, { replace: true });
        }
      })
      .catch(() => {});
  }, [caseParam, searchParams, setSearchParams]);

  const load = useCallback(() => {
    // The timeline is per-case: there is no cross-case timeline in the data,
    // and inventing one would mean inventing events.
    if (!effectiveCaseParam) {
      setRows([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    enhancedTimeline(effectiveCaseParam, {
      event_type: filterType || undefined,
      entity: filterParticipant || undefined,
      limit: 500,
    })
      .then((res) => setRows(res.events.map(toRow)))
      // A failed request is a failure — never an empty timeline.
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [effectiveCaseParam, filterType, filterParticipant]);

  useEffect(load, [load]);

  // Bounded live refresh: records land in the active dataset while this page
  // is open, and a failed refresh is reported rather than swallowed.
  const live = useLiveRefresh(async () => {
    await load();
  });

  const grouped = useMemo(() => {
    const buckets = new Map<string, EventRow[]>();
    for (const row of rows) {
      const key = dayKey(row.timestamp);
      const existing = buckets.get(key);
      if (existing) existing.push(row);
      else buckets.set(key, [row]);
    }
    return Array.from(buckets.entries()).sort((a, b) => b[0].localeCompare(a[0]));
  }, [rows]);

  const types = useMemo(
    () => Array.from(new Set(rows.map((r) => r.type))).sort(),
    [rows],
  );

  return (
    <div className="timeline-page">
      <StaleDataNotice
        error={live.error}
        lastRefreshedAt={live.lastRefreshedAt}
        onRetry={live.refreshNow}
      />
      <div className="page-header">
        <div>
          <h1>Timeline</h1>
          <p className="page-subtitle">Evidence-oriented timeline — When did the connection occur?</p>
          <div
            style={{
              fontSize: "11px",
              fontFamily: "var(--font-mono)",
              color: "var(--muted)",
              marginTop: "4px",
            }}
          >
            Evidence-grounded, real timestamps only ·{" "}
            <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span> · Read-only
            {effectiveCaseParam ? ` · Case: ${effectiveCaseParam}` : ""}
          </div>
        </div>
        <div className="timeline-filters">
          <input
            type="search"
            value={effectiveCaseParam}
            placeholder="Case id (required)"
            onChange={(event) => {
              const merged = new URLSearchParams(searchParams);
              if (event.target.value) merged.set("case", event.target.value);
              else merged.delete("case");
              setSearchParams(merged);
            }}
          />
          <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
            <option value="">All event types</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <input
            type="search"
            value={filterParticipant}
            placeholder="Entity name"
            onChange={(e) => setFilterParticipant(e.target.value)}
          />
        </div>
      </div>

      {error && (
        <div className="cl-error">
          <div className="cl-empty-title">Could not load the timeline</div>
          <div className="cl-empty-desc">{error}</div>
          <button className="cl-btn" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="skeleton-timeline">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton-line w-80" />
          ))}
        </div>
      )}

      {!loading && !error && !effectiveCaseParam && (
        <div className="cl-empty">
          <div className="cl-empty-title">Choose a case</div>
          <div className="cl-empty-desc">
            The timeline is built from a case's stored records, so it needs a case id. Pick one from
            the Cases page or type it above.
          </div>
        </div>
      )}

      {!loading && !error && effectiveCaseParam && rows.length === 0 && (
        <div className="cl-empty">
          <div className="cl-empty-title">
            {filterType || filterParticipant
              ? "No events match this filter"
              : "No timeline events for this case"}
          </div>
          <div className="cl-empty-desc">
            {filterType || filterParticipant
              ? "Clear the filters to see every recorded event."
              : "Viewer sees only the authorized timeline."}
          </div>
        </div>
      )}

      {!loading && !error && grouped.length > 0 && (
        <div className="timeline">
          {grouped.map(([date, dayEvents]) => (
            <div key={date} className="timeline-date-group">
              <div className="timeline-date">{date}</div>
              <div className="timeline-events">
                {dayEvents.map((ev) => (
                  <div key={ev.key} className="timeline-event">
                    <span className="timeline-time">{timeLabel(ev.timestamp)}</span>
                    <span className="timeline-type">{ev.type}</span>
                    <span className="timeline-persons">
                      {ev.title}
                      {ev.participants.length > 0 && ` — ${ev.participants.join(", ")}`}
                      {ev.location ? ` · ${ev.location}` : ""}
                    </span>
                    {ev.docIds.length > 0 ? (
                      <button
                        className="evidence-chip clickable"
                        onClick={() => {
                          setDrawerDocId(ev.docIds[0]);
                          setShowDrawer(true);
                        }}
                      >
                        {ev.docIds[0]}
                      </button>
                    ) : (
                      <span className="evidence-chip">No source document recorded</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <EvidenceDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        data={drawerDocId ? { id: drawerDocId } : null}
      />
    </div>
  );
}