/**
 * Timeline Page — Evidence-oriented, not a generic event feed.
 *
 * Semantic importance is derived ONLY from event_type / source data — never
 * invented colors or random weights. Categories and icons are chosen from
 * the DocumentType/Event vocabulary the backend emits (FIR, CHARGE_SHEET, CDR,
 * FINANCIAL, SURVEILLANCE, WITNESS_STATEMENT, ARREST_RECORD, etc.).
 *
 * Every event comes from `/cases/{id}/timeline`, built from stored graph
 * records. A timestamp is shown only when the record has one; the evidence
 * chip opens the real document behind the event.
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
  category: EventCategory;
  title: string;
  description: string;
  location: string | null;
  participants: string[];
  docIds: string[];
  confidence: number;
};

/**
 * Semantic categories an event can belong to — derived deterministically from
 * the event_type string the backend emits. Adding a category requires a real
 * data signal; we never colour events by arbitrary "importance".
 */
type EventCategory =
  | "legal"        // FIR, CHARGE_SHEET, ARREST_RECORD, LEGAL_RECORD, BAIL, COURT
  | "communication"// CDR, CALL, PHONE
  | "financial"    // FINANCIAL, FINANCIAL_TRANSACTION, BANK
  | "surveillance" // CCTV, SURVEILLANCE
  | "person"       // ARREST, encounter, person-level
  | "evidence"     // SCENE_REPORT, FORENSIC, SEIZURE
  | "investigation"// CASE_DIARY, PATROL_REPORT, INTEL, REVIEW
  | "statement"    // WITNESS_STATEMENT
  | "record"       // Other records
  | "unknown";

function categorize(rawType: string | null | undefined): EventCategory {
  const t = (rawType || "").toUpperCase().replace(/[^A-Z]/g, "_");
  if (!t) return "unknown";
  if (/(FIR|CHARGE|ARREST|BAIL|LEGAL|COURT|CONVICTION|ACCUSED)/.test(t)) return "legal";
  if (/(CDR|CALL|COMMUNICATION|PHONE)/.test(t)) return "communication";
  if (/(FINANCIAL|BANK|TRANSACTION|ACCOUNT)/.test(t)) return "financial";
  if (/(CCTV|SURVEILLANCE)/.test(t)) return "surveillance";
  if (/(WITNESS|STATEMENT)/.test(t)) return "statement";
  if (/(SCENE|FORENSIC|SEIZURE)/.test(t)) return "evidence";
  if (/(DIARY|PATROL|INTEL|REVIEW|INVESTIGAT)/.test(t)) return "investigation";
  if (/(PERSON|NAMES?|PRIOR_OFFENCE|CRIMINAL)/.test(t)) return "person";
  if (/(RECORD|REPORT)/.test(t)) return "record";
  return "unknown";
}

const CATEGORY_META: Record<EventCategory, { icon: string; label: string; tone: string }> = {
  legal:         { icon: "gavel",              label: "Legal milestone", tone: "critical" },
  communication: { icon: "call",               label: "Communication",   tone: "accent" },
  financial:     { icon: "account_balance",    label: "Financial",       tone: "warn" },
  surveillance:  { icon: "videocam",           label: "Surveillance",    tone: "accent" },
  statement:     { icon: "record_voice_over",  label: "Statement",       tone: "default" },
  evidence:      { icon: "location_on",        label: "Scene/Forensic",  tone: "success" },
  investigation: { icon: "menu_book",          label: "Investigation",   tone: "default" },
  person:        { icon: "person",             label: "Person event",    tone: "default" },
  record:        { icon: "description",        label: "Record",          tone: "default" },
  unknown:       { icon: "circle",             label: "Event",           tone: "muted" },
};

/** "Important" = legal milestones, forensic scene, and charges. These are the
 *  events that anchor an investigation. Other categories stay visually
 *  present but get a lighter treatment — density via border weight + icon,
 *  not via vivid color. */
function isMilestone(cat: EventCategory): boolean {
  return cat === "legal";
}

