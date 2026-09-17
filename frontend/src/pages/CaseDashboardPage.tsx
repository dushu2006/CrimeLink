/**
 * Full Case Dashboard Page — Sprint 2
 * Header stats real only intelligence clickable recent activity real
 * Continue Investigation opens workspace restored
 */

import { useParams, useNavigate } from "react-router-dom";
import { CaseDashboardFull } from "../components/investigator/CaseDashboardFull";
import { useState } from "react";
import { EnhancedTimeline } from "../components/investigator/EnhancedTimeline";
import { enhancedTimeline, type EnhancedTimelineEvent } from "../api/client";
import { useEffect } from "react";

export default function CaseDashboardPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const [timelineEvents, setTimelineEvents] = useState<EnhancedTimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  useEffect(() => {
    if (!caseId) return;
    setTimelineLoading(true);
    setTimelineError(null);
    enhancedTimeline(caseId, { limit: 200 })
      .then((res) => setTimelineEvents(res.events))
      // Never render a failed timeline as an empty one: "no events" and "the
      // request failed" are different statements about the case.
      .catch((err: Error) => setTimelineError(err.message))
      .finally(() => setTimelineLoading(false));
  }, [caseId]);

  if (!caseId) return <div className="cl-error">No case ID</div>;

  return (
    <div className="case-dashboard-page">
      <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px" }}>
        <button className="cl-btn" onClick={() => navigate("/cases")}>
          ← Cases
        </button>
        <button className="cl-btn" onClick={() => navigate(`/cases/${caseId}`)}>
          Case Detail
        </button>
        <button className="cl-btn cl-btn-primary" onClick={() => navigate(`/investigate?case=${caseId}`)}>
          Open Workspace
        </button>
      </div>

      <CaseDashboardFull
        caseId={caseId}
        onContinueInvestigation={(id) => navigate(`/investigate?case=${id}`)}
        onFocusEntity={(name) => navigate(`/investigate?focus=${encodeURIComponent(name)}&case=${caseId}`)}
        onOpenEvidence={(docId) => navigate(`/documents/${docId}`)}
      />

      <div style={{ marginTop: "24px" }}>
        <h3 style={{ fontFamily: "var(--font-display)", fontSize: "14px", fontWeight: 700, marginBottom: "12px" }}>Investigation Timeline — Enhanced</h3>
        {timelineLoading ? (
          <div className="cl-empty">
            <div className="loading-spinner" />
            <div className="cl-empty-title">Loading timeline…</div>
          </div>
        ) : timelineError ? (
          <div className="cl-error">
            <div className="cl-empty-title">Could not load the timeline</div>
            <div className="cl-empty-desc">{timelineError}</div>
          </div>
        ) : (
          <EnhancedTimeline
            caseId={caseId}
            events={timelineEvents}
            onOpenEvidence={(docId) => navigate(`/documents/${docId}`)}
            onFocusEntity={(name) => navigate(`/investigate?focus=${encodeURIComponent(name)}&case=${caseId}`)}
            onViewDoc={(docId) => navigate(`/documents/${docId}`)}
          />
        )}
      </div>
    </div>
  );
}
