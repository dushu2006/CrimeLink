/**
 * Enhanced Investigation Timeline — Professional Workflow (Sprint 2)
 *
 * Requirements:
 * - Events expose timestamp/type/entity/related/location/evidence/case/source/confidence/provenance
 * - Chronological UI grouped by day (example in spec)
 * - Interactions: open drawer / focus graph / view doc / pin / add entity
 * - Filters: date range / entity / type / evidence type / location
 * - Investigation window AI analysis via retrieval only window (no invented timestamps)
 */

import { useMemo, useState } from "react";
import type { EnhancedTimelineEvent, TimelineAnalyzeResponse } from "../../api/client";
import { analyzeTimelineWindow } from "../../api/client";

interface Props {
  caseId: string;
  events: EnhancedTimelineEvent[];
  onOpenEvidence?: (docId: string) => void;
  onFocusEntity?: (entityName: string) => void;
  onPinEvidence?: (id: string) => void;
  onAddEntity?: (entityName: string) => void;
  onViewDoc?: (docId: string) => void;
  className?: string;
}

function groupByDay(events: EnhancedTimelineEvent[] = []): Map<string, EnhancedTimelineEvent[]> {
  const map = new Map<string, EnhancedTimelineEvent[]>();
  for (const ev of events) {
    const ts = ev.timestamp || ev.at || "";
    // Extract date part YYYY-MM-DD
    const day = ts ? ts.slice(0, 10) : "Unknown date";
    if (!map.has(day)) map.set(day, []);
    map.get(day)!.push(ev);
  }
  // Sort days chronologically
  const sorted = new Map([...map.entries()].sort((a, b) => a[0].localeCompare(b[0])));
  return sorted;
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts.slice(0, 16);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts.slice(0, 16);
  }
}

function formatDayLabel(day: string): string {
  if (day === "Unknown date") return day;
  try {
    const d = new Date(day);
    if (isNaN(d.getTime())) return day;
    return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  } catch {
    return day;
  }
}

