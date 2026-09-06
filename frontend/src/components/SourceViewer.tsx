/**
 * The single source-inspection surface for the whole console.
 *
 * Every piece of evidence in CrimeLink — a case row, an entity, a
 * relationship, an AI answer's citation — opens here. The component renders
 * a file by what it *is*, not by what the API happened to squeeze its bytes
 * into:
 *
 *   PDF    → the real bytes, natively rendered by the browser
 *   CSV    → a paginated table
 *   XLSX   → sheet tabs, each a paginated table
 *   JSON   → formatted, addressable text
 *   DOCX   → extracted paragraphs/tables with basic structure preserved
 *   PPTX   → slides as extracted text
 *   image  → the real bytes, inline
 *   other  → an honest "unsupported preview" with metadata, open and download
 *
 * The never-allowed case — arbitrary binary decoded as UTF-8 and shown as
 * garbled symbols or raw `%PDF-1.3` syntax — is structurally impossible
 * through this path: binary renderers consume raw bytes, text renderers
 * consume only what the backend sniffed as text.
 *
 * Every state is explicit (AVAILABLE / LOADING / UNSUPPORTED / CORRUPTED /
 * NOT_FOUND / EXTRACTION_FAILED / NO_EXTRACTED_TEXT) with the reason the
 * backend reported — "No evidence" alone is not a state we show.
 */

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, download, fetchBlob } from "../api/client";
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
  sheets?: string[];
  sheet?: string | null;
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

export interface PreviewFileMeta {
  path: string;
  filename?: string;
  extension?: string;
  media_type?: string;
  size_bytes?: number;
  dataset_id?: string | null;
}

export interface PreviewResult {
  status: string;
  reason?: string | null;
  openable?: boolean;
  render_kind?: string;
  file?: PreviewFileMeta;
  window?: SourceWindow | null;
  pdf?: {
    page_count: number;
    text_available: boolean;
    pages: { page: number; text: string }[];
    truncated?: boolean;
  } | null;
  document_blocks?: ({ type: string; text?: string; level?: number | null; rows?: string[][] } | null)[] | null;
  slides?: { index: number; title: string; lines: string[] }[] | null;
  sheets?: string[] | null;
  sheet?: string | null;
  raw_url?: string | null;
  download_url?: string | null;
}

