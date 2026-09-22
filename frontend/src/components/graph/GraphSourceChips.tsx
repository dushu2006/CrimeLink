/**
 * Graph ↔ source traceability — THE provenance link between a graph element
 * and the evidence that created it.
 *
 * A graph node/edge is a DERIVED view of case records.  This component
 * renders the document ids the backend attached to that element
 * (``source_doc_ids``) as clickable references; each one opens the COMPLETE
 * original document in the existing CrimeLink source viewer via
 * {@link DocumentFileLink} — the graph panel never embeds content and never
 * guesses a URL.
 *
 * Provenance rules for every consumer of this component:
 *   - render exactly the ids the graph row carried (trimmed, deduped, order
 *     preserved) — nothing is merged in from other rows;
 *   - an element with no recorded sources gets the honest empty state, never
 *     a fabricated chip;
 *   - access stays server-side: opening resolves ``/explore/documents/{id}``
 *     and the evidence endpoints, which enforce case scope, jurisdiction and
 *     classification.  A stale id cannot leak another case's document.
 */
import { DocumentFileLink } from "../EvidenceLink";

export interface GraphSourceChipsProps {
  /** ``source_doc_ids`` (plus any singular ``source_doc_id``) from the row. */
  docIds: ReadonlyArray<string | null | undefined> | null | undefined;
  /** Small section heading rendered above the chips. */
  heading?: string;
  /**
   * Shown when the row carries no source ids.  Pass an empty string to hide
   * the section entirely instead.
   */
  emptyMessage?: string;
  /** How many chips to show directly; the remainder collapses behind +N. */
  max?: number;
}

export function GraphSourceChips({
  docIds,
  heading = "Sources",
  emptyMessage = "No source documents recorded for this selection.",
  max = 8,
}: GraphSourceChipsProps) {
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const raw of docIds ?? []) {
    const id = String(raw ?? "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }

  if (ids.length === 0) {
    if (!emptyMessage) return null;
    return (
      <div className="graph-source-chips graph-source-chips-empty">
        <span className="graph-source-chips-heading">{heading}</span>
        <p className="muted" style={{ fontSize: "var(--text-xs)", margin: "4px 0 0" }}>
          {emptyMessage}
        </p>
      </div>
    );
  }

  const shown = ids.slice(0, Math.max(1, max));
  const overflow = ids.length - shown.length;

  return (
    <div className="graph-source-chips">
      <span className="graph-source-chips-heading">{heading}</span>
      <div className="evidence-chips" style={{ marginTop: 4 }}>
        {shown.map((id) => (
          <DocumentFileLink key={id} docId={id} label={id} />
        ))}
        {overflow > 0 && <span className="badge badge-navy">+{overflow} more</span>}
      </div>
    </div>
  );
}