function toRow(event: EnhancedTimelineEvent, index: number): EventRow {
  const participants = (event.participants || [])
    .map((p: any) => (typeof p === "string" ? p : p?.name))
    .filter(Boolean);
  const docIds = [
    ...(event.evidence_doc_ids || []),
    ...(event.source_doc_id ? [event.source_doc_id] : []),
  ];
  const rawType = event.event_type || event.type || "";
  return {
    key: event.event_key || `event-${index}`,
    timestamp: event.timestamp ?? event.at ?? null,
    type: rawType || "EVENT",
    category: categorize(rawType),
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
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", weekday: "short" });
}

function timeLabel(timestamp: string | null): string {
  if (!timestamp) return "Timestamp unavailable";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "Timestamp unavailable";
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const FILTER_CATEGORIES: EventCategory[] = [
  "legal", "communication", "financial", "surveillance",
  "statement", "evidence", "investigation", "person",
];

export default function TimelinePage() {
  const [rows, setRows] = useState<EventRow[]>([]);
  const [filterCategory, setFilterCategory] = useState<EventCategory | "">("");
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
    if (!effectiveCaseParam) {
      setRows([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    enhancedTimeline(effectiveCaseParam, {
      event_type: filterCategory || undefined,
      entity: filterParticipant || undefined,
      limit: 500,
    })
      .then((res) => setRows(res.events.map(toRow)))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [effectiveCaseParam, filterCategory, filterParticipant]);

  useEffect(load, [load]);

  const live = useLiveRefresh(async () => { await load(); });

  const grouped = useMemo(() => {
    const filtered = rows.filter((r) => {
      if (filterCategory && r.category !== filterCategory) return false;
      if (filterParticipant) {
        const needle = filterParticipant.toLowerCase();
        const hay = (r.title + " " + r.participants.join(" ") + " " + r.description).toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    const buckets = new Map<string, { key: string; events: EventRow[] }>();
    for (const row of filtered) {
      const key = dayKey(row.timestamp);
      const existing = buckets.get(key);
      if (existing) existing.events.push(row);
      else buckets.set(key, { key, events: [row] });
    }
    // Sort groups: dated groups in reverse chronological order, "No recorded timestamp" at the end.
    return Array.from(buckets.values()).sort((a, b) => {
      if (a.key === "No recorded timestamp") return 1;
      if (b.key === "No recorded timestamp") return -1;
      return b.key.localeCompare(a.key);
    });
  }, [rows, filterCategory, filterParticipant]);

  // Counts per category for the filter chips
  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of rows) counts[r.category] = (counts[r.category] || 0) + 1;
    return counts;
  }, [rows]);

  return (
    <div className="timeline-page">
      <StaleDataNotice error={live.error} lastRefreshedAt={live.lastRefreshedAt} onRetry={live.refreshNow} />
      <div className="page-header">
        <div>
          <h1>Timeline</h1>
          <p className="page-subtitle">Chronological sequence of evidence-grounded events.</p>
          <div className="cl-hierarchy">DATE → EVENT TYPE → DESCRIPTION → PARTICIPANTS → EVIDENCE → SIGNIFICANCE</div>
        </div>
      </div>

      <div className="timeline-toolbar">
        <div className="timeline-case-input">
          <label className="filter-label">Case</label>
          <input
            type="search"
            value={effectiveCaseParam}
            placeholder="Case id"
            onChange={(event) => {
              const merged = new URLSearchParams(searchParams);
              if (event.target.value) merged.set("case", event.target.value);
              else merged.delete("case");
              setSearchParams(merged);
            }}
          />
        </div>

        <div className="timeline-filter-group">
          <label className="filter-label">Participant</label>
          <input
            type="search"
            value={filterParticipant}
            placeholder="Filter by entity…"
            onChange={(e) => setFilterParticipant(e.target.value)}
          />
        </div>

        <div className="timeline-category-chips">
          <button
            type="button"
            className={`filter-chip ${filterCategory === "" ? "active" : ""}`}
            onClick={() => setFilterCategory("")}
          >
            All <span className="chip-count">{rows.length}</span>
          </button>
          {FILTER_CATEGORIES.map((cat) => {
            const meta = CATEGORY_META[cat];
            const count = categoryCounts[cat] || 0;
            if (count === 0) return null;
            return (
              <button
                key={cat}
                type="button"
                className={`filter-chip timeline-chip-${meta.tone} ${filterCategory === cat ? "active" : ""}`}
                onClick={() => setFilterCategory(filterCategory === cat ? "" : cat)}
                title={meta.label}
              >
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>{meta.icon}</span>
                {meta.label}
                <span className="chip-count">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="timeline-legend">
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>info</span>
        Stronger left border = legal milestone (FIR / charge / arrest). Other events use
        category icons and spacing — not arbitrary color.
      </div>

      {error && (
        <div className="cl-error">
          <div className="cl-empty-title">Could not load the timeline</div>
          <div className="cl-empty-desc">{error}</div>
          <button className="cl-btn" onClick={load}>Retry</button>
        </div>
      )}

      {loading && !error && (
        <div className="skeleton-timeline">
          {[1,2,3].map((i) => (<div key={i} className="skeleton-line w-80" />))}
        </div>
      )}

      {!loading && !error && !effectiveCaseParam && (
        <div className="cl-empty">
          <div className="cl-empty-title">Choose a case</div>
          <div className="cl-empty-desc">
            The timeline is built from a case's stored records, so it needs a case id.
          </div>
        </div>
      )}

      {!loading && !error && effectiveCaseParam && rows.length === 0 && (
        <div className="cl-empty">
          <div className="cl-empty-title">No timeline events for this case</div>
          <div className="cl-empty-desc">Viewer sees only authorized timeline.</div>
        </div>
      )}

      {!loading && !error && grouped.length > 0 && grouped.every((g) => g.events.length === 0) && (
        <div className="cl-empty">
          <div className="cl-empty-title">No events match this filter</div>
          <div className="cl-empty-desc">Clear the filters to see every recorded event.</div>
        </div>
      )}

      {!loading && !error && (
        <div className="enhanced-timeline">
          {grouped.map(({ key: date, events: dayEvents }) => (
            <div key={date} className="enhanced-timeline-day-group">
              <div className="enhanced-timeline-day-header">
                <span className="enhanced-timeline-day-dot" />
                {date}
                {dayEvents.length > 0 && (
                  <span className="day-event-count">{dayEvents.length} event{dayEvents.length === 1 ? "" : "s"}</span>
                )}
              </div>
              <div className="enhanced-timeline-events">
                {dayEvents.map((ev) => {
                  const meta = CATEGORY_META[ev.category];
                  const milestone = isMilestone(ev.category);
                  return (
                    <div
                      key={ev.key}
                      className={`enhanced-timeline-event timeline-event-cat-${ev.category} ${milestone ? "timeline-milestone" : ""}`}
                    >
                      <div className="enhanced-timeline-event-time">
                        <strong>{timeLabel(ev.timestamp)}</strong>
                        {!ev.timestamp && <span className="time-unavailable">No time</span>}
                      </div>
                      <div className="enhanced-timeline-event-content">
                        <div className="enhanced-timeline-event-meta">
                          <span className={`enhanced-timeline-event-type event-type-badge event-type-${meta.tone}`}>
                            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{meta.icon}</span>
                            {ev.type.replace(/_/g, " ")}
                          </span>
                          {ev.location && (
                            <span className="enhanced-timeline-event-meta-item">
                              <span className="material-symbols-outlined" style={{ fontSize: 13 }}>location_on</span>
                              {ev.location}
                            </span>
                          )}
                        </div>
                        <div className="enhanced-timeline-event-title">{ev.title}</div>
                        {ev.description && ev.description !== ev.title && (
                          <div className="enhanced-timeline-event-desc">{ev.description}</div>
                        )}
                        {ev.participants.length > 0 && (
                          <div className="enhanced-timeline-event-persons">
                            <span className="participants-label">
                              <span className="material-symbols-outlined" style={{ fontSize: 12 }}>group</span>
                              {ev.participants.length}
                            </span>
                            {ev.participants.slice(0, 4).join(", ")}
                            {ev.participants.length > 4 && ` +${ev.participants.length - 4}`}
                          </div>
                        )}
                      </div>
                      <div className="enhanced-timeline-event-evidence">
                        {ev.docIds.length > 0 ? (
                          <button
                            type="button"
                            className="evidence-chip clickable"
                            onClick={() => { setDrawerDocId(ev.docIds[0]); setShowDrawer(true); }}
                            title={`Open source document ${ev.docIds[0]}`}
                          >
                            <span className="material-symbols-outlined" style={{ fontSize: 13 }}>description</span>
                            {ev.docIds[0]}
                            {ev.docIds.length > 1 && <span className="ev-count">+{ev.docIds.length - 1}</span>}
                          </button>
                        ) : (
                          <span className="evidence-chip" style={{ opacity: .6 }}>No source doc</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      <EvidenceDrawer open={showDrawer} onClose={() => setShowDrawer(false)} data={drawerDocId ? { id: drawerDocId } : null} />
    </div>
  );
}
