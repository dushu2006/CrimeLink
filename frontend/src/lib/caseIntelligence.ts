/**
 * CASE INTELLIGENCE header — derivation of the metric cells.
 *
 * The Case Evidence Assistant header shows five figures for the open case:
 * verified records, evidence types, entities, case-scoped relationships and
 * the first/latest recorded dates.  Every one of them comes from a single
 * authoritative source — ``GET /ai/cases/{id}/context`` — which the backend
 * computes from the same case-scoped nodes, relationships and documents the
 * assistant itself retrieves.  Nothing here reads an AI answer, and nothing
 * here stores case data.
 *
 * Two rules keep the header honest:
 *
 *  1. A metric the payload states as ``0`` is displayed as ``0`` (a genuinely
 *     empty case says so).  A metric the payload does not state is displayed
 *     as unknown (``—``) — never coerced to ``0``.
 *  2. When the authoritative endpoint cannot be reached, the header must not
 *     paint zeros and ``N/A`` as if they were facts.  The degraded context
 *     keeps the case identity for the panel and marks the metrics unavailable.
 */

export interface CaseContextStatsPayload {
  documents_indexed?: number | null;
  document_count?: number | null;
  evidence_count?: number | null;
  evidence_types_count?: number | null;
  evidence_types?: string[] | null;
  entities_extracted?: number | null;
  entity_count?: number | null;
  entity_counts_by_type?: Record<string, number> | null;
  relationships_mapped?: number | null;
  relationship_count?: number | null;
  coverage_percent?: number | null;
  confidence_score?: number | null;
}

export interface CaseContextTimelinePayload {
  first_recorded?: string | null;
  latest_recorded?: string | null;
  event_count?: number | null;
}

/** The payload of ``GET /ai/cases/{id}/context`` (``CaseAIContext.as_summary_dict``). */
export interface CaseContextSummary {
  case_id: string;
  case_number: string;
  title: string;
  stats?: CaseContextStatsPayload | null;
  timeline?: CaseContextTimelinePayload | null;
  timeline_summary?: CaseContextTimelinePayload | null;
  suggested_questions: string[];
  canonical_entities_count?: number | null;
  evidence_count?: number | null;
  /**
   * ``false`` only on the degraded context built when the authoritative
   * endpoint failed.  Absent (or ``true``) on every authoritative payload.
   */
  metrics_available?: boolean;
}

export interface CaseIntelligenceMetrics {
  /** False when the authoritative case context could not be loaded. */
  available: boolean;
  documents: number | null;
  evidenceTypes: number | null;
  entities: number | null;
  relationships: number | null;
  firstRecorded: string;
  latestRecorded: string;
  /** Exactly what the header cells render. */
  evidenceLabel: string;
  entitiesLabel: string;
  relationshipsLabel: string;
}

/** Rendered for a metric the authoritative payload did not state. */
export const UNKNOWN_METRIC = "—";
export const NO_DATE = "N/A";

/** Generic prompts shown while the case-tailored suggestions are unavailable. */
export const GENERIC_SUGGESTED_QUESTIONS: readonly string[] = [
  "Who are the key people in this case?",
  "What evidence connects the primary suspect to the incident?",
  "Show the financial relationships in this case.",
  "What happened before and after the alleged incident?",
];

function toCount(...candidates: Array<unknown>): number | null {
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined || candidate === "") continue;
    const value = typeof candidate === "number" ? candidate : Number(candidate);
    if (Number.isFinite(value)) return Math.max(0, Math.trunc(value));
  }
  return null;
}

function toDate(...candidates: Array<unknown>): string {
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const text = candidate.trim();
    if (text) return text;
  }
  return NO_DATE;
}

function show(value: number | null): string {
  return value === null ? UNKNOWN_METRIC : String(value);
}

