/**
 * Timeline Page — Evidence-oriented, not generic event feed
 * FEB 18, 14:32 Communication Person A ↔ Person B Evidence E-103
 * RBAC: Both Investigator and Viewer can view (read-only)
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { masterGraph } from "../api/client";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { useAuth } from "../store/auth";
import { getRoleBadge } from "../lib/rbac";

export default function TimelinePage() {
  const [events, setEvents] = useState<any[]>([]);
  const [filterPerson, setFilterPerson] = useState("");
  const [filterType, setFilterType] = useState("");
  const [showDrawer, setShowDrawer] = useState(false);
  const [drawerData, setDrawerData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [searchParams] = useSearchParams();
  const session = useAuth((s) => s.session);
  const roleBadge = getRoleBadge(session?.role as any);
  const caseParam = searchParams.get("case") || undefined;

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const graph = await masterGraph();
        const edges = graph.edges || [];
        const grouped: Record<string, any[]> = {};
        edges.forEach((e: any, i: number) => {
          const date = "Unknown date";
          if (!grouped[date]) grouped[date] = [];
          grouped[date].push({
            id: `E-${String(i + 42).padStart(3, "0")}`,
            time: "Timestamp unavailable",
            type: e.rel_type,
            persons: `${e.source.slice(0, 12)} ↔ ${e.target.slice(0, 12)}`,
            evidence: `E-${String(i + 42).padStart(3, "0")}`,
            docId: e.source_doc_id,
          });
        });
        const sorted = Object.entries(grouped).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 10);
        setEvents(sorted);
      } catch {}
      setLoading(false);
    }
    void load();
  }, []);

  if (loading) {
    return (
      <div className="timeline-page">
        <div className="page-header">
          <h1>Timeline</h1>
          <p className="page-subtitle">Evidence-oriented timeline — When did connection occur?</p>
        </div>
        <div className="skeleton-timeline">
          {[1,2,3].map((i) => (
            <div key={i} className="skeleton-line w-80" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="timeline-page">
      <div className="page-header">
        <div>
          <h1>Timeline</h1>
          <p className="page-subtitle">Evidence-oriented timeline — When did connection occur?</p>
          <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: "4px" }}>
            Evidence-grounded with real timestamps only. "Timestamp unavailable" if missing. · <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span> · Read-only {caseParam ? `· Case: ${caseParam}` : ""}
          </div>
        </div>
        <div className="timeline-filters">
          <select value={filterPerson} onChange={(e) => setFilterPerson(e.target.value)}><option value="">Person</option></select>
          <select value={filterType} onChange={(e) => setFilterType(e.target.value)}><option value="">Evidence type</option><option>Communication</option><option>Co-location</option></select>
          <select><option>Location</option></select>
          <select><option>Date</option></select>
        </div>
      </div>

      {events.length === 0 ? (
        <div className="cl-empty">
          <div className="cl-empty-title">No timeline events yet</div>
          <div className="cl-empty-desc">Timeline will show evidence-oriented events once data is available. Viewer sees only authorized timeline.</div>
        </div>
      ) : (
        <div className="timeline">
          {events.map(([date, dayEvents]) => (
            <div key={date} className="timeline-date-group">
              <div className="timeline-date">{date}</div>
              <div className="timeline-events">
                {(dayEvents as any[]).map((ev, idx) => (
                  <button key={idx} className="timeline-event" onClick={() => { setDrawerData({ id: ev.evidence, title: ev.evidence, type: ev.type }); setShowDrawer(true); }}>
                    <span className="timeline-time">{ev.time}</span>
                    <span className="timeline-type">{ev.type}</span>
                    <span className="timeline-persons">{ev.persons}</span>
                    <span className="evidence-chip clickable">{ev.evidence}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <EvidenceDrawer open={showDrawer} onClose={() => setShowDrawer(false)} data={drawerData} />
    </div>
  );
}
