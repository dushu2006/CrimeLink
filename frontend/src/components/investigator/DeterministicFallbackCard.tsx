/**
 * AI Failure as Normal State — Professional UI
 * Relationship established from verified evidence, AI explanation unavailable
 * [View Evidence] [View Timeline]
 */

interface Props {
  sourcePerson: string;
  targetPerson: string;
  relationshipType: string;
  evidenceCount: number;
  onViewEvidence: () => void;
  onViewTimeline: () => void;
}

export function DeterministicFallbackCard({
  sourcePerson,
  targetPerson,
  relationshipType,
  evidenceCount,
  onViewEvidence,
  onViewTimeline,
}: Props) {
  return (
    <div className="deterministic-result-card">
      <div className="deterministic-header">
        <span className="deterministic-icon">✓</span>
        <span className="deterministic-title">Relationship established from verified evidence</span>
        <span className="deterministic-badge">Deterministic</span>
      </div>
      <div className="deterministic-body">
        <p>
          <strong>{sourcePerson} ↔ {targetPerson}</strong> via {relationshipType} — {evidenceCount} independently sourced record(s).
        </p>
        <p>AI explanation unavailable, but evidence-grounded result remains usable.</p>
      </div>
      <div className="deterministic-actions">
        <button className="cl-btn cl-btn-sm cl-btn-secondary" onClick={onViewEvidence}>
          View Evidence
        </button>
        <button className="cl-btn cl-btn-sm" onClick={onViewTimeline}>
          View Timeline
        </button>
      </div>
      <div className="deterministic-note">
        Evidence validation is model-independent. Investigation results remain usable even when AI explanation is temporarily unavailable.
      </div>
    </div>
  );
}

export default DeterministicFallbackCard;
