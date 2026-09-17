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

      <div className="activity-list" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        {activities.map((activity) => (
          <div key={activity.id} className="activity-card" style={{ background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "8px", padding: "16px" }}>
            <div className="activity-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}>
              <div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: "11px", fontWeight: 600 }}>Investigation: {activity.id}</div>
                <div style={{ fontSize: "12px", color: "var(--muted)" }}>Investigator: {activity.investigatorName || activity.investigator} ({activity.investigator})</div>
                <div style={{ fontSize: "12px", color: "var(--muted)" }}>Case: {activity.caseNumber || activity.caseId}</div>
              </div>
              <div style={{ display: "flex", gap: "6px" }}>
                <span className={`badge badge-${activity.evidenceStrength === "HIGH" ? "success" : activity.evidenceStrength === "MODERATE" ? "info" : "warn"}`}>{activity.evidenceStrength}</span>
                <span className={`badge badge-${activity.classification === "FACT" ? "success" : "info"}`}>{activity.classification}</span>
              </div>
            </div>

            <div className="activity-body" style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "12px" }}>
              <div><strong>Subject:</strong> {activity.subject}</div>
              <div><strong>Finding:</strong> {activity.finding}</div>
              {activity.narrative && <div><strong>Narrative:</strong> {activity.narrative}</div>}
              {activity.reason && <div><strong>Reason:</strong> {activity.reason}</div>}
              <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                <strong>Evidence:</strong>
                {activity.evidence.length > 0 ? activity.evidence.map((ev) => (
                  <button key={ev} className="evidence-chip clickable" onClick={() => onViewEvidence?.(ev)}>{ev} ↗</button>
                )) : <span style={{ color: "#94a3b8" }}>No evidence refs</span>}
              </div>
              <div><strong>Evidence Strength:</strong> {activity.evidenceStrength} — {activity.evidence.length} independently sourced record(s)</div>
              <div><strong>Classification:</strong> {activity.classification} — {activity.classification === "FACT" ? "What records directly establish" : "What follows reasonably from evidence"}</div>
              {activity.confidence !== undefined && <div><strong>Confidence:</strong> {(activity.confidence * 100).toFixed(0)}% ({activity.confidenceBand})</div>}
              <div><strong>Completed:</strong> {activity.completedAt}</div>
              {activity.connectionPath && activity.connectionPath.length > 0 && (
                <div><strong>Connection Path:</strong> {activity.connectionPath.join(" → ")}</div>
              )}
              {activity.limitations && activity.limitations.length > 0 && (
                <div>
                  <strong>Limitations — What does NOT establish:</strong>
                  <ul style={{ margin: "4px 0 0 16px", padding: 0, fontSize: "11px", color: "#475569" }}>
                    {activity.limitations.map((lim, idx) => (
                      <li key={idx}>⚠ {lim}</li>
                    ))}
                  </ul>
                </div>
              )}
              {activity.provenance && (
                <div>
                  <strong>Provenance:</strong> {activity.provenance}
                </div>
              )}
              {/* The ticks used to be literals printed next to every finding.
                  They are now the backend's computed checks, with the reason
                  shown when a check fails. */}
              {activity.provenanceChecks && (
                <div className="provenance-checks" title={activity.provenanceChecks.detail}>
                  <span
                    className={`provenance-check ${activity.provenanceChecks.evidence_verified ? "verified" : "unverified"}`}
                  >
                    {activity.provenanceChecks.evidence_verified ? "✓" : "✗"} Evidence verified
                  </span>
                  <span
                    className={`provenance-check ${activity.provenanceChecks.source_traceable ? "verified" : "unverified"}`}
                  >
                    {activity.provenanceChecks.source_traceable ? "✓" : "✗"} Source traceable
                  </span>
                  {!activity.provenanceChecks.evidence_verified ||
                  !activity.provenanceChecks.source_traceable ? (
                    <span className="muted">{activity.provenanceChecks.detail}</span>
                  ) : null}
                </div>
              )}
              {activity.status && <div><strong>Status:</strong> {activity.status} | <strong>Method:</strong> {activity.method}</div>}
            </div>

            <div className="activity-actions" style={{ marginTop: "12px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              <button className="cl-btn cl-btn-sm" onClick={() => onViewRelationship?.(activity.subject)}>View Relationship — What connects them?</button>
              <button className="cl-btn cl-btn-sm" onClick={() => activity.evidence[0] && onViewEvidence?.(activity.evidence[0])}>View Evidence — What supports?</button>
              <button className="cl-btn cl-btn-sm" onClick={() => onViewTimeline?.()}>View Timeline — When?</button>
            </div>

            <div style={{ marginTop: "12px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "#94a3b8", borderTop: "1px solid #f1f5f9", paddingTop: "8px" }}>
              Read-only — Viewer cannot rerun, modify, delete, approve/change, or create another investigation. Based only on evidence shown.
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
