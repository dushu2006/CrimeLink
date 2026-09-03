/**
 * The single source-inspection surface for the whole console.
 *
 * Every evidence reference in CrimeLink — on a case, an entity, a relationship,
 * a detection, a review item or an AI answer — opens this one component. It
 * adapts to the source type (CSV rows/cells, text lines) rather than existing
 * in ten near-identical variants.
 *
 * It never renders anything it was not given: no placeholder rows, no invented
 * filenames. When the backend has no exact position, the viewer says so instead
 * of silently opening the top of the file.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Empty, ErrorState, Spinner } from "./Status";

export interface SourceWindow {
  file: string;
  source_type: string;
  total_units: number;
  unit_label: string;
  start: number;
  end: number;
  highlight: number[];
  columns: string[];
  rows: { row: number; values: Record<string, string> }[];
  lines: { line: number; text: string }[];
  truncated: boolean;
  size_bytes: number;
}

export interface SourceReference {
  id?: string;
  doc_id?: string;
  case_id?: string;
  origin_file: string;
  source_type?: string;
  record_id?: string | null;
  row_number?: number | null;
  field_names?: string[];
  field_values?: Record<string, string>;
  page_number?: number | null;
  line_start?: number | null;
  line_end?: number | null;
  text_start?: number | null;
  text_end?: number | null;
  excerpt?: string | null;
}

/** What the viewer was asked to open. */
export type SourceTarget =
  | { kind: "reference"; referenceId: string }
  | {
      kind: "file";
      path: string;
      row?: number | null;
      lineStart?: number | null;
      lineEnd?: number | null;
    };

interface Payload {
  reference?: SourceReference;
  window: SourceWindow;
  case?: { id: string; case_number: string };
  document?: { id: string; filename: string; document_type: string };
}

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Human description of exactly which part of the source is being shown. */
export function describeLocation(ref?: SourceReference, win?: SourceWindow): string {
  if (ref?.row_number) return `Row ${ref.row_number}`;
  if (ref?.line_start) {
    return ref.line_end && ref.line_end !== ref.line_start
      ? `Lines ${ref.line_start}–${ref.line_end}`
      : `Line ${ref.line_start}`;
  }
  if (ref?.page_number) return `Page ${ref.page_number}`;
  if (win?.highlight?.length) {
    const first = win.highlight[0];
    const last = win.highlight[win.highlight.length - 1];
    const label = win.unit_label === "row" ? "Row" : "Line";
    return first === last ? `${label} ${first}` : `${label}s ${first}–${last}`;
  }
  return "Whole file";
}

function CsvView({ win, fields }: { win: SourceWindow; fields: string[] }) {
  const highlighted = new Set(win.highlight);
  const relevant = new Set(fields);
  return (
    <div className="source-table-wrap">
      <table className="source-table">
        <thead>
          <tr>
            <th className="source-gutter">{win.unit_label}</th>
            {win.columns.map((column) => (
              <th key={column} className={relevant.has(column) ? "field-relevant" : undefined}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {win.rows.map((row) => {
            const on = highlighted.has(row.row);
            return (
              <tr key={row.row} className={on ? "row-highlight" : undefined}>
                <td className="source-gutter">{row.row}</td>
                {win.columns.map((column) => (
                  <td
                    key={column}
                    /* Highlight the specific cells the finding depended on, not
                       merely the row, so the investigator sees *why* it matched. */
                    className={on && relevant.has(column) ? "cell-highlight" : undefined}
                  >
                    {row.values[column] || <span className="muted">—</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TextView({ win }: { win: SourceWindow }) {
  const highlighted = new Set(win.highlight);
  return (
    <pre className="source-lines">
      {win.lines.map((line) => (
        <div
          key={line.line}
          className={highlighted.has(line.line) ? "line line-highlight" : "line"}
        >
          <span className="source-gutter">{line.line}</span>
          <span className="line-text">{line.text || " "}</span>
        </div>
      ))}
    </pre>
  );
}

export function SourceViewerBody({
  target,
  context = 3,
  onLoaded,
}: {
  target: SourceTarget;
  context?: number;
  onLoaded?: (payload: Payload) => void;
}) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const key =
    target.kind === "reference"
      ? `ref:${target.referenceId}`
      : `file:${target.path}:${target.row ?? ""}:${target.lineStart ?? ""}:${target.lineEnd ?? ""}`;

  const load = useCallback(() => {
    setPayload(null);
    setError(null);
    const path =
      target.kind === "reference"
        ? `/sources/reference/${encodeURIComponent(target.referenceId)}?context=${context}`
        : (() => {
            const params = new URLSearchParams({ path: target.path, context: String(context) });
            if (target.row) params.set("row", String(target.row));
            if (target.lineStart) params.set("line_start", String(target.lineStart));
            if (target.lineEnd) params.set("line_end", String(target.lineEnd));
            return `/sources/file?${params.toString()}`;
          })();

    api<Payload | SourceWindow>(path)
      .then((data) => {
        // /sources/file returns a bare window; /sources/reference wraps it.
        const normalised: Payload =
          "window" in (data as Payload)
            ? (data as Payload)
            : { window: data as SourceWindow };
        setPayload(normalised);
        onLoaded?.(normalised);
      })
      .catch((err: Error) => setError(err.message));
    // `key` collapses the target into a stable dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, context]);

  useEffect(load, [load]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!payload) return <Spinner />;

  const { window: win, reference } = payload;
  const fields = reference?.field_names ?? [];
  const hasContent = win.rows.length > 0 || win.lines.length > 0;

  return (
    <div className="source-body">
      <div className="source-meta">
        <div>
          <span className="source-label">Source location</span>
          <strong>{describeLocation(reference, win)}</strong>
        </div>
        <div>
          <span className="source-label">File</span>
          <strong>{win.file}</strong>
        </div>
        <div>
          <span className="source-label">
            {win.unit_label === "row" ? "Rows" : "Lines"}
          </span>
          <strong>{win.total_units.toLocaleString()}</strong>
        </div>
        <div>
          <span className="source-label">Size</span>
          <strong>{formatBytes(win.size_bytes)}</strong>
        </div>
      </div>

      {reference?.record_id && (
        <div className="source-record">
          Record <code>{reference.record_id}</code>
          {fields.length > 0 && (
            <>
              {" · evidence fields "}
              {fields.map((field) => (
                <code key={field} className="field-chip">
                  {field}
                </code>
              ))}
            </>
          )}
        </div>
      )}

      {!hasContent ? (
        <Empty message="This source has no readable content at the requested position." />
      ) : win.source_type === "csv" ? (
        <CsvView win={win} fields={fields} />
      ) : (
        <TextView win={win} />
      )}

      {win.truncated && (
        <p className="source-note muted">
          Showing {win.unit_label}s {win.start}–{win.end} of{" "}
          {win.total_units.toLocaleString()}. Only the relevant range is loaded.
        </p>
      )}
    </div>
  );
}

/** Modal wrapper used when evidence is opened from inside another screen. */
export default function SourceViewer({
  target,
  title = "Source evidence",
  subtitle,
  onClose,
  footer,
}: {
  target: SourceTarget;
  title?: string;
  subtitle?: string;
  onClose: () => void;
  footer?: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal source-modal" onClick={(event) => event.stopPropagation()}>
        <header className="modal-head">
          <div>
            <strong>{title}</strong>
            {subtitle && <span className="brand-sub">{subtitle}</span>}
          </div>
          <button className="btn btn-small" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="modal-scroll">
          <SourceViewerBody target={target} />
        </div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}
