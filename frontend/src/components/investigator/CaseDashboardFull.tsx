/**
 * Full Case Dashboard — expand compact to full page
 * Header ID/title/status/owner/timestamps/description
 * Real stats entities/relationships/evidence/docs/patterns/unresolved
 * Intelligence high-priority/gaps/unresolved/patterns/activity clickable
 * Recent activity real
 * Continue Investigation opens workspace restored
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { caseDashboard, type CaseDashboard } from "../../api/client";

interface Props {
  caseId: string;
  onContinueInvestigation?: (caseId: string) => void;
  onFocusEntity?: (name: string) => void;
  onOpenEvidence?: (docId: string) => void;
  className?: string;
}

export function CaseDashboardFull({ caseId, onContinueInvestigation, onFocusEntity, onOpenEvidence, className }: Props) {
  const navigate = useNavigate();
  const [data, setData] = useState<CaseDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await caseDashboard(caseId);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [caseId]);

  const handleContinue = () => {
    // Save caseId to localStorage for workspace restore
    try {
      localStorage.setItem("crimelink:continue-case", caseId);
      // Also restore investigation state if exists
      const stateKey = `crimelink:investigation-state:master:current`;
      const raw = localStorage.getItem(stateKey);
      if (raw) {
        const state = JSON.parse(raw);
        state.focus = caseId;
        localStorage.setItem(stateKey, JSON.stringify(state));
      }
    } catch {}
    if (onContinueInvestigation) onContinueInvestigation(caseId);
    else navigate(`/investigate?case=${caseId}`);
  };

  if (loading && !data) {
    return (
      <div className={`case-dashboard-full ${className || ""}`}>
        <div className="cl-empty">
          <div className="loading-spinner" />
          <div className="cl-empty-title">Loading case dashboard…</div>
          <div className="cl-empty-desc">Aggregating real stats: entities, relationships, evidence, docs, patterns, unresolved</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`case-dashboard-full ${className || ""}`}>
        <div className="cl-error">{error}</div>
        <button className="cl-btn" onClick={() => void load()}>Retry</button>
      </div>
    );
  }

  if (!data) return null;

  const { header, stats, intelligence } = data;

  return (
    <div className={`case-dashboard-full ${className || ""}`}>
      <div className="case-dashboard-full-header">
        <div className="case-dashboard-full-header-top">
          <div className="case-dashboard-full-title-group">
            <span className="case-dashboard-full-id">Case {header.case_number} · {header.id.slice(0, 8)}</span>
            <h1 className="case-dashboard-full-title">{header.title}</h1>
            <span className="case-dashboard-full-subtitle">{header.description}</span>
            <div className="case-dashboard-full-meta">
              <div className="case-dashboard-full-meta-item">
                <span className="case-dashboard-full-meta-label">Status</span>
                <span className="case-dashboard-full-meta-value">
                  <span className={`cl-badge ${header.status === "OPEN" ? "cl-badge-success" : header.status === "CLOSED" ? "cl-badge" : "cl-badge-warning"}`}>{header.status}</span>
                </span>
              </div>
              <div className="case-dashboard-full-meta-item">
                <span className="case-dashboard-full-meta-label">Jurisdiction</span>
                <span className="case-dashboard-full-meta-value">{header.jurisdiction_id}</span>
              </div>
              <div className="case-dashboard-full-meta-item">
                <span className="case-dashboard-full-meta-label">Created</span>
                <span className="case-dashboard-full-meta-value">{header.created_at ? new Date(header.created_at).toLocaleString() : "—"}</span>
              </div>
              {header.closed_at && (
                <div className="case-dashboard-full-meta-item">
                  <span className="case-dashboard-full-meta-label">Closed</span>
                  <span className="case-dashboard-full-meta-value">{new Date(header.closed_at).toLocaleString()}</span>
                </div>
              )}
              <div className="case-dashboard-full-meta-item">
                <span className="case-dashboard-full-meta-label">Dataset</span>
                <span className="case-dashboard-full-meta-value">{header.dataset_id?.slice(0, 8) || "—"}</span>
              </div>
            </div>
          </div>
          <div className="case-dashboard-full-actions">
            <button className="cl-btn cl-btn-primary" onClick={handleContinue}>
              ▶ Continue Investigation
            </button>
            <button className="cl-btn" onClick={() => navigate(`/cases/${caseId}`)}>
              View Case Detail
            </button>
            <button className="cl-btn" onClick={() => void load()}>
              Refresh
            </button>
          </div>
        </div>

        <div className="case-dashboard-full-stats">
          <div className="case-dashboard-stat-card" onClick={() => navigate(`/graph?case=${caseId}`)}>
            <span className="case-dashboard-stat-label">Entities</span>
            <span className="case-dashboard-stat-value">{stats.entities.toLocaleString()}</span>
            <span className="case-dashboard-stat-sub">{Object.entries(stats.entities_by_label).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(" · ")}</span>
          </div>
          <div className="case-dashboard-stat-card" onClick={() => navigate(`/graph?case=${caseId}`)}>
            <span className="case-dashboard-stat-label">Relationships</span>
            <span className="case-dashboard-stat-value">{stats.relationships.toLocaleString()}</span>
            <span className="case-dashboard-stat-sub">{Object.entries(stats.relationships_by_type).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(" · ")}</span>
          </div>
          <div className="case-dashboard-stat-card">
            <span className="case-dashboard-stat-label">Evidence</span>
            <span className="case-dashboard-stat-value">{stats.evidence}</span>
            <span className="case-dashboard-stat-sub">Unique source docs</span>
          </div>
          <div className="case-dashboard-stat-card" onClick={() => navigate(`/cases/${caseId}`)}>
            <span className="case-dashboard-stat-label">Documents</span>
            <span className="case-dashboard-stat-value">{stats.documents}</span>
            <span className="case-dashboard-stat-sub">Uploaded files</span>
          </div>
          <div className="case-dashboard-stat-card">
            <span className="case-dashboard-stat-label">Patterns</span>
            <span className="case-dashboard-stat-value">{stats.patterns}</span>
            <span className="case-dashboard-stat-sub">Detected signals</span>
          </div>
          <div className="case-dashboard-stat-card" style={stats.unresolved > 0 ? { borderColor: "var(--warning-border)", background: "var(--warning-bg)" } : {}}>
            <span className="case-dashboard-stat-label">Unresolved</span>
            <span className="case-dashboard-stat-value" style={stats.unresolved > 0 ? { color: "var(--warning)" } : {}}>{stats.unresolved}</span>
            <span className="case-dashboard-stat-sub">Pending resolution</span>
          </div>
        </div>
      </div>

      <div className="case-dashboard-full-grid">
        <div className="case-dashboard-intelligence">
          <div className="case-dashboard-intel-section">
            <h3 className="case-dashboard-intel-title">
              <span>🚨 High Priority</span>
              <span className="cl-badge cl-badge-critical">{intelligence.high_priority.length}</span>
            </h3>
            <div className="case-dashboard-intel-list">
              {intelligence.high_priority.length === 0 ? (
                <div className="cl-empty" style={{ padding: "12px" }}>
                  <div className="cl-empty-desc">No high priority items — real data only</div>
                </div>
              ) : (
                intelligence.high_priority.slice(0, 8).map((item: any) => (
                  <div key={item.id} className="case-dashboard-intel-item" onClick={() => navigate(`/cases/${caseId}`)}>
                    <span className="case-dashboard-intel-item-dot critical" />
                    <div className="case-dashboard-intel-item-content">
                      <span className="case-dashboard-intel-item-title">{item.type === "pattern" ? `${item.pattern_type} — ${item.confidence?.toFixed(2)}` : item.question || item.title}</span>
                      <span className="case-dashboard-intel-item-desc">{item.explanation || item.description || `${item.type} — ${item.status}`}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="case-dashboard-intel-section">
            <h3 className="case-dashboard-intel-title">
              <span>⚠️ Evidence Gaps</span>
              <span className="cl-badge cl-badge-warning">{intelligence.gaps.length}</span>
            </h3>
            <div className="case-dashboard-intel-list">
              {intelligence.gaps.length === 0 ? (
                <div className="cl-empty" style={{ padding: "12px" }}>
                  <div className="cl-empty-desc">No gaps detected</div>
                </div>
              ) : (
                intelligence.gaps.slice(0, 8).map((gap: any) => (
                  <div key={gap.id} className="case-dashboard-intel-item">
                    <span className="case-dashboard-intel-item-dot warning" />
                    <div className="case-dashboard-intel-item-content">
                      <span className="case-dashboard-intel-item-title">{gap.type} — {gap.canonical_id || gap.pattern_type || gap.id.slice(0, 8)}</span>
                      <span className="case-dashboard-intel-item-desc">{gap.reason || gap.explanation || "Needs review"}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="case-dashboard-intel-section">
            <h3 className="case-dashboard-intel-title">
              <span>📊 Patterns</span>
              <span className="cl-badge cl-badge-info">{intelligence.patterns.length}</span>
            </h3>
            <div className="case-dashboard-intel-list">
              {intelligence.patterns.slice(0, 10).map((pat: any) => (
                <div key={pat.id} className="case-dashboard-intel-item" onClick={() => navigate(`/cases/${caseId}`)}>
                  <span className="case-dashboard-intel-item-dot info" />
                  <div className="case-dashboard-intel-item-content">
                    <span className="case-dashboard-intel-item-title">{pat.pattern_type} — {pat.confidence?.toFixed(2)} — {pat.status}</span>
                    <span className="case-dashboard-intel-item-desc">{pat.entity_count} entities · Click to investigate</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="case-dashboard-intelligence">
          <div className="case-dashboard-intel-section">
            <h3 className="case-dashboard-intel-title">
              <span>🕒 Recent Activity — Real</span>
            </h3>
            <div className="case-dashboard-recent-activity">
              {intelligence.recent_activity.length === 0 ? (
                <div className="cl-empty" style={{ padding: "12px" }}>
                  <div className="cl-empty-desc">No recent activity</div>
                </div>
              ) : (
                intelligence.recent_activity.slice(0, 15).map((act: any, idx: number) => (
                  <div key={`${act.type}-${act.id}-${idx}`} className="case-dashboard-activity-item">
                    <span className="case-dashboard-activity-time">{act.timestamp ? new Date(act.timestamp).toLocaleDateString() : "—"}</span>
                    <span className="case-dashboard-activity-text">
                      <span className={`cl-badge ${act.type === "document" ? "cl-badge-info" : act.type === "pattern" ? "cl-badge-warning" : "cl-badge"}`} style={{ fontSize: "9px", marginRight: "6px" }}>
                        {act.type}
                      </span>
                      {act.description || act.title}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="case-dashboard-intel-section">
            <h3 className="case-dashboard-intel-title">Unresolved Entities</h3>
            <div className="case-dashboard-intel-list">
              {intelligence.unresolved.length === 0 ? (
                <div className="cl-empty" style={{ padding: "12px" }}>
                  <div className="cl-empty-desc">No unresolved entities</div>
                </div>
              ) : (
                intelligence.unresolved.map((u: any) => (
                  <div key={u.id} className="case-dashboard-intel-item">
                    <span className="case-dashboard-intel-item-dot warning" />
                    <div className="case-dashboard-intel-item-content">
                      <span className="case-dashboard-intel-item-title">{u.canonical_id}</span>
                      <span className="case-dashboard-intel-item-desc">{u.member_count} potential duplicates</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CaseDashboardFull;
