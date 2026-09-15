/**
 * Investigator Activity — Read-only for Viewer
 * Shows completed investigation activity, NOT admin audit console
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
  evidence: string[];
  evidenceStrength: string;
  classification: string;
  completedAt: string;
  connectionPath?: string[];
  limitations?: string[];
  provenance?: string;
}

interface Props {
  activities?: ActivityItem[];
  onViewEvidence?: (ref: string) => void;
  onViewTimeline?: () => void;
  onViewRelationship?: (subject: string) => void;
}

const DEMO_ACTIVITIES: ActivityItem[] = [
  {
    id: "INV-0042",
    investigator: "DEMO-INVESTIGATOR",
    investigatorName: "Demo Investigator",
    caseId: "case-001-demo",
    caseNumber: "CR-1024",
    subject: "PERSON-001 ↔ PERSON-002",
    finding: "Supported communication relationship",
    evidence: ["E-042", "E-103", "E-118"],
    evidenceStrength: "HIGH",
    classification: "FACT",
    completedAt: "15 Sep 2026",
    connectionPath: ["PERSON-001", "PERSON-002"],
    limitations: ["Purpose of association beyond documented records is unknown", "Criminal intent not established by this evidence alone"],
    provenance: "Verified from CDR and field reports",
  },
  {
    id: "INV-0043",
    investigator: "DEMO-INVESTIGATOR",
    investigatorName: "Demo Investigator",
    caseId: "case-002-demo",
    caseNumber: "CR-1025",
    subject: "PERSON-001 ↔ PERSON-003",
    finding: "Co-location at Location X",
    evidence: ["E-071"],
    evidenceStrength: "MODERATE",
    classification: "INFERENCE",
    completedAt: "15 Sep 2026",
    connectionPath: ["PERSON-001", "PERSON-003"],
    limitations: ["Co-location does not establish direct communication"],
    provenance: "Field report verified",
  },
];

export function InvestigatorActivity({ activities = DEMO_ACTIVITIES, onViewEvidence, onViewTimeline, onViewRelationship }: Props) {
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
              <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
                <strong>Evidence:</strong>
                {activity.evidence.map((ev) => (
                  <button key={ev} className="evidence-chip clickable" onClick={() => onViewEvidence?.(ev)}>{ev} ↗</button>
                ))}
              </div>
              <div><strong>Evidence Strength:</strong> {activity.evidenceStrength} — {activity.evidence.length} independently sourced record(s)</div>
              <div><strong>Classification:</strong> {activity.classification} — {activity.classification === "FACT" ? "What records directly establish" : "What follows reasonably from evidence"}</div>
              <div><strong>Completed:</strong> {activity.completedAt}</div>
              {activity.connectionPath && (
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
                <div><strong>Provenance:</strong> {activity.provenance} ✓ Evidence verified ✓ Source traceable</div>
              )}
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
        Mode: Investigator Activity read-only · Same data scope · Different operation permissions · Backend authorization enforced
      </div>
    </div>
  );
}

export default InvestigatorActivity;
