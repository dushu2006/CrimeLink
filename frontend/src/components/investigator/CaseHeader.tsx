/**
 * Case Header — Hero screen for Case Workspace
 * Investigation status, People, Relationships, Evidence, Last activity, [Continue Investigation]
 *
 * Every field is a required prop with no default: a header that quietly prints
 * "Active" or "12 min ago" when the caller forgets a value is reporting an
 * invented fact about a real case.  Where the data genuinely does not exist the
 * caller passes the explicit "not recorded" string below.
 */

interface Props {
  caseId: string;
  status: string;
  peopleCount: number;
  relationshipsCount: number;
  evidenceCount: number;
  /** Human-readable newest record timestamp, or the explicit "no activity" note. */
  lastActivity: string;
  onContinue?: () => void;
}

export function CaseHeader({ caseId, status, peopleCount, relationshipsCount, evidenceCount, lastActivity, onContinue }: Props) {
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
