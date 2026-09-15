/**
 * Evidence Strength — Confidence semantics
 * High ████████░░, Medium ██████░░░░, Low ███░░░░░░░, Insufficient ░░░░░░░░░░
 * With reason: High — supported by 4 independent records across 3 dates.
 */

interface Props {
  strength: "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT" | string;
  count?: number;
  dates?: number;
  label?: string;
  onClick?: () => void;
}

export function EvidenceStrength({ strength, count, dates, label, onClick }: Props) {
  const normalized = strength?.toUpperCase();
  const level = normalized === "STRONG" ? 4 : normalized === "MODERATE" ? 3 : normalized === "WEAK" ? 2 : normalized === "INSUFFICIENT" ? 0 : 1;
  const bars = "█".repeat(level) + "░".repeat(4 - level);
  const displayLabel = label || (normalized === "STRONG" ? "High" : normalized === "MODERATE" ? "Medium" : normalized === "WEAK" ? "Low" : "Insufficient");
  const reason = normalized === "STRONG" ? `High — supported by ${count || 4} independent records${dates ? ` across ${dates} dates` : ""}.` : normalized === "MODERATE" ? `Medium — ${count || 2} records.` : normalized === "WEAK" ? `Low — ${count || 1} record.` : "Insufficient — evidence does not establish connection.";

  return (
    <button className="evidence-strength" onClick={onClick} title={reason}>
      <span className="evidence-strength-bars">{bars}</span>
      <span className="evidence-strength-label">{displayLabel}</span>
      <span className="evidence-strength-reason">{reason}</span>
    </button>
  );
}

export default EvidenceStrength;
