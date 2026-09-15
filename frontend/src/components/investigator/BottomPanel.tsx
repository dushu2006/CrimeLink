/**
 * Bottom Panel — Evidence / Timeline / Selected Entity / Notes
 * Part of 3-pane investigative layout: bottom strip under CASE | GRAPH | INTELLIGENCE
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import type { GraphNodeRow, GraphEdgeRow, TimelineEntry, EvidenceItem, ProvenanceItem } from "../../api/client";
import { Badge } from "../Status";
import { EvidenceList } from "./InvestigatorEvidence";

interface BottomPanelProps {
  selectedNode?: GraphNodeRow | null;
  selectedEdge?: GraphEdgeRow | null;
  timeline?: TimelineEntry[];
  evidence?: EvidenceItem[];
  provenance?: ProvenanceItem[];
  onOpenEvidence?: (id: string) => void;
  onOpenSource?: (pointer: ProvenanceItem) => void;
  pinnedCount?: number;
}

type TabId = "evidence" | "timeline" | "selected" | "provenance";

export function BottomPanel({
  selectedNode,
  selectedEdge,
  timeline,
  evidence,
  provenance,
  onOpenEvidence,
  onOpenSource,
  pinnedCount,
}: BottomPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>("selected");

  const tabs: Array<{ id: TabId; label: string; count?: number }> = [
    { id: "selected", label: "Selected Entity" },
    { id: "evidence", label: "Evidence", count: evidence?.length },
    { id: "timeline", label: "Timeline", count: timeline?.length },
    { id: "provenance", label: "Sources", count: provenance?.length },
  ];

  return (
    <div className="bottom-panel">
      <div className="bottom-panel-tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`bottom-panel-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
            {tab.count !== undefined && tab.count > 0 && (
              <span className="bottom-panel-tab-count">{tab.count}</span>
            )}
          </button>
        ))}
        {pinnedCount !== undefined && pinnedCount > 0 && (
          <span className="bottom-panel-pinned">{pinnedCount} pinned</span>
        )}
      </div>

      <div className="bottom-panel-content">
        {activeTab === "selected" && (
          <div className="bottom-panel-selected">
            {!selectedNode && !selectedEdge ? (
              <p className="muted">No selection. Click a node in the graph to inspect. Right-click for investigative actions.</p>
            ) : selectedNode ? (
              <div className="selected-entity-details">
                <div className="selected-entity-header">
                  <h3 className="selected-entity-name">{selectedNode.name}</h3>
                  <Badge value={selectedNode.label} />
                  {selectedNode.is_criminal && <Badge value="CONFIRMED_CRIMINAL" />}
                  <span className="selected-entity-id">{selectedNode.provenance_key.slice(0, 16)}…</span>
                </div>
                <div className="selected-entity-meta">
                  <div className="selected-entity-field">
                    <span className="selected-entity-label">Confidence</span>
                    <span className="selected-entity-value">{Math.round((selectedNode.confidence || 0) * 100)}%</span>
                  </div>
                  {selectedNode.case_ids && selectedNode.case_ids.length > 0 && (
                    <div className="selected-entity-field">
                      <span className="selected-entity-label">Cases</span>
                      <span className="selected-entity-value">{selectedNode.case_ids.join(", ")}</span>
                    </div>
                  )}
                  {selectedNode.aliases && selectedNode.aliases.length > 0 && (
                    <div className="selected-entity-field">
                      <span className="selected-entity-label">Aliases</span>
                      <span className="selected-entity-value">{selectedNode.aliases.join(", ")}</span>
                    </div>
                  )}
                </div>
                <div className="selected-entity-actions">
                  <button
                    className="btn btn-secondary btn-small"
                    onClick={() => onOpenEvidence?.(selectedNode.provenance_key)}
                  >
                    View evidence
                  </button>
                  <Link
                    className="btn btn-tertiary btn-small"
                    to={`/entities/${encodeURIComponent(selectedNode.provenance_key)}`}
                  >
                    Open entity
                  </Link>
                </div>
                <div className="selected-entity-why">
                  <span className="why-label">Why surfaced:</span>
                  <span className="why-text">
                    Entity directly connected to investigation scope. Type: {selectedNode.label}.{" "}
                    {selectedNode.is_criminal ? "Confirmed criminal — source-derived." : "Not a confirmed criminal."}
                  </span>
                </div>
              </div>
            ) : selectedEdge ? (
              <div className="selected-edge-details">
                <div className="selected-entity-header">
                  <h3 className="selected-entity-name">
                    {selectedEdge.source.slice(0, 12)}… → {selectedEdge.target.slice(0, 12)}…
                  </h3>
                  <Badge value={selectedEdge.rel_type} />
                </div>
                <div className="selected-entity-meta">
                  <div className="selected-entity-field">
                    <span className="selected-entity-label">Relationship</span>
                    <span className="selected-entity-value">{selectedEdge.rel_type}</span>
                  </div>
                  <div className="selected-entity-field">
                    <span className="selected-entity-label">Confidence</span>
                    <span className="selected-entity-value">{Math.round((selectedEdge.confidence || 0) * 100)}%</span>
                  </div>
                  {selectedEdge.source_doc_ids && selectedEdge.source_doc_ids.length > 0 && (
                    <div className="selected-entity-field">
                      <span className="selected-entity-label">Sources</span>
                      <span className="selected-entity-value">{selectedEdge.source_doc_ids.length} docs</span>
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        )}

        {activeTab === "evidence" && (
          <div className="bottom-panel-evidence">
            {evidence && evidence.length > 0 ? (
              <EvidenceList items={evidence} empty="No evidence for this scope." />
            ) : (
              <p className="muted">No evidence in current view. Run an investigation or select an entity.</p>
            )}
          </div>
        )}

        {activeTab === "timeline" && (
          <div className="bottom-panel-timeline">
            {timeline && timeline.length > 0 ? (
              <div className="timeline-list">
                {timeline.slice(0, 50).map((entry: any, idx: number) => (
                  <div key={idx} className="timeline-entry">
                    <span className="timeline-when">{entry.timestamp || entry.ts || "—"}</span>
                    <span className="timeline-label">{entry.label || entry.summary || entry.event_type || "Event"}</span>
                    {entry.rel_type && <Badge value={String(entry.rel_type)} />}
                    {entry.description && <span className="timeline-desc">{String(entry.description)}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">No timeline for this scope. Timeline appears when investigating with temporal context.</p>
            )}
          </div>
        )}

        {activeTab === "provenance" && (
          <div className="bottom-panel-provenance">
            {provenance && provenance.length > 0 ? (
              <ul className="provenance-list">
                {provenance.slice(0, 100).map((p, idx) => (
                  <li key={`${p.kind}-${p.ref}-${idx}`} className="provenance-item">
                    <button className="evidence-link" onClick={() => onOpenSource?.(p)}>
                      {p.label}
                    </button>
                    <span className="provenance-kind">{p.kind}</span>
                    {p.detail && <span className="muted">{p.detail.slice(0, 80)}</span>}
                    {p.content_hash && <span className="hash">{p.content_hash.slice(0, 12)}…</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No sources in scope. Every finding links to its source documents here.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
