/**
 * Temporal evidence view for the investigation workspace.
 *
 * It places the records the answer relied on in chronological order and
 * groups them relative to a reference moment (the incident, when one is
 * known). The grouping is a *position in time* and nothing more: the panel
 * states explicitly that ordering alone establishes no causation, because
 * temporal proximity is exactly the kind of signal that gets over-read.
 *
 * Future records are never silently mixed into an "around the incident"
 * reading — when the workspace pins a reference moment, later records are
 * shown separately and labelled.
 */

import { Link } from "react-router-dom";
import type { TimelineEntry } from "../../api/client";
import { timelineLabel, timelinePhase, timelineTimestamp, timelineUpTo } from "../../lib/investigator";

function phaseTitle(phase: ReturnType<typeof timelinePhase>): string {
  switch (phase) {
    case "before":
      return "Before the incident";
    case "during":
      return "Around the incident (±24h)";
    case "after":
      return "After the incident";
    default:
      return "Time not established";
  }
}

function EntryRow({ entry }: { entry: TimelineEntry }) {
  const ts = timelineTimestamp(entry);
  const docId = typeof entry.source_doc_id === "string" ? entry.source_doc_id : null;
  const docs = Array.isArray(entry.evidence_doc_ids)
    ? (entry.evidence_doc_ids as string[]).filter(Boolean)
    : [];
  const participants = Array.isArray(entry.participants)
    ? (entry.participants as { name?: string }[]).map((item) => item?.name).filter(Boolean)
    : [];

  return (
    <li className="inv-timeline-entry">
      <span className="inv-timeline-when">{ts ?? "no timestamp"}</span>
      <div className="inv-timeline-body">
        <span className="inv-timeline-label">{timelineLabel(entry)}</span>
        {typeof entry.event_type === "string" && entry.event_type && (
          <span className="badge badge-muted">{entry.event_type.replaceAll("_", " ")}</span>
        )}
        {typeof entry.location === "string" && entry.location && (
          <span className="inv-timeline-meta">{entry.location}</span>
        )}
        {participants.length > 0 && (
          <span className="inv-timeline-meta">{participants.join(", ")}</span>
        )}
        {(docId || docs.length > 0) && (
          <span className="inv-timeline-meta">
            {[docId, ...docs]
              .filter((value, index, array) => value && array.indexOf(value) === index)
              .slice(0, 3)
              .map((id) => (
                <Link key={String(id)} to={`/documents/${id}`} className="evidence-link">
                  <span className="evidence-icon" aria-hidden="true" />
                  <span>{String(id).slice(0, 8)}</span>
                </Link>
              ))}
          </span>
        )}
      </div>
    </li>
  );
}

export function InvestigationTimeline({
  entries,
  referenceIso,
  limit = 40,
}: {
  entries: TimelineEntry[] | null | undefined;
  referenceIso?: string | null;
  limit?: number;
}) {
  const all = entries ?? [];
  if (all.length === 0) {
    return (
      <p className="muted">
        No dated records are available in this scope, so no timeline can be reconstructed. That is
        a data gap, not a quiet period.
      </p>
    );
  }

  const visible = all.slice(0, limit);
  const usable = referenceIso ? timelineUpTo(visible, referenceIso) : visible;
  const laterCount = visible.length - usable.length;

  type Phase = "before" | "during" | "after" | "unknown";
  const groups: Record<Phase, TimelineEntry[]> = { before: [], during: [], after: [], unknown: [] };
  for (const entry of usable) {
    const phase = timelinePhase(timelineTimestamp(entry), referenceIso ?? null);
    groups[phase].push(entry);
  }

  const order: Phase[] = referenceIso
    ? ["before", "during", "after", "unknown"]
    : ["unknown"];

  return (
    <div className="inv-timeline">
      <p className="muted inv-timeline-note">
        Orders the records the analysis relied on. Events close together in time are not thereby
        related — ordering alone establishes no causation.
      </p>
      {order
        .filter((phase) => groups[phase].length > 0)
        .map((phase) => (
          <section key={phase} className={`inv-timeline-group inv-timeline-${phase}`}>
            <h4>
              {phaseTitle(phase)} <span className="inv-count">{groups[phase].length}</span>
            </h4>
            <ol className="timeline inv-timeline-list">
              {groups[phase].map((entry, index) => (
                <EntryRow key={`${timelineTimestamp(entry) ?? "na"}-${index}`} entry={entry} />
              ))}
            </ol>
          </section>
        ))}
      {laterCount > 0 && (
        <p className="muted inv-timeline-later">
          {laterCount} later record(s) fall after the reference moment and are excluded from the
          before/around reading above, so later evidence cannot be used to explain an earlier event.
        </p>
      )}
      {all.length > visible.length && (
        <p className="muted">
          Showing the first {visible.length} of {all.length} dated record(s).
        </p>
      )}
    </div>
  );
}
