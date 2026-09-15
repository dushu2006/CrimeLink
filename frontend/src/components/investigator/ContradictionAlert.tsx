/**
 * Contradiction Alert — Visually obvious, trust feature
 * ⚠ Conflicting evidence, Two records place Person A at different locations
 */

interface Props {
  details: string[];
  onViewConflicting?: () => void;
}

export function ContradictionAlert({ details, onViewConflicting }: Props) {
  if (!details || details.length === 0) return null;

  return (
    <div className="contradiction-alert">
      <div className="contradiction-header">
        <span className="contradiction-icon">⚠</span>
        <span className="contradiction-title">Conflicting evidence</span>
      </div>
      <div className="contradiction-body">
        <p>Two records place persons at different locations during the same period. This reduces confidence in the location-based connection.</p>
        <ul>
          {details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      </div>
      <button className="cl-btn cl-btn-sm" onClick={onViewConflicting}>View conflicting records</button>
    </div>
  );
}

export default ContradictionAlert;
