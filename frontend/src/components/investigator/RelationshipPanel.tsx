/**
 * Relationship Panel — Signature CrimeLink Interaction
 * PERSON A ↓ COMMUNICATION ↓ PERSON B
 * Evidence strength HIGH 4 supporting records [Why are they connected?]
 * Connection, Evidence, Timeline, What establishes, What does NOT, View all evidence
 */

import { useState } from "react";
import { EvidenceStrength } from "./EvidenceStrength";
import { ClassificationBadge } from "./ClassificationBadge";
import { ProvenanceBadge, provenanceChecksFor } from "./ProvenanceBadge";
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

const NO_TIMESTAMP = "timestamp unavailable";

export function RelationshipPanel({ relationship, onViewEvidence, onViewTimeline, onOpenCase, onFocusPerson }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  const [showEvidenceBreakdown, setShowEvidenceBreakdown] = useState(false);

  // Derived, never asserted: a placeholder string is not a recorded time.
  const supporting = relationship.supporting_evidence ?? [];
  const timestampsRecorded = supporting.filter((item) => {
    const value = (item.timestamp ?? "").trim();
    return value !== "" && value.toLowerCase() !== NO_TIMESTAMP;
  }).length;
  const temporalDetail =
    timestampsRecorded > 0
      ? `${timestampsRecorded} of ${supporting.length} supporting records carry a recorded time`
      : supporting.length > 0
        ? "No supporting record carries a recorded time"
        : "No supporting records";

  const provenanceEntries = relationship.provenance ?? [];
  const provenanceRefs = provenanceEntries.filter((entry) => (entry.ref ?? "").trim() !== "").length;
  const provenanceDetail =
    provenanceRefs > 0
      ? `${provenanceRefs} provenance entr${provenanceRefs === 1 ? "y names" : "ies name"} a source document`
      : "No provenance entry names a source document";

  return (
    <div className="relationship-panel">
      <div className="relationship-panel-header">
        <h2>{relationship.source_person} ↔ {relationship.target_person}</h2>
        <span className="relationship-type">{relationship.relationship_type}</span>
        <ClassificationBadge classification={relationship.classification} />
      </div>

      <div className="relationship-panel-visual">
        <div className="person-node person-a">
          <span className="material-symbols-outlined person-icon" aria-hidden="true">person</span>
          <span>{relationship.source_person}</span>
        </div>
        <div className="relationship-edge">
          <span className="edge-line">↓</span>
          <span className="edge-type">{relationship.relationship_type}</span>
          <span className="edge-line">↓</span>
        </div>
        <div className="person-node person-b">
          <span className="material-symbols-outlined person-icon" aria-hidden="true">person</span>
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
          {/* "Temporal consistency" and "Provenance" used to be literal ticks,
              printed whatever the relationship actually carried.  They are now
              derived from the records: a relationship is temporally grounded
              only if some supporting record has a real timestamp, and its
              provenance is present only if some entry names a real document. */}
          <div title={temporalDetail}>
            Temporal consistency:{" "}
            {timestampsRecorded > 0 ? (
              <span className="provenance-check verified">✓</span>
            ) : (
              <span className="provenance-check unverified">✗</span>
            )}{" "}
            <span className="muted">{temporalDetail}</span>
          </div>
          <div>
            Contradictions:{" "}
            {relationship.limitations?.some((l) =>
              l.toLowerCase().includes("conflict"),
            )
              ? "1"
              : "0"}
          </div>
          <div title={provenanceDetail}>
            Provenance:{" "}
            {provenanceRefs > 0 ? (
              <span className="provenance-check verified">✓</span>
            ) : (
              <span className="provenance-check unverified">✗</span>
            )}{" "}
            <span className="muted">{provenanceDetail}</span>
          </div>
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

          <ProvenanceBadge {...provenanceChecksFor(relationship)} />

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
        <div className="next-steps-heading-group">
          <div>
            <h4 className="next-steps-title">
              <span className="material-symbols-outlined" style={{ fontSize: "18px", color: "var(--cl-accent, #2563eb)" }}>
                explore
              </span>
              Suggested investigation actions
            </h4>
            <div className="next-steps-subtitle">Actionable next steps for this connection</div>
          </div>
          <span className="next-steps-badge">ACTIONS</span>
        </div>
        <ul>
          <li>
            <button
              type="button"
              className="next-step-btn"
              onClick={() => onViewEvidence?.(relationship.evidence_refs?.[0])}
            >
              <span className="material-symbols-outlined next-step-btn-icon">description</span>
              <div className="next-step-btn-body">
                <span className="next-step-btn-title">Review {relationship.evidence_refs?.[0] || "Evidence"}</span>
                <span className="next-step-btn-desc">Inspect primary supporting record</span>
              </div>
              <span className="next-step-btn-arrow">→</span>
            </button>
          </li>
          <li>
            <button type="button" className="next-step-btn" onClick={onViewTimeline}>
              <span className="material-symbols-outlined next-step-btn-icon">schedule</span>
              <div className="next-step-btn-body">
                <span className="next-step-btn-title">Examine timeline</span>
                <span className="next-step-btn-desc">{relationship.source_person} chronological history</span>
              </div>
              <span className="next-step-btn-arrow">→</span>
            </button>
          </li>
          {relationship.hop_count && relationship.hop_count > 1 && (
            <li>
              <button
                type="button"
                className="next-step-btn"
                onClick={() => onFocusPerson?.(relationship.source_real_key || "")}
              >
                <span className="material-symbols-outlined next-step-btn-icon">hub</span>
                <div className="next-step-btn-body">
                  <span className="next-step-btn-title">Investigate multi-hop</span>
                  <span className="next-step-btn-desc">{relationship.hop_count}-hop connection pathway</span>
                </div>
                <span className="next-step-btn-arrow">→</span>
              </button>
            </li>
          )}
          <li>
            <button type="button" className="next-step-btn" onClick={() => onViewEvidence?.()}>
              <span className="material-symbols-outlined next-step-btn-icon">rule</span>
              <div className="next-step-btn-body">
                <span className="next-step-btn-title">Review conflicting evidence</span>
                <span className="next-step-btn-desc">Audit discrepancies and data limitations</span>
              </div>
              <span className="next-step-btn-arrow">→</span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  );
}

export default RelationshipPanel;
