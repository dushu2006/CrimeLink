/**
 * Evidence rendering for the investigator workspace.
 *
 * The whole point of this component is that a finding is never a bare
 * sentence: every item shows its inference label, its stance (supporting vs
 * contradicting), and — wherever the backend recorded one — a pointer that
 * opens the record behind it.
 *
 * When there is genuinely no openable source, the component says so rather
 * than rendering a decorative link that goes nowhere.
 */

import { Link } from "react-router-dom";
import type { EvidenceItem, ProvenanceItem } from "../../api/client";
import { FileLink } from "../EvidenceLink";
import { Badge } from "../Status";
import { labelText, labelTone, provenanceTarget, provenanceText } from "../../lib/investigator";

/**
 * One provenance pointer, rendered by what it actually is.
 *
 * Exported so the workspace's own provenance list uses the same dispatch: a
 * graph edge, metric or dataset-level record is a reference, and must never be
 * rendered as a document link that dead-ends in the console.
 */
export function ProvenanceChip({
  pointer,
  caseId,
}: {
  pointer: ProvenanceItem;
  /** The case in scope, when there is one: graph and analytics are case-scoped. */
  caseId?: string | null;
}) {
  const text = provenanceText(pointer);
  const target = provenanceTarget(pointer, caseId);

  if (target.kind === "file") {
    // A row inside a dataset source file: open it at exactly that row.
    return (
      <FileLink
        path={target.to}
        row={target.row}
        lineStart={target.lineStart}
        lineEnd={target.lineEnd}
        label={text ?? pointer.label}
      />
    );
  }

  if (target.kind === "document") {
    return (
      <Link className="evidence-link" to={target.to} title={pointer.label || pointer.ref}>
        <span className="evidence-icon" aria-hidden="true" />
        <span>{text ?? pointer.label}</span>
      </Link>
    );
  }

  // Datasets, graph edges and computed metrics open the surface that owns them,
  // labelled so the label never claims more than the pointer carries.
  const vocabulary: Record<string, string> = {
    dataset: "dataset record",
    graph_edge: "graph edge",
    metric: "computed metric",
    audit: "audit record",
    note: "investigator note",
  };
  const kind = vocabulary[pointer.kind] ?? pointer.kind;
  if (target.kind === "dataset" || target.kind === "graph" || target.kind === "analytics") {
    return (
      <Link className="evidence-link inv-pointer-ref" to={target.to} title={pointer.ref}>
        <span className="badge badge-muted">{kind}</span>
        <span className="inv-pointer-label">{text ?? pointer.label ?? pointer.ref}</span>
      </Link>
    );
  }

  return (
    <span className="inv-pointer inv-pointer-ref" title={pointer.ref}>
      <span className="badge badge-muted">{kind}</span>
      <span className="inv-pointer-label">{pointer.label || pointer.ref}</span>
    </span>
  );
}

/** One evidenced claim. */
export function EvidenceRow({
  item,
  caseId,
}: {
  item: EvidenceItem;
  caseId?: string | null;
}) {
  const openable = (item.provenance ?? []).some(
    (pointer) => pointer.origin_file || pointer.doc_id,
  );
  return (
    <li className={`inv-evidence-row inv-stance-${item.stance}`}>
      <div className="inv-evidence-head">
        <span className={`badge badge-${labelTone(item.inference_label)}`}>
          {labelText(item.inference_label)}
        </span>
        {item.stance === "contradicts" && (
          <span className="badge badge-warn" title="Evidence that weakens this reading">
            contradicts
          </span>
        )}
        {item.stance === "context" && <span className="badge badge-muted">context</span>}
        <span className="inv-evidence-kind">{item.kind}</span>
      </div>
      <p className="inv-evidence-summary">{item.summary}</p>
      {(item.provenance ?? []).length > 0 && (
        <div className="inv-pointers">
          {(item.provenance ?? []).map((pointer, index) => (
            <ProvenanceChip
              key={`${pointer.kind}-${pointer.ref}-${index}`}
              pointer={pointer}
              caseId={caseId}
            />
          ))}
        </div>
      )}
      {!openable && (item.provenance ?? []).length === 0 && (
        <p className="muted inv-no-source">
          No source record is attached to this item — treat it as a derivable summary, not a citable record.
        </p>
      )}
    </li>
  );
}

export function EvidenceList({
  items,
  empty = "Nothing recorded here.",
  className = "",
  caseId,
}: {
  items: EvidenceItem[] | null | undefined;
  empty?: string;
  className?: string;
  /** Case in scope: lets graph/analytics references open their own surface. */
  caseId?: string | null;
}) {
  const list = items ?? [];
  if (list.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ul className={`inv-evidence-list ${className}`.trim()}>
      {list.map((item, index) => (
        <EvidenceRow
          key={`${item.kind}-${index}-${item.summary.slice(0, 24)}`}
          item={item}
          caseId={caseId}
        />
      ))}
    </ul>
  );
}

/**
 * Supporting and contradictory evidence side by side.
 *
 * This split is the anti-confirmation-bias surface: a reader must be able to
 * see immediately what argues *for* a reading and what argues *against* it.
 */
export function EvidenceDebate({
  supporting,
  contradicting,
}: {
  supporting: EvidenceItem[] | null | undefined;
  contradicting: EvidenceItem[] | null | undefined;
}) {
  const forItems = supporting ?? [];
  const againstItems = contradicting ?? [];
  return (
    <div className="inv-debate">
      <section className="inv-debate-col inv-debate-supports">
        <h4>
          Supporting evidence <span className="inv-count">{forItems.length}</span>
        </h4>
        <EvidenceList items={forItems} empty="No supporting record is attached yet." />
      </section>
      <section className="inv-debate-col inv-debate-contradicts">
        <h4>
          Contradictory evidence <span className="inv-count">{againstItems.length}</span>
        </h4>
        <EvidenceList
          items={againstItems}
          empty="No contradicting record was found — which is not the same as confirmation."
        />
      </section>
    </div>
  );
}

/** A flat list of strings (alternatives, contradictions, caveats). */
export function NoteList({
  items,
  empty,
  className = "",
}: {
  items: string[] | null | undefined;
  empty?: string;
  className?: string;
}) {
  const list = (items ?? []).filter((item) => String(item ?? "").trim().length > 0);
  if (list.length === 0) return empty ? <p className="muted">{empty}</p> : null;
  return (
    <ul className={`inv-note-list ${className}`.trim()}>
      {list.map((item, index) => (
        <li key={`${index}-${String(item).slice(0, 24)}`}>{item}</li>
      ))}
    </ul>
  );
}

/** Inference-label chips, used as the workspace's vocabulary legend. */
export function LabelLegend({ labels }: { labels: string[] }) {
  if (labels.length === 0) return null;
  return (
    <div className="inv-legend">
      <span className="inv-legend-title">Labels used in this answer</span>
      {labels.map((label) => (
        <span key={label} className={`badge badge-${labelTone(label)}`}>
          {labelText(label)}
        </span>
      ))}
    </div>
  );
}

/** Re-exported for the pages that render a lone badge. */
export { Badge };
