/**
 * Full Case Dashboard Page — Sprint 2
 * Header stats real only intelligence clickable recent activity real
 * Continue Investigation opens workspace restored
 */

import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { CaseDashboardFull } from "../components/investigator/CaseDashboardFull";
import { useState } from "react";
import { EnhancedTimeline } from "../components/investigator/EnhancedTimeline";
import { enhancedTimeline, type EnhancedTimelineEvent } from "../api/client";
import { useEffect } from "react";
import { api } from "../api/client";

export default function CaseDashboardPage() {
  const { caseId: routeCaseId } = useParams<{ caseId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryCaseId = searchParams.get("case");
  const [resolvedCaseId, setResolvedCaseId] = useState<string | null>(routeCaseId || queryCaseId);
  const [caseResolveError, setCaseResolveError] = useState<string | null>(null);
  const caseId = resolvedCaseId; 
  const [timelineEvents, setTimelineEvents] = useState<EnhancedTimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  useEffect(() => {
    if (routeCaseId || queryCaseId) {
      setResolvedCaseId(routeCaseId || queryCaseId);
      return;
    }
    setCaseResolveError(null);
    api<{ items: Array<{ id: string }> }>("/cases?limit=1")
      .then((data) => {
        const first = data.items?.[0]?.id;
        if (!first) {
          setCaseResolveError("No cases are available in the active dataset.");
          return;
        }
        setResolvedCaseId(first);
        navigate(`/cases/dashboard?case=${encodeURIComponent(first)}`, { replace: true });
      })
      .catch((err: Error) => setCaseResolveError(err.message));
  }, [routeCaseId, queryCaseId, navigate]);

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

  if (caseResolveError) {
    return (
      <div className="cl-error">
        <div className="cl-empty-title">Could not resolve the active case</div>
        <div className="cl-empty-desc">{caseResolveError}</div>
        <button className="cl-btn" onClick={() => window.location.reload()}>Retry</button>
      </div>
    );
  }
  if (!caseId) {
    return <div className="cl-empty"><div className="cl-empty-title">Loading active case…</div></div>;
  }

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