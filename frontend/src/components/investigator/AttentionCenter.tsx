/**
 * Attention Center — Sprint 2
 * Not generic notification: categories Critical / Investigation / Data quality / Evidence
 * Only real data, links directly to objects
 * Build UI + interface for future events
 */

import { useEffect, useState } from "react";
import { attentionCenter, type AttentionCenter, type AttentionItem } from "../../api/client";
import { useNavigate } from "react-router-dom";

interface Props {
  onFocusEntity?: (key: string) => void;
  onOpenEvidence?: (docId: string) => void;
  className?: string;
}

type Category = "critical" | "investigation" | "data_quality" | "evidence";

const CATEGORY_LABELS: Record<Category, string> = {
  critical: "Critical",
  investigation: "Investigation",
  data_quality: "Data Quality",
  evidence: "Evidence",
};

const CATEGORY_ICONS: Record<Category, string> = {
  critical: "🚨",
  investigation: "🔍",
  data_quality: "⚠️",
  evidence: "📄",
};

export function AttentionCenterPanel({ onFocusEntity, onOpenEvidence, className }: Props) {
  const navigate = useNavigate();
  const [data, setData] = useState<AttentionCenter | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Category>("critical");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await attentionCenter();
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleItemClick = (item: AttentionItem) => {
    const link = item.link;
    if (!link) return;
    switch (link.kind) {
      case "pattern":
        if (link.case_id) navigate(`/cases/${link.case_id}`);
        break;
      case "finding":
        if (link.case_id) navigate(`/cases/${link.case_id}`);
        break;
      case "document":
        if (link.document_id) onOpenEvidence?.(link.document_id);
        break;
      case "entity":
        if (link.entity_key) onFocusEntity?.(link.entity_key);
        break;
      case "resolution":
        if (link.case_id) navigate(`/cases/${link.case_id}`);
        break;
      case "case":
        if (link.case_id) navigate(`/cases/${link.case_id}`);
        break;
      default:
        if (link.case_id) navigate(`/cases/${link.case_id}`);
    }
  };

  const currentItems = data ? (data.categories[activeTab] as AttentionItem[]) : [];

  return (
    <div className={`attention-center ${className || ""}`}>
      <div className="attention-center-header">
        <h3 className="attention-center-title">
          <span>Attention Center</span>
          {data && data.total > 0 && <span className="attention-center-count">{data.total}</span>}
        </h3>
        <button className="cl-btn" style={{ fontSize: "11px", padding: "2px 8px" }} onClick={() => void load()} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && <div className="cl-error">{error}</div>}

      {data && (
        <div className="attention-center-tabs">
          {(Object.keys(CATEGORY_LABELS) as Category[]).map((cat) => (
            <button
              key={cat}
              className={`attention-center-tab ${cat} ${activeTab === cat ? "active" : ""}`}
              onClick={() => setActiveTab(cat)}
            >
              <span>{CATEGORY_ICONS[cat]}</span>
              <span>{CATEGORY_LABELS[cat]}</span>
              <span className="attention-center-tab-count">{data.counts[cat] || 0}</span>
            </button>
          ))}
        </div>
      )}

      {loading && !data && (
        <div className="cl-empty">
          <div className="loading-spinner" />
          <div className="cl-empty-title">Loading attention items…</div>
          <div className="cl-empty-desc">Aggregating real data: critical findings, investigation signals, data quality issues, evidence</div>
        </div>
      )}

      {data && currentItems.length === 0 && (
        <div className="attention-empty">
          No {CATEGORY_LABELS[activeTab].toLowerCase()} items. Real data only — no fabrication. This view will surface future events via the same interface.
        </div>
      )}

      {data && currentItems.length > 0 && (
        <div className="attention-center-list">
          {currentItems.map((item) => (
            <div key={`${item.type}-${item.id}`} className="attention-item" onClick={() => handleItemClick(item)}>
              <div className={`attention-item-severity ${item.severity}`} />
              <div className="attention-item-content">
                <span className="attention-item-title">{item.title}</span>
                <span className="attention-item-desc">{item.description}</span>
                <div className="attention-item-meta">
                  <span className="attention-item-time">{item.timestamp ? new Date(item.timestamp).toLocaleString() : "—"}</span>
                  <span className="attention-item-type">{item.type}</span>
                  {item.case_id && <span className="attention-item-type">Case {item.case_id.slice(0, 8)}</span>}
                </div>
              </div>
              <div className="attention-item-action">
                <button className="cl-btn" style={{ fontSize: "10px", padding: "2px 6px" }}>
                  Open →
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: "10px", color: "var(--text-tertiary)", fontStyle: "italic", marginTop: "4px" }}>
        Attention Center uses real data only — patterns, findings, unresolved entities, documents. No generic notifications. Future events will use the same interface.
      </div>
    </div>
  );
}

export default AttentionCenterPanel;