export function EnhancedTimeline({
  caseId,
  events,
  onOpenEvidence,
  onFocusEntity,
  onPinEvidence,
  onAddEntity,
  onViewDoc,
  className,
}: Props) {
  // Filters
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");
  const [filterEntity, setFilterEntity] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterLocation, setFilterLocation] = useState("");
  const [filterEvidenceType, setFilterEvidenceType] = useState("");

  // Window analysis
  const [windowFrom, setWindowFrom] = useState("");
  const [windowTo, setWindowTo] = useState("");
  const [windowQuestion, setWindowQuestion] = useState("");
  const [windowAnalysis, setWindowAnalysis] = useState<TimelineAnalyzeResponse | null>(null);
  const [windowLoading, setWindowLoading] = useState(false);
  const [windowError, setWindowError] = useState<string | null>(null);

  const filteredEvents = useMemo(() => {
    const safeEvents = Array.isArray(events) ? events : [];
    let out = [...safeEvents];
    if (filterFrom) out = out.filter((e) => (e.timestamp || e.at || "") >= filterFrom);
    if (filterTo) out = out.filter((e) => (e.timestamp || e.at || "") <= filterTo);
    if (filterEntity) {
      const needle = filterEntity.toLowerCase();
      out = out.filter(
        (e) =>
          (e.entity && e.entity.toLowerCase().includes(needle)) ||
          (e.related_entities ?? []).some((r) => r.toLowerCase().includes(needle)) ||
          (e.participants ?? []).some((p: any) => String(p.name || "").toLowerCase().includes(needle))
      );
    }
    if (filterType) {
      const needle = filterType.toLowerCase();
      out = out.filter((e) => (e.type || e.event_type || "").toLowerCase().includes(needle));
    }
    if (filterLocation) {
      const needle = filterLocation.toLowerCase();
      out = out.filter((e) => (e.location || "").toLowerCase().includes(needle));
    }
    if (filterEvidenceType) {
      const needle = filterEvidenceType.toLowerCase();
      out = out.filter((e) => e.evidence_doc_ids.some((id) => id.toLowerCase().includes(needle)) || (e.description || "").toLowerCase().includes(needle));
    }
    // Sort chronological
    out.sort((a, b) => String(a.timestamp || a.at || "").localeCompare(String(b.timestamp || b.at || "")));
    return out;
  }, [events, filterFrom, filterTo, filterEntity, filterType, filterLocation, filterEvidenceType]);

  const grouped = useMemo(() => groupByDay(filteredEvents), [filteredEvents]);

  const handleAnalyzeWindow = async () => {
    if (!windowFrom && !windowTo) return;
    setWindowLoading(true);
    setWindowError(null);
    setWindowAnalysis(null);
    try {
      const res = await analyzeTimelineWindow(caseId, {
        from_ts: windowFrom || null,
        to_ts: windowTo || null,
        question: windowQuestion || null,
      });
      setWindowAnalysis(res);
    } catch (err) {
      setWindowError(err instanceof Error ? err.message : String(err));
    } finally {
      setWindowLoading(false);
    }
  };

  return (
    <div className={`enhanced-timeline ${className || ""}`}>
      <div className="enhanced-timeline-header">
        <h3 className="enhanced-timeline-title">
          <span>Investigation Timeline</span>
          <span className="cl-badge cl-badge-info">{filteredEvents.length} events</span>
        </h3>
        <div className="enhanced-timeline-window-selector">
          <label>Window analysis</label>
          <input
            className="enhanced-timeline-filter-input"
            type="datetime-local"
            value={windowFrom}
            onChange={(e) => setWindowFrom(e.target.value)}
            placeholder="From"
          />
          <input
            className="enhanced-timeline-filter-input"
            type="datetime-local"
            value={windowTo}
            onChange={(e) => setWindowTo(e.target.value)}
            placeholder="To"
          />
          <input
            className="enhanced-timeline-filter-input"
            style={{ minWidth: "200px" }}
            value={windowQuestion}
            onChange={(e) => setWindowQuestion(e.target.value)}
            placeholder="Question about window (optional)"
          />
          <button className="cl-btn cl-btn-primary" onClick={() => void handleAnalyzeWindow()} disabled={windowLoading}>
            {windowLoading ? "Analyzing…" : "Analyze window"}
          </button>
        </div>
      </div>

      {windowError && <div className="cl-error">{windowError}</div>}

      {windowAnalysis && (
        <div className="enhanced-timeline-ai-analysis">
          <div className="enhanced-timeline-ai-header">
            <span className="enhanced-timeline-ai-title">AI Analysis — Window {windowAnalysis.window.from_ts || "…"} to {windowAnalysis.window.to_ts || "…"}</span>
            <span className="cl-badge cl-badge-info">{windowAnalysis.counts.events} events in window</span>
          </div>
          <div className="enhanced-timeline-ai-content">
            {windowAnalysis.ai_analysis?.analysis || `Window contains ${windowAnalysis.counts.events} events, ${windowAnalysis.counts.entities} entities, ${windowAnalysis.counts.relationships} relationships.`}
          </div>
          {windowAnalysis.ai_analysis?.evidence_refs && (
            <div className="enhanced-timeline-event-meta" style={{ marginTop: "8px" }}>
              {(Array.isArray(windowAnalysis.ai_analysis.evidence_refs) ? windowAnalysis.ai_analysis.evidence_refs : []).slice(0, 5).map((ref: string) => (
                <button key={ref} className="cl-btn" style={{ fontSize: "10px", padding: "2px 6px" }} onClick={() => onOpenEvidence?.(ref)}>
                  {ref.slice(0, 12)}…
                </button>
              ))}
            </div>
          )}
          <div className="enhanced-timeline-ai-disclaimer">{windowAnalysis.ai_analysis?.disclaimer || "AI-assisted analysis — requires verification. Only window context used. No invented timestamps."}</div>
        </div>
      )}

      <div className="enhanced-timeline-filters">
        <div className="enhanced-timeline-filter-group">
          <span className="enhanced-timeline-filter-label">Date range</span>
          <input className="enhanced-timeline-filter-input" type="date" value={filterFrom} onChange={(e) => setFilterFrom(e.target.value)} />
          <span style={{ color: "var(--text-tertiary)" }}>→</span>
          <input className="enhanced-timeline-filter-input" type="date" value={filterTo} onChange={(e) => setFilterTo(e.target.value)} />
        </div>
        <div className="enhanced-timeline-filter-group">
          <span className="enhanced-timeline-filter-label">Entity</span>
          <input className="enhanced-timeline-filter-input" placeholder="Name" value={filterEntity} onChange={(e) => setFilterEntity(e.target.value)} />
        </div>
        <div className="enhanced-timeline-filter-group">
          <span className="enhanced-timeline-filter-label">Type</span>
          <input className="enhanced-timeline-filter-input" placeholder="Event type" value={filterType} onChange={(e) => setFilterType(e.target.value)} />
        </div>
        <div className="enhanced-timeline-filter-group">
          <span className="enhanced-timeline-filter-label">Location</span>
          <input className="enhanced-timeline-filter-input" placeholder="Location" value={filterLocation} onChange={(e) => setFilterLocation(e.target.value)} />
        </div>
        <div className="enhanced-timeline-filter-group">
          <span className="enhanced-timeline-filter-label">Evidence</span>
          <input className="enhanced-timeline-filter-input" placeholder="Doc type" value={filterEvidenceType} onChange={(e) => setFilterEvidenceType(e.target.value)} />
        </div>
        {(filterFrom || filterTo || filterEntity || filterType || filterLocation || filterEvidenceType) && (
          <button
            className="cl-btn"
            onClick={() => {
              setFilterFrom("");
              setFilterTo("");
              setFilterEntity("");
              setFilterType("");
              setFilterLocation("");
              setFilterEvidenceType("");
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {filteredEvents.length === 0 ? (
        <div className="cl-empty">
          <div className="cl-empty-title">No timeline events</div>
          <div className="cl-empty-desc">No events match the current filters, or no events exist for this case. Timeline uses real evidence timestamps only — no invented data.</div>
        </div>
      ) : (
        <div className="enhanced-timeline-days">
          {[...grouped.entries()].map(([day, dayEvents]) => (
            <div key={day} className="enhanced-timeline-day-group">
              <div className="enhanced-timeline-day-header">
                <span className="enhanced-timeline-day-dot" />
                <span>{formatDayLabel(day)}</span>
                <span className="cl-badge" style={{ marginLeft: "6px" }}>{dayEvents.length}</span>
              </div>
              <div className="enhanced-timeline-events">
                {dayEvents.map((ev) => (
                  <div key={ev.event_key} className="enhanced-timeline-event" onClick={() => ev.source_doc_id && onOpenEvidence?.(ev.source_doc_id)}>
                    <div className="enhanced-timeline-event-time">
                      <strong>{formatTime(ev.timestamp || ev.at || null)}</strong>
                      <span>{ev.location || "—"}</span>
                    </div>
                    <div className="enhanced-timeline-event-content">
                      <span className="enhanced-timeline-event-type">{ev.type || ev.event_type || "Event"}</span>
                      <span className="enhanced-timeline-event-title" title={ev.name}>
                        {ev.name} {ev.entity ? `— ${ev.entity}` : ""}
                      </span>
                      <span className="enhanced-timeline-event-desc" title={ev.description}>
                        {ev.description}
                      </span>
                      <div className="enhanced-timeline-event-meta">
                        {ev.entity && (
                          <button
                            className="enhanced-timeline-event-meta-item"
                            onClick={(e) => {
                              e.stopPropagation();
                              onFocusEntity?.(ev.entity!);
                            }}
                          >
                            👤 {ev.entity}
                          </button>
                        )}
                        {(ev.related_entities ?? []).slice(0, 3).map((r) => (
                          <button
                            key={r}
                            className="enhanced-timeline-event-meta-item"
                            onClick={(e) => {
                              e.stopPropagation();
                              onFocusEntity?.(r);
                            }}
                          >
                            + {r}
                          </button>
                        ))}
                        {ev.location && <span className="enhanced-timeline-event-meta-item">📍 {ev.location}</span>}
                        {(ev.evidence_doc_ids ?? []).slice(0, 2).map((docId) => (
                          <button
                            key={docId}
                            className="enhanced-timeline-event-meta-item"
                            onClick={(e) => {
                              e.stopPropagation();
                              onViewDoc?.(docId);
                            }}
                          >
                            📄 {docId.slice(0, 10)}…
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="enhanced-timeline-event-actions">
                      <span
                        className="enhanced-timeline-confidence"
                        style={{
                          background: ev.confidence >= 0.8 ? "var(--success-bg)" : ev.confidence >= 0.5 ? "var(--warning-bg)" : "var(--surface-secondary)",
                          color: ev.confidence >= 0.8 ? "var(--success)" : ev.confidence >= 0.5 ? "var(--warning)" : "var(--text-tertiary)",
                          border: "1px solid",
                          borderColor: ev.confidence >= 0.8 ? "var(--success-border)" : ev.confidence >= 0.5 ? "var(--warning-border)" : "var(--border-secondary)",
                        }}
                      >
                        {Math.round(ev.confidence * 100)}%
                      </span>
                      <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", justifyContent: "flex-end" }}>
                        <button
                          className="cl-btn"
                          style={{ fontSize: "10px", padding: "2px 6px" }}
                          onClick={(e) => {
                            e.stopPropagation();
                            onPinEvidence?.(ev.event_key);
                          }}
                        >
                          Pin
                        </button>
                        {ev.entity && (
                          <button
                            className="cl-btn"
                            style={{ fontSize: "10px", padding: "2px 6px" }}
                            onClick={(e) => {
                              e.stopPropagation();
                              onAddEntity?.(ev.entity!);
                            }}
                          >
                            Add entity
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default EnhancedTimeline;
