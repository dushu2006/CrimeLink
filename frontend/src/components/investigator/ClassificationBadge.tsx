/**
 * Classification Badge — Global design language
 * FACT, INFERENCE, HYPOTHESIS, UNKNOWN with ⓘ tooltip
 */

interface Props {
  classification: "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN" | string;
  showTooltip?: boolean;
}

const definitions: Record<string, string> = {
  FACT: "What records directly establish.",
  INFERENCE: "What follows reasonably from the evidence.",
  HYPOTHESIS: "What may warrant further investigation.",
  UNKNOWN: "What the current evidence cannot establish.",
};

export function ClassificationBadge({ classification, showTooltip = true }: Props) {
  const def = definitions[classification] || definitions.UNKNOWN;
  const tone = classification === "FACT" ? "success" : classification === "INFERENCE" ? "info" : classification === "HYPOTHESIS" ? "warn" : "muted";

  return (
    <span className={`classification-badge classification-${classification} cl-badge cl-badge-${tone}`} title={def}>
      {classification}
      {showTooltip && <span className="classification-info"> ⓘ</span>}
    </span>
  );
}

export default ClassificationBadge;
