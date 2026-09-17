/**
 * Evidence classification for a person relationship.
 *
 * Both the case workspace and the relationships page used to stamp every
 * relationship `classification: "FACT"`, no matter what the underlying records
 * said — while reading confidence and evidence strength from the same edge.
 * A relationship supported by one weak record is not a verified fact, and
 * claiming so is exactly the "no hardcoded evidence classifications" rule the
 * audit brief sets out.
 *
 * Classification is derived here, in one place, from the two values the graph
 * service already computes.
 */

export type RelationshipClassification = "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN";

export interface ClassificationInput {
  /** The strength the graph service assigned from the supporting records. */
  strength?: string | null;
  /** The aggregated confidence for the relationship, 0..1. */
  confidence?: number | null;
  /** How many supporting records stand behind the relationship. */
  supportingCount?: number;
}

/**
 * Map real evidence signals to a classification.
 *
 * A relationship is a FACT only when the evidence behind it is strong and the
 * confidence agrees.  Weaker support degrades to INFERENCE, then HYPOTHESIS.
 * No supporting record at all is UNKNOWN, never a fact.
 */
export function classifyRelationship(input: ClassificationInput): RelationshipClassification {
  const confidence = typeof input.confidence === "number" ? input.confidence : 0;
  const supporting = typeof input.supportingCount === "number" ? input.supportingCount : 0;
  const strength = (input.strength ?? "").toUpperCase();

  if (supporting === 0 && confidence <= 0) return "UNKNOWN";

  if (strength === "STRONG" && confidence >= 0.85) return "FACT";
  if (strength === "STRONG" || strength === "MODERATE") {
    return confidence >= 0.6 ? "FACT" : "INFERENCE";
  }
  if (strength === "WEAK") return confidence >= 0.5 ? "INFERENCE" : "HYPOTHESIS";
  // Strength absent: fall back to confidence alone.
  if (confidence >= 0.85) return "FACT";
  if (confidence >= 0.6) return "INFERENCE";
  if (confidence > 0) return "HYPOTHESIS";
  return "UNKNOWN";
}
