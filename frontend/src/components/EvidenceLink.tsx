/**
 * The clickable affordance for a piece of evidence.
 *
 * Wherever CrimeLink shows evidence, it must be obvious that it can be opened
 * and precisely what will open — "cdr.csv · row 18342", not a bare filename.
 * Rendering this consistently is what stops the investigator having to guess.
 *
 * If there is genuinely no exact position, the label says so rather than
 * implying a precision the backend cannot deliver.
 */

import { useState } from "react";
import { api } from "../api/client";
import SourceViewer, { SourceReference, SourceTarget } from "./SourceViewer";

/** Evidence pointer as carried on a graph node or edge. */
export interface EvidencePointer {
  source_doc_id?: string | null;
  text_span?: number[] | null;
  origin?: {
    file: string;
    row?: number | null;
    record_id?: string | null;
    fields?: string[];
    values?: Record<string, string>;
  } | null;
}

function shortName(path: string): string {
  const clean = path.split("#")[0];
  const parts = clean.split("/");
  return parts[parts.length - 1] || clean;
}

export function evidenceLabel(pointer?: EvidencePointer | null): string | null {
  const origin = pointer?.origin;
  if (!origin?.file) return null;
  const name = shortName(origin.file);
  if (origin.row) return `${name} · row ${origin.row}`;
  return name;
}

/** Open a source reference stored in the database. */
export function ReferenceLink({
  reference,
  label,
  footer,
}: {
  reference: SourceReference;
  label?: string;
  footer?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const name = shortName(reference.origin_file);
  const position = reference.row_number
    ? `row ${reference.row_number}`
    : reference.line_start
      ? reference.line_end && reference.line_end !== reference.line_start
        ? `lines ${reference.line_start}–${reference.line_end}`
        : `line ${reference.line_start}`
      : null;

  return (
    <>
      <button
        type="button"
        className="evidence-link"
        onClick={() => setOpen(true)}
        title={`Open ${reference.origin_file}${position ? ` at ${position}` : ""}`}
      >
        <span className="evidence-icon" aria-hidden="true" />
        <span>{label ?? name}</span>
        {position && <span className="evidence-pos">{position}</span>}
      </button>
      {open && reference.id && (
        <SourceViewer
          target={{ kind: "reference", referenceId: reference.id }}
          subtitle={reference.origin_file}
          onClose={() => setOpen(false)}
          footer={footer}
        />
      )}
      {open && !reference.id && (
        <SourceViewer
          target={{
            kind: "file",
            path: reference.origin_file,
            row: reference.row_number ?? undefined,
            lineStart: reference.line_start ?? undefined,
            lineEnd: reference.line_end ?? undefined,
          }}
          subtitle={reference.origin_file}
          onClose={() => setOpen(false)}
          footer={footer}
        />
      )}
    </>
  );
}

/** Open the origin carried inline on a graph node or edge. */
export function EvidencePointerLink({
  pointer,
  footer,
  emptyMessage = "No source reference recorded",
}: {
  pointer?: EvidencePointer | null;
  footer?: React.ReactNode;
  emptyMessage?: string;
}) {
  const origin = pointer?.origin;
  if (!origin?.file) {
    // Honest empty state: never fabricate a source to fill the space.
    return <span className="muted">{emptyMessage}</span>;
  }
  return (
    <ReferenceLink
      reference={{
        origin_file: origin.file,
        row_number: origin.row ?? null,
        record_id: origin.record_id ?? null,
        field_names: origin.fields ?? [],
        field_values: origin.values ?? {},
      }}
      footer={footer}
    />
  );
}

/**
 * Open an ingested *document* (by doc id) inside the same SourceViewer modal.
 *
 * A provenance pointer of kind "document" carries a doc_id rather than a file
 * path.  To keep the viewer consistent — every piece of evidence opens in the
 * same in-page overlay, never navigating away — the document's origin file is
 * resolved from the document endpoint and then shown exactly like any other
 * source.  If the origin cannot be resolved, the button stays honest (it does
 * not dead-end as a fake document link).
 */
export function DocumentFileLink({
  docId,
  originFile,
  row,
  lineStart,
  lineEnd,
  label,
}: {
  docId: string;
  originFile?: string | null;
  row?: number | null;
  lineStart?: number | null;
  lineEnd?: number | null;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [resolvedPath, setResolvedPath] = useState<string | null>(originFile ?? null);
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<boolean>(Boolean(originFile));

  const openViewer = () => {
    setOpen(true);
    if (resolvedPath) return;
    if (resolving || resolved) return;
    setResolving(true);
    api<{ origin?: { file: string } | null; relative_path?: string | null }>(
      `/explore/documents/${encodeURIComponent(docId)}`,
    )
      .then((detail) => {
        const path = detail?.origin?.file || detail?.relative_path || null;
        setResolvedPath(path);
        setResolved(true);
      })
      .catch(() => {
        setResolved(true);
      })
      .finally(() => setResolving(false));
  };

  const target: SourceTarget | null = resolvedPath
    ? {
        kind: "file",
        path: resolvedPath,
        row: row ?? undefined,
        lineStart: lineStart ?? undefined,
        lineEnd: lineEnd ?? undefined,
      }
    : null;

  return (
    <>
      <button
        type="button"
        className="evidence-link"
        onClick={openViewer}
        title={label ?? "Open document in the source viewer"}
      >
        <span className="evidence-icon" aria-hidden="true" />
        <span>{label ?? "document"}</span>
        {row ? <span className="evidence-pos">row {row}</span> : null}
      </button>
      {open && target && (
        <SourceViewer
          target={target}
          subtitle={resolvedPath ?? undefined}
          onClose={() => setOpen(false)}
        />
      )}
      {open && !target && (
        <p className="muted" style={{ marginTop: "var(--space-1)" }}>
          {resolved
            ? "This document has no openable origin file in the active dataset — it can be reviewed from its document page."
            : "Resolving document…"}
        </p>
      )}
    </>
  );
}

/** Open an arbitrary dataset file, optionally at a position. */
export function FileLink({
  path,
  row,
  lineStart,
  lineEnd,
  label,
}: {
  path: string;
  row?: number | null;
  lineStart?: number | null;
  lineEnd?: number | null;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const target: SourceTarget = {
    kind: "file",
    path,
    row: row ?? undefined,
    lineStart: lineStart ?? undefined,
    lineEnd: lineEnd ?? undefined,
  };
  return (
    <>
      <button type="button" className="evidence-link" onClick={() => setOpen(true)}>
        <span className="evidence-icon" aria-hidden="true" />
        <span>{label ?? shortName(path)}</span>
        {row ? <span className="evidence-pos">row {row}</span> : null}
      </button>
      {open && (
        <SourceViewer target={target} subtitle={path} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
