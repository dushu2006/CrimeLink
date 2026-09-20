/**
 * Investigator Activity — Read-only for Viewer
 * Shows completed investigation activity from real persisted DB records
 * No hardcoded fallback — shows Unavailable if backend fails
 * Minimal: Investigation, Investigator, Case, Subject, Finding, Evidence, Strength, Classification, Completed
 */

interface ActivityItem {
  id: string;
  investigator: string;
  investigatorName?: string;
  caseId: string;
  caseNumber?: string;
  subject: string;
  finding: string;
  narrative?: string;
  reason?: string;
  evidence: string[];
  evidenceStrength: string;
  classification: string;
  completedAt: string;
  connectionPath?: string[];
  limitations?: string[];
  provenance?: string;
  /** Computed by the backend from stored records — never asserted by the UI. */
  provenanceChecks?: {
    evidence_verified: boolean;
    source_traceable: boolean;
    evidence_cited: number;
    evidence_resolved: number;
    detail: string;
  } | null;
  confidence?: number;
  confidenceBand?: string;
  status?: string;
  method?: string;
  entityKeys?: string[];
}

interface Props {
  activities: ActivityItem[];
  onViewEvidence?: (ref: string) => void;
  onViewTimeline?: () => void;
  onViewRelationship?: (subject: string) => void;
}

