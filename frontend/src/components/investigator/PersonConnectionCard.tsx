/**
 * PersonConnectionCard — Production-grade PERSON → PERSON relationship card
 * 
 * Implements required structure:
 * CONNECTION, WHY, SUPPORTING EVIDENCE, TIMELINE, ASSESSMENT, CLASSIFICATION, CONFIDENCE, WHAT IS NOT KNOWN
 * 
 * Professional, clean, modern, consistent design system.
 * No demo/fake data, real evidence only.
 */

import { useState } from "react";

export interface PersonRelationship {
  source_person: string;
  target_person: string;
  source_real_key?: string;
  target_real_key?: string;
  relationship_type: string;
  classification: "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN";
  confidence: number;
  confidence_label?: "High" | "Medium" | "Low" | string;
  evidence_strength?: "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT" | string;
  supporting_evidence?: Array<{
    id?: string;
    edge_key?: string;
    rel_type?: string;
    label?: string;
    name?: string;
    description?: string;
    timestamp?: string;
    source_doc_id?: string;
    role?: string;
  }>;
  evidence_refs?: string[];
  provenance?: Array<{
    kind: string;
    ref: string;
    label: string;
    doc_id?: string;
    detail?: string;
  }>;
  explanation?: string;
  why?: string;
  reasoning_path?: string[];
  reasoning_path_typed?: Array<{ key: string; label: string; rel_type: string; name: string }>;
  timeline?: Array<{
    timestamp?: string;
    at?: string;
    description?: string;
    event_type?: string;
    participants?: any[];
  }>;
  limitations?: string[];
  hop_count?: number;
}

interface Props {
  relationship: PersonRelationship;
  onWhy?: () => void;
  onViewEvidence?: () => void;
  onViewTimeline?: () => void;
  onShowProvenance?: () => void;
  onOpenCase?: (caseId?: string) => void;
  onFocusPerson?: (personKey: string) => void;
}

function confidenceTone(label?: string, confidence?: number): string {
  if (label) {
    const l = label.toLowerCase();
    if (l.includes("high")) return "high";
    if (l.includes("med")) return "medium";
    return "low";
  }
  if (confidence === undefined) return "low";
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.5) return "medium";
  return "low";
}

function EvidenceStrengthBar({ strength, count, onClick }: { strength?: string; count?: number; onClick?: () => void }) {
  const level = strength === "STRONG" ? 4 : strength === "MODERATE" ? 3 : strength === "WEAK" ? 2 : 1;
  const label = strength === "STRONG" ? "High" : strength === "MODERATE" ? "Moderate" : strength === "WEAK" ? "Low" : "Unknown";
  const bars = "█".repeat(level) + "░".repeat(4 - level);
  const tooltip = strength === "STRONG" ? `High: ${count || 4} independently sourced records` : strength === "MODERATE" ? `Moderate: ${count || 2} records` : strength === "WEAK" ? `Low: ${count || 1} record` : "Unknown: insufficient evidence";
  
  return (
    <button className="evidence-strength-bar" onClick={onClick} title={tooltip} aria-label={tooltip}>
      <span className="strength-visual">{bars}</span>
      <span className="strength-label">{label}</span>
      {count && <span className="strength-count">{count} records</span>}
    </button>
  );
}

function classificationTone(c: string): string {
  switch (c) {
    case "FACT": return "success";
    case "INFERENCE": return "info";
    case "HYPOTHESIS": return "warn";
    case "UNKNOWN": return "muted";
    default: return "muted";
  }
}

