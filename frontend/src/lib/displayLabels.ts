/**
 * Canonical centralized display-label and visual styling resolver for CrimeLink graph entities.
 *
 * Requirements:
 * 1. ONLY source-derived confirmed criminals receive the STAR shape.
 *    EVERY other entity MUST be a CIRCLE ("ellipse").
 * 2. High-degree, high-betweenness, or high-PageRank entities remain CIRCLES unless
 *    they have source-derived confirmed criminal status.
 * 3. Centralized label resolution: human-readable operational values for PERSON, PHONE,
 *    BANK_ACCOUNT, VEHICLE, LOCATION, ORGANIZATION, CASE, FIR, DOCUMENT, EVENT.
 * 4. Internal IDs (P001, PH001, BA001) are preserved internally for joins and provenance
 *    but never displayed as primary visual graph labels when operational values exist.
 */

const CONFIRMED_CRIMINAL_STATUSES = new Set([
  "confirmed",
  "convicted",
  "accused",
  "chargesheeted",
  "criminal",
]);

/**
 * Type-safe check for confirmed criminal status.
 *
 * Rules:
 * - Only an entity whose canonical type is PERSON can be a criminal.
 * - Must be explicitly marked `is_criminal === true` or have an authoritative
 *   legal/criminal status matching confirmed/accused/convicted/chargesheeted.
 * - Negative or unconfirmed statuses ("not_confirmed", "suspect", "witness", "victim",
 *   "person_of_interest", "unknown", "none") are NOT confirmed criminals.
 * - Non-PERSON entities (phone, bank account, vehicle, location, org, case, etc.)
 *   can NEVER be a criminal.
 */
export function isConfirmedCriminal(entity: any): boolean {
  if (!entity) return false;

  // Check canonical entity type
  const rawType = String(
    entity.entity_type ||
    entity.label ||
    entity.kind ||
    entity.properties?.entity_type ||
    entity.properties?.label ||
    ""
  ).toUpperCase();

  if (rawType !== "PERSON") {
    return false;
  }

  // If backend already computed is_criminal boolean
  if (entity.is_criminal === true || entity.properties?.is_criminal === true) {
    return true;
  }

  const status = String(
    entity.criminal_status ||
    entity.legal_status ||
    entity.properties?.criminal_status ||
    entity.properties?.legal_status ||
    ""
  )
    .trim()
    .toLowerCase();

  return CONFIRMED_CRIMINAL_STATUSES.has(status);
}

/**
 * Universal node shape rule:
 *   ★ STAR = source-derived confirmed criminal PERSON only
 *   ○ CIRCLE ("ellipse") = every other entity in the system
 */
export function nodeShapeRule(entityOrCriminal: any): "star" | "ellipse" {
  if (typeof entityOrCriminal === "boolean") {
    return entityOrCriminal ? "star" : "ellipse";
  }
  return isConfirmedCriminal(entityOrCriminal) ? "star" : "ellipse";
}

/**
 * Centralized display-label resolver for graph nodes and entity mentions.
 */
export function getDisplayLabel(entity: any): string {
  if (!entity) return "";

  const rawType = String(
    entity.entity_type || entity.label || entity.kind || ""
  ).toUpperCase();

  const props = entity.properties || entity;

  if (rawType.includes("PERSON")) {
    const candidates = [
      props.canonical_name,
      props.display_name,
      props.full_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) {
        const trimmed = c.trim();
        // Avoid raw P001 placeholder if other options exist
        if (!/^[Pp]\d+$/.test(trimmed)) {
          return trimmed;
        }
      }
    }
    const aliases = props.aliases;
    if (Array.isArray(aliases) && aliases.length > 0 && typeof aliases[0] === "string" && aliases[0].trim()) {
      return aliases[0].trim();
    }
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) {
        return c.trim();
      }
    }
  } else if (rawType.includes("PHONE")) {
    const candidates = [
      props.phone_number,
      props.phone,
      props.msisdn,
      props.number,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("ACCOUNT") || rawType.includes("BANK")) {
    const candidates = [
      props.account_number,
      props.account_no,
      props.account_id,
      props.number,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("VEHICLE")) {
    const candidates = [
      props.registration_number,
      props.registration_no,
      props.vehicle_number,
      props.plate,
      props.reg_no,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("LOCATION") || rawType.includes("ADDRESS")) {
    const candidates = [
      props.location_name,
      props.address,
      props.city,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("ORGANIZATION") || rawType.includes("COMPANY")) {
    const candidates = [
      props.org_name,
      props.organization_name,
      props.company_name,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("CASE")) {
    const candidates = [
      props.case_number,
      props.case_no,
      props.case_id,
      props.title,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("FIR")) {
    const candidates = [
      props.fir_number,
      props.fir_no,
      props.fir_id,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("DOCUMENT")) {
    const candidates = [
      props.original_filename,
      props.filename,
      props.title,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  } else if (rawType.includes("EVENT")) {
    const candidates = [
      props.event_summary,
      props.summary,
      props.event_label,
      props.title,
      props.description,
      props.event_type,
      props.display_name,
      props.name,
      entity.name,
    ];
    for (const c of candidates) {
      if (c && typeof c === "string" && c.trim()) return c.trim();
    }
  }

  // General fallback
  const fallback =
    props.display_name ||
    props.name ||
    entity.name ||
    props.canonical_id ||
    props.id ||
    entity.id ||
    entity.provenance_key ||
    "";

  return typeof fallback === "string" ? fallback.trim() : String(fallback);
}
