/**
 * Case Header — Hero screen for Case Workspace
 * Investigation status, People, Relationships, Evidence, Last activity, [Continue Investigation]
 */

interface Props {
  caseId: string;
  status?: string;
  peopleCount?: number;
  relationshipsCount?: number;
  evidenceCount?: number;
  lastActivity?: string;
  onContinue?: () => void;
}

export function CaseHeader({ caseId, status = "Active", peopleCount = 0, relationshipsCount = 0, evidenceCount = 0, lastActivity = "12 min ago", onContinue }: Props) {
  return (
    <div className="case-header-hero">
      <div className="case-header-top">
        <h1 className="case-header-title">CASE #{caseId}</h1>
        <span className="case-status-badge">{status}</span>
      </div>

      <div className="case-header-stats">
        <div className="case-stat">
          <span className="case-stat-value">{peopleCount}</span>
          <span className="case-stat-label">People</span>
        </div>
        <div className="case-stat">
          <span className="case-stat-value">{relationshipsCount}</span>
          <span className="case-stat-label">Relationships</span>
        </div>
        <div className="case-stat">
          <span className="case-stat-value">{evidenceCount}</span>
          <span className="case-stat-label">Evidence</span>
        </div>
        <div className="case-stat">
          <span className="case-stat-value">{lastActivity}</span>
          <span className="case-stat-label">Last activity</span>
        </div>
      </div>

      <button className="cl-btn cl-btn-primary case-continue-btn" onClick={onContinue}>
        Continue Investigation
      </button>

      <div className="case-header-hierarchy">
        <span>Case → People → Relationships → Evidence → Explanation → Action</span>
      </div>
    </div>
  );
}

export default CaseHeader;