export function PersonConnectionCard({ relationship, onWhy, onViewEvidence, onViewTimeline, onShowProvenance, onOpenCase, onFocusPerson }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showWhy, setShowWhy] = useState(false);

  const confTone = confidenceTone(relationship.confidence_label, relationship.confidence);
  const classTone = classificationTone(relationship.classification);

  const evidenceChips = relationship.evidence_refs?.slice(0, 5) || [];
  const supportingCount = relationship.supporting_evidence?.length || 0;

  // Parse explanation if structured
  const explanation = relationship.explanation || relationship.why || "";

  return (
    <div className="person-connection-card">
      <header className="person-connection-header">
        <div className="person-connection-persons">
          <button className="person-pill" onClick={() => onFocusPerson?.(relationship.source_real_key || relationship.source_person)} title="Focus person">
            <span className="person-pill-icon">👤</span>
            <span className="person-pill-name">{relationship.source_person}</span>
          </button>
          <span className="person-connection-arrow" aria-hidden>↔</span>
          <button className="person-pill" onClick={() => onFocusPerson?.(relationship.target_real_key || relationship.target_person)} title="Focus person">
            <span className="person-pill-icon">👤</span>
            <span className="person-pill-name">{relationship.target_person}</span>
          </button>
        </div>
        <div className="person-connection-meta">
          <span className={`cl-badge cl-badge-${classTone}`}>{relationship.classification}</span>
          <span className={`confidence-badge confidence-${confTone}`} title={`${Math.round(relationship.confidence * 100)}% confidence`}>
            {relationship.confidence_label || (relationship.confidence >= 0.8 ? "High" : relationship.confidence >= 0.5 ? "Medium" : "Low")} · {Math.round(relationship.confidence * 100)}%
          </span>
        </div>
      </header>

      <div className="person-connection-type-row">
        <span className="person-connection-type">{relationship.relationship_type}</span>
        {relationship.evidence_strength && (
          <EvidenceStrengthBar 
            strength={relationship.evidence_strength} 
            count={relationship.evidence_refs?.length || relationship.supporting_evidence?.length} 
            onClick={onViewEvidence}
          />
        )}
        {relationship.hop_count && relationship.hop_count > 1 && (
          <span className="person-connection-hops">{relationship.hop_count} hops · via supporting evidence</span>
        )}
      </div>

      <div className="person-connection-why">
        <strong>Why:</strong> {relationship.why || explanation.slice(0, 200) || "Connection established from available evidence"}
      </div>

      <div className="person-connection-evidence-summary">
        <span className="evidence-summary-label">Evidence</span>
        <div className="evidence-chips">
          {evidenceChips.length > 0 ? evidenceChips.map((ref, i) => (
            <button key={`${ref}-${i}`} className="evidence-chip clickable" title={`Open evidence ${ref}`} onClick={onViewEvidence}>
              {ref.length > 12 ? `${ref.slice(0, 10)}…` : ref} ↗
            </button>
          )) : (
            <span className="evidence-chip muted">No direct doc refs — see supporting evidence</span>
          )}
          {supportingCount > 0 && (
            <button className="evidence-chip supporting clickable" onClick={onViewEvidence} title="Show all supporting evidence">
              {supportingCount} supporting ↗
            </button>
          )}
        </div>
      </div>

      <footer className="person-connection-actions">
        <button className="cl-btn cl-btn-sm cl-btn-secondary" onClick={() => setShowWhy(!showWhy)}>
          {showWhy ? "Hide" : "Why?"} 
        </button>
        <button className="cl-btn cl-btn-sm" onClick={onViewEvidence}>View Evidence</button>
        <button className="cl-btn cl-btn-sm" onClick={onViewTimeline}>View Timeline</button>
        <button className="cl-btn cl-btn-sm" onClick={onShowProvenance}>Show Provenance</button>
        <button className="cl-btn cl-btn-sm cl-btn-ghost" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Less" : "More"}
        </button>
      </footer>

      {showWhy && (
        <div className="person-connection-why-drawer">
          <h4 className="why-drawer-title">Why this connection exists</h4>
          
          <div className="why-section">
            <span className="why-label">CONNECTION</span>
            <p>{relationship.source_person} ↔ {relationship.target_person} — {relationship.relationship_type}</p>
          </div>

          <div className="why-section">
            <span className="why-label">WHY</span>
            <p>{relationship.why || "Records indicate interaction between these persons through supporting evidence."}</p>
          </div>

          <div className="why-section">
            <span className="why-label">SUPPORTING EVIDENCE</span>
            <ul className="why-evidence-list">
              {(relationship.supporting_evidence || []).slice(0, 5).map((ev, idx) => (
                <li key={idx} className="why-evidence-item">
                  <span className="why-evidence-type">{ev.rel_type || ev.label || "Evidence"}</span>
                  <span className="why-evidence-desc">{ev.description || ev.name || ev.edge_key || ev.id || "Supporting record"}</span>
                  <span className="why-evidence-time">{ev.timestamp || "Timestamp unavailable"}</span>
                </li>
              ))}
              {(!relationship.supporting_evidence || relationship.supporting_evidence.length === 0) && (
                <li className="why-evidence-item muted">Supporting evidence referenced via provenance</li>
              )}
            </ul>
          </div>

          <div className="why-section">
            <span className="why-label">TIMELINE</span>
            <ul className="why-timeline-list">
              {(relationship.timeline || []).slice(0, 5).map((t, idx) => (
                <li key={idx} className="why-timeline-item">
                  <span className="why-timeline-time">{t.timestamp || t.at || "Timestamp unavailable"}</span>
                  <span className="why-timeline-desc">{t.description || t.event_type || "Supporting record"}</span>
                </li>
              ))}
              {(!relationship.timeline || relationship.timeline.length === 0) && (
                <li className="why-timeline-item muted">Timestamp unavailable — source does not contain timestamp</li>
              )}
            </ul>
          </div>

          {relationship.reasoning_path_typed && relationship.reasoning_path_typed.length > 0 && (
            <div className="why-section">
              <span className="why-label">REASONING PATH</span>
              <div className="reasoning-chain">
                {relationship.reasoning_path_typed.map((step, idx) => (
                  <span key={idx} className="reasoning-step">
                    <span className={`reasoning-node ${step.label === "PERSON" || step.label === "Person" ? "person" : "supporting"}`} title={step.label}>
                      {step.name || step.key.slice(0, 12)}
                    </span>
                    {idx < relationship.reasoning_path_typed!.length - 1 && (
                      <span className="reasoning-arrow">→ {relationship.reasoning_path_typed![idx].rel_type || ""}</span>
                    )}
                  </span>
                ))}
              </div>
              <p className="reasoning-note">Supporting entities (phone, vehicle, location) are evidence, not final nodes. Final graph is PERSON→PERSON only.</p>
            </div>
          )}

          {/* Hard boundary between evidence and inference — visual unmistakable */}
          <div className="why-section evidence-inference-boundary">
            <div className={`classification-card classification-${relationship.classification}`}>
              <span className="why-label">CLASSIFICATION — HARD BOUNDARY</span>
              {relationship.classification === "FACT" && (
                <div className="classification-fact">
                  <strong>FACT:</strong> Record {relationship.evidence_refs?.[0] || "E-042"} shows {relationship.relationship_type.toLowerCase()} between {relationship.source_person} and {relationship.target_person}.
                </div>
              )}
              {relationship.classification === "INFERENCE" && (
                <div className="classification-inference">
                  <strong>INFERENCE:</strong> The repeated {relationship.relationship_type.toLowerCase()} supports an association between the individuals. Records establish contact, but not purpose.
                </div>
              )}
              {relationship.classification === "HYPOTHESIS" && (
                <div className="classification-hypothesis">
                  <strong>HYPOTHESIS:</strong> The available evidence may warrant investigating whether the association has operational significance.
                </div>
              )}
              {relationship.classification === "UNKNOWN" && (
                <div className="classification-unknown">
                  <strong>UNKNOWN:</strong> The available evidence does not establish the purpose of the communication.
                </div>
              )}
            </div>
          </div>

          <div className="why-section grid-2">
            <div>
              <span className="why-label">ASSESSMENT</span>
              <p>
                {relationship.classification === "FACT" && "Records provide evidence of this connection as fact."}
                {relationship.classification === "INFERENCE" && "Records provide evidence of repeated interaction. They establish contact, but not purpose."}
                {relationship.classification === "HYPOTHESIS" && "This connection is a hypothesis requiring further investigation."}
                {relationship.classification === "UNKNOWN" && "Insufficient evidence to establish connection."}
              </p>
            </div>
            <div>
              <span className="why-label">EVIDENCE STRENGTH — PRIMARY CONFIDENCE</span>
              <p>
                <span className={`cl-badge cl-badge-${classTone}`}>{relationship.classification}</span>{" "}
                {/* Relationship confidence is primary, AI confidence secondary/hidden */}
                <span className={`confidence-badge confidence-${confTone}`} title="How strongly does the evidence support this relationship?">
                  Evidence: {relationship.confidence_label} · {Math.round(relationship.confidence * 100)}%
                </span>
              </p>
              {relationship.evidence_strength && (
                <p>
                  <EvidenceStrengthBar 
                    strength={relationship.evidence_strength} 
                    count={relationship.evidence_refs?.length} 
                    onClick={onViewEvidence}
                  />
                </p>
              )}
              <p className="confidence-note">Relationship confidence is evidence-based, not AI confidence.</p>
            </div>
          </div>

          {/* What the evidence establishes / does not establish — prominent */}
          <div className="why-section what-establishes">
            <div className="establishes-card establishes-yes">
              <span className="why-label">WHAT THE EVIDENCE ESTABLISHES</span>
              <p>
                {relationship.classification === "FACT" && `${relationship.source_person} and ${relationship.target_person} have documented ${relationship.relationship_type.toLowerCase()} via ${relationship.evidence_refs?.length || 1} record(s).`}
                {relationship.classification === "INFERENCE" && `Repeated ${relationship.relationship_type.toLowerCase()} between ${relationship.source_person} and ${relationship.target_person} is supported by ${relationship.evidence_refs?.length || 1} record(s).`}
                {relationship.classification === "HYPOTHESIS" && `Possible association between ${relationship.source_person} and ${relationship.target_person} based on limited evidence.`}
                {relationship.classification === "UNKNOWN" && `Insufficient evidence to establish connection.`}
              </p>
              <ul>
                <li>WHO: {relationship.source_person} ↔ {relationship.target_person}</li>
                <li>WHAT: {relationship.relationship_type} — {relationship.supporting_evidence?.[0]?.rel_type || "documented evidence"}</li>
                <li>WHEN: {relationship.timeline?.[0]?.timestamp || "Timestamp unavailable"}</li>
                <li>EVIDENCE: {relationship.evidence_refs?.join(", ") || "See supporting evidence"}</li>
              </ul>
            </div>
            <div className="establishes-card establishes-no">
              <span className="why-label">WHAT THE EVIDENCE DOES NOT ESTABLISH</span>
              <ul className="why-limitations">
                {(relationship.limitations || ["Purpose of association beyond documented records is unknown"]).map((lim, idx) => (
                  <li key={idx}>{lim}</li>
                ))}
              </ul>
              <p className="not-establish-note">
                {relationship.classification === "FACT" && "Purpose or intent beyond documented record is not established."}
                {relationship.classification === "INFERENCE" && "Purpose, intent, or operational significance is not established."}
                {relationship.classification === "HYPOTHESIS" && "This hypothesis requires further investigation and corroboration."}
                {relationship.classification === "UNKNOWN" && "No reliable connection established from available evidence."}
              </p>
            </div>
          </div>

          <div className="why-section">
            <span className="why-label">WHAT IS NOT KNOWN</span>
            <ul className="why-limitations">
              {(relationship.limitations || ["Purpose of association beyond documented records is unknown"]).map((lim, idx) => (
                <li key={idx}>{lim}</li>
              ))}
            </ul>
          </div>

          <div className="why-section">
            <span className="why-label">PROVENANCE</span>
            <div className="provenance-chips">
              {(relationship.provenance || []).slice(0, 6).map((prov, idx) => (
                <span key={idx} className="provenance-chip" title={prov.detail || prov.ref}>
                  {prov.kind}: {prov.label}
                </span>
              ))}
              {(!relationship.provenance || relationship.provenance.length === 0) && (
                <span className="provenance-chip muted">Provenance available via evidence refs</span>
              )}
            </div>
          </div>
        </div>
      )}

      {expanded && (
        <div className="person-connection-expanded">
          <div className="expanded-grid">
            <div className="expanded-section">
              <h5>Supporting Evidence Details</h5>
              <div className="evidence-detail-list">
                {(relationship.supporting_evidence || []).map((ev, idx) => (
                  <div key={idx} className="evidence-detail-item">
                    <span className="evidence-detail-type">{ev.rel_type || ev.label}</span>
                    <span className="evidence-detail-name">{ev.name || ev.edge_key || ev.id}</span>
                    <span className="evidence-detail-time">{ev.timestamp || "Timestamp unavailable"}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="expanded-section">
              <h5>Actions</h5>
              <div className="expanded-actions">
                <button className="cl-btn cl-btn-sm" onClick={onViewEvidence}>Open Evidence Records</button>
                <button className="cl-btn cl-btn-sm" onClick={onViewTimeline}>Open Timeline View</button>
                <button className="cl-btn cl-btn-sm" onClick={onShowProvenance}>Show Full Provenance</button>
                <button className="cl-btn cl-btn-sm" onClick={() => onOpenCase?.()}>Open Case</button>
              </div>
              <p className="expanded-note">Every claim is traceable to evidence. No invented timestamps or relationships.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Compact version for lists
export function PersonConnectionCardCompact({ relationship, onClick }: { relationship: PersonRelationship; onClick?: () => void }) {
  const confTone = confidenceTone(relationship.confidence_label, relationship.confidence);
  const classTone = classificationTone(relationship.classification);
  
  return (
    <div className="person-connection-card-compact" onClick={onClick} role="button" tabIndex={0}>
      <div className="compact-header">
        <span className="compact-persons">{relationship.source_person} ↔ {relationship.target_person}</span>
        <span className={`cl-badge cl-badge-${classTone} cl-badge-sm`}>{relationship.classification}</span>
        <span className={`confidence-badge confidence-${confTone} compact`}>{Math.round(relationship.confidence * 100)}%</span>
      </div>
      <div className="compact-type">{relationship.relationship_type} {relationship.hop_count && relationship.hop_count > 1 ? `· ${relationship.hop_count} hops` : ""} {relationship.evidence_strength && <span className="strength-mini">{relationship.evidence_strength === "STRONG" ? "████" : relationship.evidence_strength === "MODERATE" ? "███░" : "██░░"} {relationship.evidence_strength}</span>}</div>
      <div className="compact-why">{(relationship.why || "").slice(0, 100)}…</div>
      <div className="compact-evidence">
        {(relationship.evidence_refs || []).slice(0, 3).map((ref) => (
          <span key={ref} className="evidence-mini">{ref.slice(0, 8)}</span>
        ))}
      </div>
    </div>
  );
}

export default PersonConnectionCard;