export function InvestigatorActivity({ activities, onViewEvidence, onViewTimeline, onViewRelationship }: Props) {
  if (!activities || activities.length === 0) {
    return (
      <div className="investigator-activity">
        <div className="page-header">
          <h1>Investigator Activity</h1>
          <p className="page-subtitle">Completed investigations — read-only review for higher authority</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Activity</div>
        </div>
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: "8px", padding: "24px", textAlign: "center" }}>
          <div style={{ fontSize: "14px", color: "#64748b", fontFamily: "var(--font-mono)" }}>
            No investigation activity available — run seed_demo.py or check MinIO/PostgreSQL availability
          </div>
          <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "8px" }}>
            Status: Unavailable — no fake data shown
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="investigator-activity">
      <div className="page-header">
        <h1>Investigator Activity</h1>
        <p className="page-subtitle">Completed investigations — read-only review for higher authority</p>
        <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Activity</div>
        <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: "4px" }}>
          Viewer can inspect findings, evidence, connection path, provenance, timeline, limitations, investigator identity — cannot rerun/modify/delete
        </div>
      </div>

      <div className="activity-list" style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
        {activities.map((activity) => (
          <div key={activity.id} className="activity-card-executive">
            {/* Header: Investigation ID, Investigator & Case info + Status Badges */}
            <div className="activity-exec-header">
              <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <span style={{ fontFamily: "var(--cl-font-mono)", fontSize: "12px", fontWeight: 800, color: "var(--cl-accent)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
                    Investigation {activity.id}
                  </span>
                  <span style={{ fontSize: "11px", color: "var(--cl-text-3)" }}>•</span>
                  <span style={{ fontFamily: "var(--cl-font-mono)", fontSize: "12px", fontWeight: 700, color: "var(--cl-ink)" }}>
                    Case: {activity.caseNumber || activity.caseId}
                  </span>
                </div>
                <div style={{ fontSize: "12.5px", color: "var(--cl-text-2)", marginTop: "2px" }}>
                  <strong>Investigator:</strong> {activity.investigatorName || activity.investigator}
                  <span style={{ color: "var(--cl-text-3)", marginLeft: "4px" }}>({activity.investigator})</span>
                </div>
              </div>

              <div className="activity-exec-badges">
                {activity.confidence !== undefined && (
                  <span className="cl-badge cl-badge-info" style={{ fontFamily: "var(--cl-font-mono)", fontWeight: 700 }}>
                    {(activity.confidence * 100).toFixed(0)}% Confidence
                  </span>
                )}
                <span className={`cl-badge ${activity.evidenceStrength === "HIGH" ? "cl-badge-success" : activity.evidenceStrength === "MODERATE" ? "cl-badge-info" : "cl-badge-warning"}`}>
                  Strength: {activity.evidenceStrength}
                </span>
                <span className={`cl-badge ${activity.classification === "FACT" ? "cl-badge-success" : "cl-badge-accent"}`}>
                  {activity.classification}
                </span>
              </div>
            </div>

            {/* Finding Banner */}
            <div className="activity-summary-banner">
              <div className="activity-finding-heading">
                <span className="material-symbols-outlined" style={{ fontSize: 18, color: "var(--cl-accent)" }}>verified</span>
                Finding: {activity.finding}
              </div>
              {activity.narrative && (
                <div style={{ fontSize: "13px", color: "var(--cl-text)", lineHeight: 1.55 }}>
                  {activity.narrative}
                </div>
              )}
            </div>

            {/* Metrics & Metadata Grid */}
            <div className="activity-metrics-grid">
              <div className="activity-metric-box">
                <span className="activity-metric-label">Subject ID</span>
                <span className="activity-metric-value" style={{ fontFamily: "var(--cl-font-mono)", fontSize: "11.5px", wordBreak: "break-all" }}>
                  {activity.subject}
                </span>
              </div>

              <div className="activity-metric-box">
                <span className="activity-metric-label">Classification Basis</span>
                <span className="activity-metric-value" style={{ fontSize: "12px" }}>
                  {activity.classification === "FACT" ? "Directly established by records" : "Reasonable inference from evidence"}
                </span>
              </div>

              <div className="activity-metric-box">
                <span className="activity-metric-label">Evidence Grounding</span>
                <span className="activity-metric-value">
                  {activity.evidence.length} record(s) · {activity.evidenceStrength}
                </span>
              </div>

              <div className="activity-metric-box">
                <span className="activity-metric-label">Completed Timestamp</span>
                <span className="activity-metric-value" style={{ fontFamily: "var(--cl-font-mono)", fontSize: "11.5px" }}>
                  {activity.completedAt ? new Date(activity.completedAt).toLocaleString() : "—"}
                </span>
              </div>
            </div>

            {/* Evidence & Path Section */}
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div className="activity-section-block">
                <span className="activity-section-heading">
                  <span className="material-symbols-outlined" style={{ fontSize: 14 }}>description</span>
                  Supporting Evidence Sources ({activity.evidence.length})
                </span>
                <div className="activity-evidence-wrap">
                  {activity.evidence.length > 0 ? (
                    activity.evidence.map((ev) => (
                      <button key={ev} className="activity-evidence-chip" onClick={() => onViewEvidence?.(ev)} title={`Open evidence document ${ev}`}>
                        <span className="material-symbols-outlined" style={{ fontSize: 12 }}>attachment</span>
                        {ev}
                      </button>
                    ))
                  ) : (
                    <span style={{ color: "var(--cl-text-3)", fontSize: "12px" }}>No evidence references recorded</span>
                  )}
                </div>
              </div>

              {activity.connectionPath && activity.connectionPath.length > 0 && (
                <div className="activity-section-block">
                  <span className="activity-section-heading">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>timeline</span>
                    Connection Path
                  </span>
                  <div style={{ fontFamily: "var(--cl-font-mono)", fontSize: "12px", background: "var(--cl-surface-2)", padding: "8px 12px", borderRadius: "var(--cl-r-md)", border: "1px solid var(--cl-border)", wordBreak: "break-all" }}>
                    {activity.connectionPath.join(" → ")}
                  </div>
                </div>
              )}

              {activity.limitations && activity.limitations.length > 0 && (
                <div className="activity-section-block">
                  <span className="activity-section-heading" style={{ color: "var(--cl-warning)" }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>warning</span>
                    Investigation Boundaries & Limitations
                  </span>
                  <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "12px", color: "var(--cl-text-2)", display: "flex", flexDirection: "column", gap: "4px" }}>
                    {activity.limitations.map((lim, idx) => (
                      <li key={idx}>{lim}</li>
                    ))}
                  </ul>
                </div>
              )}

              {activity.provenance && (
                <div className="activity-section-block">
                  <span className="activity-section-heading">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>history_edu</span>
                    Provenance & Reason
                  </span>
                  <div style={{ fontSize: "12.5px", color: "var(--cl-text-2)" }}>
                    {activity.provenance}
                  </div>
                </div>
              )}

              {/* Provenance Verifications */}
              {activity.provenanceChecks && (
                <div className="provenance-checks" title={activity.provenanceChecks.detail}>
                  <div className="activity-provenance-row">
                    <span className={`provenance-check ${activity.provenanceChecks.evidence_verified ? "verified" : "unverified"}`}>
                      {activity.provenanceChecks.evidence_verified ? "✓" : "✗"} Evidence verified
                    </span>
                    <span className={`provenance-check ${activity.provenanceChecks.source_traceable ? "verified" : "unverified"}`}>
                      {activity.provenanceChecks.source_traceable ? "✓" : "✗"} Source traceable
                    </span>
                    {(!activity.provenanceChecks.evidence_verified || !activity.provenanceChecks.source_traceable) && (
                      <span className="muted">{activity.provenanceChecks.detail}</span>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Direct Action Links */}
            <div className="activity-exec-actions">
              <button className="cl-btn cl-btn-sm" onClick={() => onViewRelationship?.(activity.subject)}>
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>polyline</span>
                Inspect Relationship Graph
              </button>
              <button className="cl-btn cl-btn-sm" onClick={() => activity.evidence[0] && onViewEvidence?.(activity.evidence[0])}>
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>description</span>
                Inspect Source Evidence
              </button>
              <button className="cl-btn cl-btn-sm" onClick={() => onViewTimeline?.()}>
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>schedule</span>
                Inspect Case Timeline
              </button>
            </div>

            <div style={{ fontSize: "11px", color: "var(--cl-text-3)", borderTop: "1px solid var(--cl-divider)", paddingTop: "8px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "6px" }}>
              <span>Read-only record — Authenticated inspection for supervisory & authority review.</span>
              {activity.status && (
                <span style={{ fontFamily: "var(--cl-font-mono)", fontSize: "10.5px" }}>
                  Status: <strong>{activity.status}</strong> · Method: <strong>{activity.method}</strong>
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
        Mode: Investigator Activity read-only · Same data scope · Different operation permissions · Backend authorization enforced · Real DB records
      </div>
    </div>
  );
}

export default InvestigatorActivity;
