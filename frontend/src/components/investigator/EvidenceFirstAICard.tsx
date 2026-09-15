/**
 * Evidence-First AI Card — Priority 2 in UX redesign
 * 
 * Don't show: "Person A is connected to Person B."
 * Show: Possible connection identified + reasoning path + supporting evidence + classification + actions
 */

import { Link } from "react-router-dom";
import { Badge } from "../Status";

export interface AIFinding {
  id: string;
  title: string;
  summary: string;
  confidence: number;
  evidenceLevel: "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN";
  reasoningPath?: Array<{ from: string; to: string; via?: string; relType?: string }>;
  supportingEvidence?: Array<{ id: string; type: string; caseId?: string }>;
  suggestedActions?: Array<{ label: string; action: string }>;
  entities?: string[];
  relationships?: string[];
  evidenceRefs?: string[];
  raw?: any;
}

interface EvidenceFirstAICardProps {
  finding: AIFinding;
  onWhy?: () => void;
  onShowEvidence?: () => void;
  onShowGraphPath?: () => void;
  onOpenEvidence?: (id: string) => void;
}

export function EvidenceFirstAICard({ finding, onWhy, onShowEvidence, onShowGraphPath, onOpenEvidence }: EvidenceFirstAICardProps) {
  const levelTone = {
    FACT: "success",
    INFERENCE: "info",
    HYPOTHESIS: "warn",
    UNKNOWN: "muted",
  }[finding.evidenceLevel] || "muted";

  const confidenceLevel = finding.confidence > 0.8 ? "High" : finding.confidence > 0.5 ? "Moderate" : finding.confidence > 0.3 ? "Low" : "Very Low";
  const confidenceTone = finding.confidence > 0.8 ? "high" : finding.confidence > 0.5 ? "medium" : "low";

  return (
    <div className="ai-finding-card evidence-first">
      <header className="ai-finding-header">
        <div className="ai-finding-title-row">
          <span className={`badge badge-${levelTone}`}>{finding.evidenceLevel}</span>
          <h3 className="ai-finding-title">{finding.title}</h3>
        </div>
        <div className="ai-finding-meta">
          <span className={`confidence-badge confidence-${confidenceTone}`} title={`Confidence: ${Math.round(finding.confidence * 100)}%`}>
            {confidenceLevel} · {Math.round(finding.confidence * 100)}%
          </span>
        </div>
      </header>

      <div className="ai-finding-summary">
        <p>{finding.summary}</p>
      </div>

      {finding.reasoningPath && finding.reasoningPath.length > 0 && (
        <div className="ai-finding-reasoning">
          <h4 className="ai-finding-section-title">Reasoning</h4>
          <div className="reasoning-path">
            {finding.reasoningPath.map((step, idx) => (
              <div key={idx} className="reasoning-step">
                <div className="reasoning-node">{step.from}</div>
                {step.relType && (
                  <div className="reasoning-edge">
                    <span className="reasoning-arrow">↓</span>
                    <span className="reasoning-rel">{step.relType}</span>
                    {step.via && <span className="reasoning-via">{step.via}</span>}
                  </div>
                )}
                <div className="reasoning-node">{step.to}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {finding.supportingEvidence && finding.supportingEvidence.length > 0 && (
        <div className="ai-finding-evidence">
          <h4 className="ai-finding-section-title">Supporting evidence</h4>
          <ul className="evidence-list-compact">
            {finding.supportingEvidence.map((ev) => (
              <li key={ev.id} className="evidence-item-compact">
                <button className="evidence-link-btn" onClick={() => onOpenEvidence?.(ev.id)}>
                  {ev.id}
                </button>
                <span className="evidence-type-badge">{ev.type}</span>
                {ev.caseId && <span className="evidence-case">{ev.caseId}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {finding.evidenceRefs && finding.evidenceRefs.length > 0 && !finding.supportingEvidence && (
        <div className="ai-finding-evidence">
          <h4 className="ai-finding-section-title">Evidence refs</h4>
          <div className="evidence-refs">
            {finding.evidenceRefs.slice(0, 5).map((ref) => (
              <span key={ref} className="evidence-ref-chip">{ref.slice(0, 12)}…</span>
            ))}
          </div>
        </div>
      )}

      <div className="ai-finding-classification">
        <h4 className="ai-finding-section-title">Classification</h4>
        <div className="classification-grid">
          <div className="classification-item">
            <span className="classification-label">Evidence Level</span>
            <Badge value={finding.evidenceLevel} />
          </div>
          <div className="classification-item">
            <span className="classification-label">Confidence</span>
            <span className={`confidence-badge confidence-${confidenceTone}`}>{confidenceLevel}</span>
          </div>
          <div className="classification-item">
            <span className="classification-label">Review</span>
            <span className="badge badge-warn">Requires verification</span>
          </div>
        </div>
      </div>

      <footer className="ai-finding-actions">
        <button className="btn btn-tertiary btn-small" onClick={onWhy}>
          Why this conclusion?
        </button>
        <button className="btn btn-tertiary btn-small" onClick={onShowEvidence}>
          Show evidence
        </button>
        <button className="btn btn-tertiary btn-small" onClick={onShowGraphPath}>
          Show graph path
        </button>
        {finding.supportingEvidence?.[0] && (
          <button className="btn btn-secondary btn-small" onClick={() => finding.supportingEvidence && onOpenEvidence?.(finding.supportingEvidence[0].id)}>
            Open evidence
          </button>
        )}
      </footer>

      <div className="ai-finding-disclaimer">
        <span className="muted">AI-assisted finding — requires investigator verification</span>
      </div>
    </div>
  );
}

// Compact version for list
export function EvidenceFirstAICardCompact({ finding, onClick }: { finding: AIFinding; onClick?: () => void }) {
  const levelTone = {
    FACT: "success",
    INFERENCE: "info",
    HYPOTHESIS: "warn",
    UNKNOWN: "muted",
  }[finding.evidenceLevel] || "muted";

  return (
    <div className="ai-finding-card-compact" onClick={onClick} role="button" tabIndex={0}>
      <div className="ai-finding-compact-header">
        <Badge value={finding.evidenceLevel} />
        <span className="ai-finding-compact-title">{finding.title}</span>
        <span className="confidence-mini">{Math.round(finding.confidence * 100)}%</span>
      </div>
      <p className="ai-finding-compact-summary">{finding.summary.slice(0, 120)}…</p>
      {finding.supportingEvidence && (
        <div className="ai-finding-compact-evidence">
          {finding.supportingEvidence.slice(0, 3).map((ev) => (
            <span key={ev.id} className="evidence-mini">{ev.id.slice(0, 8)}</span>
          ))}
        </div>
      )}
    </div>
  );
}