interface ReferencePayload {
  reference?: SourceReference;
  window?: SourceWindow | null;
  status?: string;
  reason?: string | null;
  preview_url?: string;
  raw_url?: string;
  case?: { id: string; case_number: string };
  document?: { id: string; filename: string; document_type: string };
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Human description of exactly which part of the source is being shown. */
export function describeLocation(ref?: SourceReference, win?: SourceWindow | null): string {
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

const STATUS_TONE: Record<string, string> = {
  AVAILABLE: "ok",
  NO_EXTRACTED_TEXT: "warn",
  UNSUPPORTED: "muted",
  CORRUPTED: "bad",
  NOT_FOUND: "bad",
  EXTRACTION_FAILED: "bad",
};

function StatusChip({ status, reason }: { status: string; reason?: string | null }) {
  return (
    <span className={`badge badge-${STATUS_TONE[status] ?? "muted"}`} title={reason ?? undefined}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function CsvView({
  win,
  fields,
  onLoadMore,
  loadingMore,
}: {
  win: SourceWindow;
  fields: string[];
  onLoadMore?: () => void;
  loadingMore?: boolean;
}) {
  const highlighted = new Set(win.highlight);
  const relevant = new Set(fields);
  return (
    <>
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
      {win.truncated && (
        <div className="source-note muted" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span>
            Showing {win.unit_label}s {win.start}–{win.end} of{" "}
            {win.total_units.toLocaleString()}.
          </span>
          {onLoadMore && (
            <button className="btn btn-small" onClick={onLoadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </div>
      )}
    </>
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

/**
 * PDF: the browser is the renderer. The bytes go to an <object> untouched —
 * no text conversion anywhere near them — and the extractable text layer is
 * offered alongside for searching/citations.
 */
function PdfView({
  path,
  preview,
}: {
  path: string;
  preview: PreviewResult | null;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setUrl(null);
    setError(null);
    fetchBlob(`/sources/raw?path=${encodeURIComponent(path)}`)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
        setUrl(objectUrl);
      })
      .catch((err: Error) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  const pages = preview?.pdf;
  return (
    <div className="pdf-view">
      {error && <ErrorState message={`The PDF could not be loaded: ${error}`} />}
      {!error && !url && <Spinner />}
      {url && (
        <object data={url} type="application/pdf" aria-label="PDF document" className="pdf-frame">
          <div className="source-note">
            This browser cannot display inline PDFs.{" "}
            <a href={url} target="_blank" rel="noreferrer">
              Open the PDF in a new tab
            </a>
            .
          </div>
        </object>
      )}
      {pages && (
        <details className="pdf-text-layer">
          <summary>
            Text layer · {pages.page_count.toLocaleString()} page
            {pages.page_count === 1 ? "" : "s"}
            {pages.truncated ? " (first 200 shown)" : ""}
          </summary>
          {pages.pages.map((page) => (
            <div key={page.page} className="pdf-page-text">
              <div className="source-gutter">page {page.page}</div>
              <pre>{page.text || "— no extractable text —"}</pre>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function ImageFileView({ path }: { path: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    fetchBlob(`/sources/raw?path=${encodeURIComponent(path)}`)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  if (!url) return <Spinner />;
  return (
    <div className="image-view">
      <img src={url} alt="Source image" style={{ maxWidth: "100%" }} />
    </div>
  );
}

function DocxView({ blocks }: { blocks: NonNullable<PreviewResult["document_blocks"]> }) {
  return (
    <article className="docx-view">
      {blocks.map((block, index) => {
        if (!block) return null;
        if (block.type === "table" && block.rows) {
          return (
            <table className="source-table" key={index}>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          );
        }
        if (block.type === "heading") {
          const level = Math.min(6, Math.max(1, block.level ?? 1));
          const Heading = `h${level}` as "h1";
          return <Heading key={index}>{block.text}</Heading>;
        }
        return <p key={index}>{block.text}</p>;
      })}
    </article>
  );
}

function PptxView({ slides }: { slides: NonNullable<PreviewResult["slides"]> }) {
  return (
    <div className="pptx-view">
      {slides.map((slide) => (
        <section className="pptx-slide" key={slide.index}>
          <header>
            <span className="source-gutter">slide {slide.index}</span>
            <strong>{slide.title}</strong>
          </header>
          <ul>
            {slide.lines.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function SourceViewerBody({
  target,
  context = 3,
  onLoaded,
}: {
  target: SourceTarget;
  context?: number;
  onLoaded?: (payload: ReferencePayload | PreviewResult) => void;
}) {
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [reference, setReference] = useState<SourceReference | undefined>(undefined);
  const [caseRef, setCaseRef] = useState<{ id: string; case_number: string } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewingFile, setViewingFile] = useState<string | null>(null);

  const effectiveTarget: SourceTarget = viewingFile
    ? { kind: "file", path: viewingFile }
    : target;

  const key =
    effectiveTarget.kind === "reference"
      ? `ref:${effectiveTarget.referenceId}`
      : `file:${effectiveTarget.path}`;

  const load = useCallback(() => {
    setPreview(null);
    setError(null);
    if (effectiveTarget.kind === "reference") {
      api<ReferencePayload>(
        `/sources/reference/${encodeURIComponent(effectiveTarget.referenceId)}?context=${context}`,
      )
        .then((data) => {
          setReference(data.reference);
          setCaseRef(data.case ?? null);
          const windowKindBySource: Record<string, string> = {
            csv: "csv",
            table: "xlsx",
            json: "json",
            txt: "text",
            document: "text",
          };
          setPreview({
            status: data.status ?? (data.window ? "AVAILABLE" : "NOT_FOUND"),
            reason: data.reason ?? null,
            window: data.window ?? null,
            openable: Boolean(data.window),
            render_kind: windowKindBySource[data.window?.source_type ?? ""] ?? "text",
            file: { path: data.reference?.origin_file ?? "" },
            raw_url: data.raw_url ?? null,
          });
          onLoaded?.(data);
        })
        .catch((err: Error) => setError(err.message));
      return;
    }
    const params = new URLSearchParams({ path: effectiveTarget.path, context: String(context) });
    if (effectiveTarget.row) params.set("row", String(effectiveTarget.row));
    if (effectiveTarget.lineStart) params.set("line_start", String(effectiveTarget.lineStart));
    if (effectiveTarget.lineEnd) params.set("line_end", String(effectiveTarget.lineEnd));
    api<PreviewResult>(`/sources/preview?${params.toString()}`)
      .then((data) => {
        setPreview(data);
        onLoaded?.(data);
      })
      .catch((err: Error) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, context]);

  useEffect(load, [load]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!preview) return <Spinner />;

  const win = preview.window ?? null;
  const fields = reference?.field_names ?? [];
  const path = preview.file?.path ?? "";
  const renderKind = preview.render_kind ?? "none";
  const status = preview.status ?? "AVAILABLE";
  const hasWindowContent = Boolean(win && (win.rows.length > 0 || win.lines.length > 0));

  const loadMore = () => {
    if (!win) return;
    setLoadingMore(true);
    const params = new URLSearchParams({ path, offset: String(win.end + 1), limit: "500" });
    api<PreviewResult>(`/sources/preview?${params.toString()}`)
      .then((data) => {
        if (data.window && preview.window) {
          setPreview({
            ...data,
            window: { ...data.window, rows: [...preview.window.rows, ...data.window.rows] },
          });
        } else {
          setPreview(data);
        }
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoadingMore(false));
  };

  return (
    <div className="source-body">
      <div className="source-meta">
        <div>
          <span className="source-label">State</span>
          <StatusChip status={status} reason={preview.reason} />
        </div>
        <div>
          <span className="source-label">File</span>
          <strong>{path}</strong>
        </div>
        <div>
          <span className="source-label">Type</span>
          <strong>{preview.file?.media_type ?? "—"}</strong>
        </div>
        <div>
          <span className="source-label">Size</span>
          <strong>{formatBytes(preview.file?.size_bytes)}</strong>
        </div>
        {win && (
          <div>
            <span className="source-label">{win.unit_label === "row" ? "Rows" : "Lines"}</span>
            <strong>{win.total_units.toLocaleString()}</strong>
          </div>
        )}
        {win && win.rows.length > 0 && (
          <div>
            <span className="source-label">Position</span>
            <strong>{describeLocation(reference, win)}</strong>
          </div>
        )}
        {caseRef && (
          <div>
            <span className="source-label">Case</span>
            <strong>{caseRef.case_number}</strong>
          </div>
        )}
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {path && (
            <button
              className="btn btn-small"
              type="button"
              onClick={() => void download(`/sources/raw?path=${encodeURIComponent(path)}&download=true`, preview.file?.filename ?? "source-file")}
            >
              Download
            </button>
          )}
          {target.kind === "reference" && reference?.origin_file && !viewingFile && (
            <button className="btn btn-small" type="button" onClick={() => setViewingFile(reference.origin_file)}>
              Open full file
            </button>
          )}
          {viewingFile && (
            <button className="btn btn-small" type="button" onClick={() => setViewingFile(null)}>
              Back to reference
            </button>
          )}
        </div>
      </div>

      {preview.reason && (
        <p className="source-note muted">{preview.reason}</p>
      )}

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

      {renderKind === "pdf" && path && <PdfView path={path} preview={preview} />}
      {renderKind === "image" && path && <ImageFileView path={path} />}
      {renderKind === "csv" && win && (
        <CsvView win={win} fields={fields} onLoadMore={win.truncated ? loadMore : undefined} loadingMore={loadingMore} />
      )}
      {renderKind === "xlsx" && win && (
        <>
          {preview.sheets && preview.sheets.length > 1 && (
            <div className="sheet-tabs" role="tablist">
              {preview.sheets.map((sheet) => (
                <button
                  key={sheet}
                  role="tab"
                  type="button"
                  className={sheet === preview.sheet ? "sheet-tab on" : "sheet-tab"}
                  onClick={() => {
                    // Sheet switching is a fresh load at the top of that sheet.
                    const params = new URLSearchParams({ path, sheet });
                    void api<PreviewResult>(`/sources/preview?${params.toString()}`).then(setPreview);
                  }}
                >
                  {sheet}
                </button>
              ))}
            </div>
          )}
          <CsvView win={win} fields={fields} onLoadMore={win.truncated ? loadMore : undefined} loadingMore={loadingMore} />
        </>
      )}
      {renderKind === "docx" && preview.document_blocks && preview.document_blocks.length > 0 && (
        <DocxView blocks={preview.document_blocks} />
      )}
      {renderKind === "pptx" && preview.slides && preview.slides.length > 0 && (
        <PptxView slides={preview.slides} />
      )}
      {(renderKind === "text" || renderKind === "json") && win && <TextView win={win} />}
      {renderKind === "document" && win && <TextView win={win} />}

      {status === "UNSUPPORTED" && (
        <Empty
          message={
            preview.reason ??
            "Unsupported file preview — the file is part of the evidence set, but there is no viewer for this format."
          }
        />
      )}
      {status === "NOT_FOUND" && (
        <Empty message={preview.reason ?? "The file is no longer stored with this dataset."} />
      )}
      {status === "CORRUPTED" && (
        <Empty message={preview.reason ?? "The file could not be parsed; it may be damaged."} />
      )}
      {status === "EXTRACTION_FAILED" && (
        <Empty message={preview.reason ?? "Content extraction failed for this file."} />
      )}
      {status === "NO_EXTRACTED_TEXT" && renderKind !== "pdf" && (
        <Empty message={preview.reason ?? "This document has no extractable text (it may be a scan)."} />
      )}
      {renderKind === "binary" && (
        <div className="unsupported-view">
          <Empty message="Unsupported file preview" />
          <p className="hint">
            CrimeLink does not decode unknown binary formats as text. The original file is
            available for download and retains its provenance metadata above.
          </p>
        </div>
      )}
      {!hasWindowContent &&
        status === "AVAILABLE" &&
        !["pdf", "image", "docx", "pptx"].includes(renderKind) &&
        !win && <Empty message="This source has no readable content at the requested position." />}
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

  const modal = (
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

  return typeof document !== "undefined" ? createPortal(modal, document.body) : modal;
}
