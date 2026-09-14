import type { SuspiciousPattern } from "../api/client";

/**
 * Stable identity for a rendered pattern.
 *
 * The current pattern wire model has no dedicated pattern UUID, so identity is
 * derived from the stable domain fields that produced a detector result:
 * detector kind, canonical entity keys, case IDs, time range, and provenance
 * references. Human-readable titles are included only as an additional stable
 * discriminator, never as the sole key.
 */
export function patternIdentity(pattern: SuspiciousPattern | null | undefined): string | null {
  if (!pattern) return null;

  const uniqueSorted = (values: Iterable<unknown>): string[] =>
    [...new Set(Array.from(values, (value) => String(value)))]
      .filter(Boolean)
      .sort();

  const entityKeys = uniqueSorted(pattern.entity_keys ?? []);
  const caseIds = uniqueSorted(pattern.cases ?? []);
  const provenanceRefs = uniqueSorted(
    (pattern.provenance ?? []).map((pointer) => `${pointer.kind}:${pointer.ref}`),
  );
  const evidenceRefs = uniqueSorted(
    (pattern.evidence ?? []).flatMap((item) =>
      (item.provenance ?? []).map((pointer) => `${pointer.kind}:${pointer.ref}`),
    ),
  );
  const timeRange = Object.entries(pattern.time_range ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => `${name}=${value ?? ""}`)
    .join(",");

  return [
    pattern.kind,
    `entities:${entityKeys.join(",")}`,
    `cases:${caseIds.join(",")}`,
    `time:${timeRange}`,
    `provenance:${provenanceRefs.join(",")}`,
    `evidence:${evidenceRefs.join(",")}`,
    `type:${pattern.pattern_type ?? ""}`,
    `title:${pattern.title}`,
  ].join("|");
}
