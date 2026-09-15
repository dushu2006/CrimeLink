/**
 * Investigation Path — Multi-hop visualization
 * Makes multi-hop connections visually understandable to investigators
 * PERSON A → PERSON X → PERSON Y → PERSON B
 * 3-hop connection, Path evidence A→X E-021, etc.
 */

interface PathStep {
  key: string;
  label: string;
  name: string;
  rel_type?: string;
  evidenceRef?: string;
}

interface Props {
  path: PathStep[];
  hopCount?: number;
  onViewEvidence?: (ref: string) => void;
}

export function RelationshipPath({ path, hopCount, onViewEvidence }: Props) {
  if (!path || path.length === 0) return null;

  return (
    <div className="relationship-path">
      <div className="relationship-path-header">
        <span className="relationship-path-title">Investigation Path</span>
        {hopCount && <span className="relationship-path-hops">{hopCount}-hop connection</span>}
      </div>

      <div className="relationship-path-visual">
        {path.map((step, idx) => (
          <div key={idx} className="path-step">
            <div className={`path-node ${step.label.toUpperCase() === "PERSON" ? "person" : "supporting"}`}>
              <span className="path-node-icon">{step.label.toUpperCase() === "PERSON" ? "👤" : "📄"}</span>
              <span className="path-node-name">{step.name || step.key.slice(0, 12)}</span>
              <span className="path-node-label">{step.label}</span>
            </div>
            {idx < path.length - 1 && (
              <div className="path-edge">
                <span className="path-edge-line">│</span>
                <span className="path-edge-type">{path[idx].rel_type || step.rel_type || "connected"}</span>
                <span className="path-edge-arrow">▼</span>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="relationship-path-evidence">
        <div className="path-evidence-title">Path evidence</div>
        {path.slice(0, -1).map((step, idx) => (
          <div key={idx} className="path-evidence-item">
            <span className="path-evidence-pair">{step.name || step.key.slice(0, 8)} → {path[idx + 1]?.name || path[idx + 1]?.key.slice(0, 8)}</span>
            {step.evidenceRef && (
              <button className="evidence-chip clickable" onClick={() => onViewEvidence?.(step.evidenceRef!)}>{step.evidenceRef}</button>
            )}
          </div>
        ))}
      </div>

      <div className="path-note">Supporting entities (phone, vehicle, location) are evidence, not final nodes. Final graph is PERSON→PERSON only.</div>
    </div>
  );
}

export default RelationshipPath;