const ENTITY_UNITS: Record<string, [singular: string, plural: string]> = {
  person: ["person", "people"],
  persons: ["person", "people"],
  people: ["person", "people"],
  account: ["account", "accounts"],
  accounts: ["account", "accounts"],
  bankaccount: ["account", "accounts"],
  phone: ["phone", "phones"],
  phones: ["phone", "phones"],
  phonenumber: ["phone", "phones"],
  vehicle: ["vehicle", "vehicles"],
  vehicles: ["vehicle", "vehicles"],
  organization: ["organization", "organizations"],
  organizations: ["organization", "organizations"],
  company: ["company", "companies"],
  companies: ["company", "companies"],
  location: ["location", "locations"],
  locations: ["location", "locations"],
  address: ["address", "addresses"],
  addresses: ["address", "addresses"],
  event: ["event", "events"],
  events: ["event", "events"],
};

function unitFor(rawType: string, count: number): string {
  const key = rawType.trim().toLowerCase().replace(/[\s_-]+/g, "");
  const pair = ENTITY_UNITS[key];
  if (pair) return count === 1 ? pair[0] : pair[1];
  const singular = key.endsWith("s") ? key.slice(0, -1) : key;
  return count === 1 ? singular : `${singular}s`;
}

/**
 * "10 people · 3 phones · 2 accounts" from the authoritative per-type counts,
 * or "N entities" when no breakdown is available.
 */
export function formatEntityCounts(
  counts: Record<string, number> | null | undefined,
  total: number | null,
): string {
  const parts: string[] = [];
  for (const [rawType, raw] of Object.entries(counts ?? {})) {
    const count = toCount(raw);
    if (count === null || count <= 0) continue;
    parts.push(`${count} ${unitFor(rawType, count)}`);
  }
  if (parts.length > 0) return parts.join(" · ");
  if (total === null) return `${UNKNOWN_METRIC} entities`;
  return total === 1 ? "1 entity" : `${total} entities`;
}

/**
 * The header cells for one case context.  Pure: the same payload always
 * yields the same cells, and no field is ever invented.
 */
export function deriveCaseIntelligenceMetrics(
  context: CaseContextSummary | null | undefined,
): CaseIntelligenceMetrics {
  const stats = context?.stats ?? null;
  const available = Boolean(context) && context?.metrics_available !== false && stats !== null;

  if (!available) {
    return {
      available: false,
      documents: null,
      evidenceTypes: null,
      entities: null,
      relationships: null,
      firstRecorded: NO_DATE,
      latestRecorded: NO_DATE,
      evidenceLabel: "",
      entitiesLabel: "",
      relationshipsLabel: "",
    };
  }

  const documents = toCount(
    stats?.documents_indexed,
    stats?.document_count,
    stats?.evidence_count,
    context?.evidence_count,
  );
  const evidenceTypes = toCount(
    stats?.evidence_types_count,
    Array.isArray(stats?.evidence_types) ? stats?.evidence_types.length : null,
  );
  const entities = toCount(
    stats?.entities_extracted,
    stats?.entity_count,
    context?.canonical_entities_count,
  );
  const relationships = toCount(stats?.relationships_mapped, stats?.relationship_count);
  const firstRecorded = toDate(
    context?.timeline?.first_recorded,
    context?.timeline_summary?.first_recorded,
  );
  const latestRecorded = toDate(
    context?.timeline?.latest_recorded,
    context?.timeline_summary?.latest_recorded,
  );

  return {
    available: true,
    documents,
    evidenceTypes,
    entities,
    relationships,
    firstRecorded,
    latestRecorded,
    evidenceLabel: `${show(documents)} verified records · ${show(evidenceTypes)} evidence types`,
    entitiesLabel: formatEntityCounts(stats?.entity_counts_by_type, entities),
    relationshipsLabel: `${show(relationships)} case-scoped relationships`,
  };
}

/**
 * The context used when ``/ai/cases/{id}/context`` is unreachable.  It keeps
 * the panel usable (case identity, generic prompts) but states no metric:
 * the header must never present zeros and N/A that nobody computed.
 */
export function degradedCaseContext(
  caseId: string,
  caseData: { case_number?: string | null; title?: string | null } | null | undefined,
): CaseContextSummary {
  return {
    case_id: caseId,
    case_number: caseData?.case_number || caseId,
    title: caseData?.title || "Active Investigation",
    stats: null,
    timeline: null,
    suggested_questions: [...GENERIC_SUGGESTED_QUESTIONS],
    metrics_available: false,
  };
}
