/**
 * Null-safe display helpers.
 *
 * The backend is honest about the schema: a field that is nullable in the
 * database arrives as `null` — never as an empty string.  In particular,
 * dataset-level documents have `case_id: null` (the pipeline never assigns
 * them to the synthetic container case), so any table that prints a short
 * id must treat `null` as a valid value instead of calling `.slice()` on it.
 *
 * Production failure this module pins down:
 *   TypeError: Cannot read properties of null (reading 'slice')
 *   Admin.tsx → documents tab → `d.case_id.slice(0, 8)`
 *
 * Pages should render nullable identifiers *through* these helpers and keep
 * the raw value (null) for joins, filters and keys.
 */

/** The em-dash used to display "no value" in tables. */
export const DASH = "—";

/**
 * A short, display-only form of an identifier.
 *
 * - `null`/`undefined`/empty → the dash (the value genuinely does not exist);
 * - longer than `length` chars → first `length` chars + ellipsis;
 * - otherwise unchanged.
 *
 * Never throws on any input shape the API can send.
 */
export function shortId(value: string | null | undefined, length: number): string {
  if (value === null || value === undefined) return DASH;
  const text = String(value);
  if (!text) return DASH;
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

/**
 * A number that may arrive as `null` rendered with fixed decimals — the
 * graph rows always carry a confidence, but a hand-built or legacy row must
 * not take the table down.
 */
export function fixed(value: number | null | undefined, digits: number): string {
  if (typeof value !== "number" || Number.isNaN(value)) return DASH;
  return value.toFixed(digits);
}
