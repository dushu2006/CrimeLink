/**
 * Case Dashboard — Professional case overview (Priority 6 in UX redesign)
 * 
 * When opening a case:
 * CASE CR-2026-003
 * Status: Active
 * Investigator: John Doe
 * Created: 12 Aug 2026
 * ENTITIES 1,284 | RELATIONSHIPS 4,721 | EVIDENCE 382 | DOCUMENTS 147 | PATTERNS 23
 * RECENT ACTIVITY
 */

import { Link } from "react-router-dom";
import { Badge } from "../Status";

export interface CaseDashboardData {
  caseId: string;
  caseNumber?: string;
  title: string;
  status: string;
  investigator?: string;
  createdAt?: string;
  jurisdiction?: string;
  entities: number;
  relationships: number;
  evidence: number;
  documents: number;
  patterns: number;
  recentActivity?: Array<{ time: string; action: string; actor?: string }>;
}

interface CaseDashboardProps {
  data: CaseDashboardData;
  onInvestigate?: () => void;
}

export function CaseDashboard({ data, onInvestigate }: CaseDashboardProps) {
  return (
    <div className="case-dashboard">
      <header className="case-dashboard-header">
        <div>
          <span className="case-dashboard-kicker">CASE {data.caseNumber || data.caseId.slice(0, 8)}</span>
          <h1 className="case-dashboard-title">{data.title}</h1>
        </div>
        <div className="case-dashboard-actions">
          <Badge value={data.status} />
          {onInvestigate && (
            <button className="btn btn-primary" onClick={onInvestigate}>
              Investigate
            </button>
          )}
        </div>
      </header>

      <div className="case-dashboard-meta">
        <div className="case-dashboard-field">
          <span className="case-dashboard-label">Status</span>
          <span className="case-dashboard-value">
            <span className={`status-dot status-${data.status.toLowerCase()}`}>●</span> {data.status}
          </span>
        </div>
        {data.investigator && (
          <div className="case-dashboard-field">
            <span className="case-dashboard-label">Investigator</span>
            <span className="case-dashboard-value">{data.investigator}</span>
          </div>
        )}
        {data.createdAt && (
          <div className="case-dashboard-field">
            <span className="case-dashboard-label">Created</span>
            <span className="case-dashboard-value">{new Date(data.createdAt).toLocaleDateString()}</span>
          </div>
        )}
        {data.jurisdiction && (
          <div className="case-dashboard-field">
            <span className="case-dashboard-label">Jurisdiction</span>
            <span className="case-dashboard-value">{data.jurisdiction}</span>
          </div>
        )}
      </div>

      <div className="case-dashboard-stats">
        <div className="case-stat-card">
          <span className="case-stat-value">{data.entities.toLocaleString()}</span>
          <span className="case-stat-label">ENTITIES</span>
          <Link to={`/cases/${data.caseId}/entities`} className="case-stat-link">View →</Link>
        </div>
        <div className="case-stat-card">
          <span className="case-stat-value">{data.relationships.toLocaleString()}</span>
          <span className="case-stat-label">RELATIONSHIPS</span>
          <Link to={`/cases/${data.caseId}/graph`} className="case-stat-link">View →</Link>
        </div>
        <div className="case-stat-card">
          <span className="case-stat-value">{data.evidence.toLocaleString()}</span>
          <span className="case-stat-label">EVIDENCE</span>
          <Link to={`/cases/${data.caseId}/evidence`} className="case-stat-link">View →</Link>
        </div>
        <div className="case-stat-card">
          <span className="case-stat-value">{data.documents.toLocaleString()}</span>
          <span className="case-stat-label">DOCUMENTS</span>
          <Link to={`/cases/${data.caseId}/documents`} className="case-stat-link">View →</Link>
        </div>
        <div className="case-stat-card">
          <span className="case-stat-value">{data.patterns.toLocaleString()}</span>
          <span className="case-stat-label">PATTERNS</span>
          <Link to={`/cases/${data.caseId}/patterns`} className="case-stat-link">View →</Link>
        </div>
      </div>

      {data.recentActivity && data.recentActivity.length > 0 && (
        <div className="case-dashboard-activity">
          <h3 className="case-dashboard-section-title">RECENT ACTIVITY</h3>
          <ul className="activity-list">
            {data.recentActivity.slice(0, 8).map((activity, idx) => (
              <li key={idx} className="activity-item">
                <span className="activity-time">{activity.time}</span>
                <span className="activity-action">{activity.action}</span>
                {activity.actor && <span className="activity-actor">{activity.actor}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// Compact version for sidebar
export function CaseDashboardCompact({ data }: { data: CaseDashboardData }) {
  return (
    <div className="case-dashboard-compact">
      <div className="case-dashboard-compact-header">
        <span className="case-dashboard-kicker">CASE</span>
        <span className="case-dashboard-compact-title">{data.title.slice(0, 30)}</span>
        <Badge value={data.status} />
      </div>
      <div className="case-dashboard-compact-stats">
        <span>{data.entities} entities</span>
        <span>·</span>
        <span>{data.relationships} rels</span>
        <span>·</span>
        <span>{data.documents} docs</span>
      </div>
    </div>
  );
}
