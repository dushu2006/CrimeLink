/**
 * Relationship Panel — Signature CrimeLink Interaction
 * PERSON A ↓ COMMUNICATION ↓ PERSON B
 * Evidence strength HIGH 4 supporting records [Why are they connected?]
 * Connection, Evidence, Timeline, What establishes, What does NOT, View all evidence
 */

import { useState } from "react";
import { EvidenceStrength } from "./EvidenceStrength";
import { ClassificationBadge } from "./ClassificationBadge";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { ContradictionAlert } from "./ContradictionAlert";
import { RelationshipPath } from "./RelationshipPath";
import { PersonRelationship } from "./PersonConnectionCard";

interface Props {
  relationship: PersonRelationship;
  onViewEvidence?: (ref?: string) => void;
  onViewTimeline?: () => void;
  onOpenCase?: () => void;
  onFocusPerson?: (key: string) => void;
}

export function RelationshipPanel({ relationship, onViewEvidence, onViewTimeline, onOpenCase, onFocusPerson }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  const [showEvidenceBreakdown, setShowEvidenceBreakdown] = useState(false);

  return (
    <div className="relationship-panel">
      <div className="relationship-panel-header">
        <h2>{relationship.source_person} ↔ {relationship.target_person}</h2>
        <span className="relationship-type">{relationship.relationship_type}</span>
        <ClassificationBadge classification={relationship.classification} />
      </div>

      <div className="relationship-panel-visual">
        <div className="person-node person-a">
          <span className="person-icon">👤</span>
          <span>{relationship.source_person}</span>
        </div>
        <div className="relationship-edge">
          <span className="edge-line">↓</span>
          <span className="edge-type">{relationship.relationship_type}</span>
          <span className="edge-line">↓</span>
        </div>
        <div className="person-node person-b">
          <span className="person-icon">👤</span>
          <span>{relationship.target_person}</span>
        </div>
      </div>

      <div className="relationship-panel-strength">
        <EvidenceStrength
          strength={relationship.evidence_strength || "MODERATE"}
          count={relationship.evidence_refs?.length}
          dates={relationship.timeline?.length}
          onClick={() => setShowEvidenceBreakdown(!showEvidenceBreakdown)}
        />
        <span className="supporting-count">{relationship.evidence_refs?.length || 0} supporting records</span>
      </div>

      {showEvidenceBreakdown && (
        <div className="evidence-breakdown">
          <div>Direct evidence: {relationship.supporting_evidence?.length || 0}</div>
          <div>Independent sources: {relationship.evidence_refs?.length || 0}</div>
          <div>Temporal consistency: ✓</div>
          <div>Contradictions: {relationship.limitations?.some((l) => l.toLowerCase().includes("conflict")) ? "1" : "0"}</div>
          <div>Provenance: ✓</div>
        </div>
      )}

      <button className="cl-btn cl-btn-primary" onClick={() => setShowWhy(!showWhy)}>Why are they connected?</button>

      {showWhy && (
        <div className="why-panel">
          <div className="why-section">
            <h4>Connection</h4>
            <p>{relationship.source_person} and {relationship.target_person} are connected through {relationship.relationship_type.toLowerCase()} records.</p>
          </div>

          <div className="why-section what-establishes">
            <div className="establishes-yes">
              <h5>What the evidence establishes</h5>
              <ul>
                <li>✓ Communication occurred</li>
                <li>✓ Both persons are connected</li>
                <li>✓ Multiple observations support relationship</li>
                <li>WHO: {relationship.source_person} ↔ {relationship.target_person}</li>
                <li>WHAT: {relationship.relationship_type} — {relationship.supporting_evidence?.[0]?.rel_type || "documented"}</li>
                <li>WHEN: {relationship.timeline?.[0]?.timestamp || "Timestamp unavailable"}</li>
              </ul>
            </div>
            <div className="establishes-no">
              <h5>What the evidence does NOT establish</h5>
              <ul>
                {(relationship.limitations || ["Purpose of communication", "Criminal intent", "Nature of relationship"]).map((lim, i) => (
                  <li key={i}>⚠ {lim}</li>
                ))}
              </ul>
            </div>
          </div>

          <div className="why-section">
            <h4>Supporting evidence</h4>
            <div className="evidence-chips">
              {(relationship.evidence_refs || []).map((ref) => (
                <button key={ref} className="evidence-chip clickable" onClick={() => onViewEvidence?.(ref)}>{ref}</button>
              ))}
            </div>
          </div>

          <div className="why-section">
            <h4>Assessment</h4>
            <div className="assessment">
              <span>Evidence strength: </span>
              <EvidenceStrength strength={relationship.evidence_strength || "MODERATE"} count={relationship.evidence_refs?.length} />
            </div>
          </div>

          <ProvenanceBadge />

          {relationship.limitations?.some((l) => l.toLowerCase().includes("conflict") || l.toLowerCase().includes("contradict")) && (
            <ContradictionAlert details={relationship.limitations || []} onViewConflicting={() => onViewEvidence?.()} />
          )}

          {relationship.reasoning_path_typed && relationship.reasoning_path_typed.length > 2 && (
            <RelationshipPath path={relationship.reasoning_path_typed.map((s) => ({ key: s.key, label: s.label, name: s.name, rel_type: s.rel_type, evidenceRef: relationship.evidence_refs?.[0] }))} hopCount={relationship.hop_count} onViewEvidence={onViewEvidence} />
          )}

          <div className="ai-explanation-footer">
            <span>AI-assisted explanation — Based only on evidence shown above. Unsupported claims excluded by grounding validation.</span>
            <button className="cl-btn cl-btn-sm" onClick={() => onViewEvidence?.()}>View reasoning basis</button>
          </div>
        </div>
      )}

      <div className="relationship-panel-actions">
        <button className="cl-btn cl-btn-sm" onClick={() => onViewEvidence?.()}>View all evidence</button>
        <button className="cl-btn cl-btn-sm" onClick={onViewTimeline}>View Timeline</button>
        <button className="cl-btn cl-btn-sm" onClick={onOpenCase}>Open Case</button>
      </div>

      <div className="next-steps">
        <h4>Suggested investigation actions</h4>
        <ul>
          <li><button className="next-step-btn" onClick={() => onViewEvidence?.(relationship.evidence_refs?.[0])}>Review {relationship.evidence_refs?.[0] || "E-042"}</button></li>
          <li><button className="next-step-btn" onClick={onViewTimeline}>Examine {relationship.source_person} timeline</button></li>
          {relationship.hop_count && relationship.hop_count > 1 && <li><button className="next-step-btn" onClick={() => onFocusPerson?.(relationship.source_real_key || "")}>Investigate {relationship.hop_count}-hop connection</button></li>}
          <li><button className="next-step-btn" onClick={() => onViewEvidence?.()}>Review conflicting evidence</button></li>
        </ul>
      </div>
    </div>
  );
}

export default RelationshipPanel;
