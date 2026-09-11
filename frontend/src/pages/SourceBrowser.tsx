/**
 * Dataset sources.
 *
 * The listing is the ACTIVE dataset's file manifest — every file the import
 * discovered, what it became (document/case), and its explicit availability
 * state, straight from the manifest the pipeline wrote. It is not a folder
 * scan and not the bundled evaluation corpus: when a dataset is replaced,
 * this page shows the new one's files and nothing of the old one's.
 *
 * Every file that has bytes on disk is openable: PDFs render as PDFs,
 * CSV/XLSX as tables, JSON formatted, DOCX/PPTX as extracted content, and
 * formats with no viewer are declared UNSUPPORTED with a reason and a
 * download — never a silent "No evidence".
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { t } from "../i18n";
import { Empty, ErrorState, Spinner } from "../components/Status";
import SourceViewer from "../components/SourceViewer";

interface SourceRow {
  source_id?: string;
  dataset_id?: string;
  path: string;
  filename?: string;
  extension?: string;
  media_type?: string;
  file_kind?: string;
  semantic_type?: string;
  size_bytes: number;
  status: string;
  reason?: string | null;
  ingestion_status?: string | null;
  extraction_status?: string | null;
  page_count?: number | null;
  row_count?: number | null;
  sheets?: string[] | null;
  doc_id?: string | null;
  document_type?: string | null;
  case_id?: string | null;
  case_number?: string | null;
  reference_count: number;
  openable?: boolean;
  readable?: boolean;
  download_url?: string | null;
  section?: string | null;
}

interface FilesResponse {
  dataset_name: string | null;
  dataset_id?: string | null;
  dataset_status?: string | null;
  ok: boolean;
  issues: string[];
  warnings: string[];
  counts: Record<string, number>;
  items: SourceRow[];
}

const SOURCE_STATE_TONE: Record<string, string> = {
  AVAILABLE: "ok",
  NORMALIZED: "ok",
  INGESTED: "ok",
  DISCOVERED: "muted",
  SKIPPED: "muted",
  UNSUPPORTED: "muted",
  CORRUPTED: "bad",
  NOT_FOUND: "bad",
};

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function stateOf(item: SourceRow): string {
  const status = (item.status || "").toUpperCase();
  if (status === "CORRUPT") return "CORRUPTED";
  return status || "AVAILABLE";
}

export default function SourceBrowser() {
  const [data, setData] = useState<FilesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<SourceRow | null>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(() => {
    setData(null);
    setError(null);
    api<FilesResponse>("/sources/files")
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(load, [load]);

  const sections = useMemo(() => {
    const items = (data?.items ?? []).filter(
      (item) =>
        item.path.toLowerCase().includes(filter.toLowerCase()) ||
        (item.semantic_type ?? "").toLowerCase().includes(filter.toLowerCase()),
    );
    const grouped = new Map<string, SourceRow[]>();
    for (const item of items) {
      const dir = item.path.includes("/") ? item.path.split("/").slice(0, -1).join("/") : "root";
      const key = item.section ?? dir;
      grouped.set(key, [...(grouped.get(key) ?? []), item]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data, filter]);

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t("nav.sources")}</h1>
          <p className="muted">
            {data.dataset_name ? (
              <>
                {t("admin.dataset")} <strong>{data.dataset_name}</strong> ·{" "}
                {data.items.length} {t("cases.documents")} ·{" "}
                {data.dataset_status ?? "—"}
              </>
            ) : (
              t("state.empty")
            )}
          </p>
        </div>
        <div className="row-actions">
          <button className="btn" onClick={load} type="button">
            {t("sources.reload")}
          </button>
        </div>
      </header>

      {data.issues.length > 0 && (
        <div className="alert alert-bad">
          {data.issues.map((issue) => (
            <div key={issue}>{issue}</div>
          ))}
        </div>
      )}

      <input
        className="filter-input"
        placeholder={t("entities.searchPlaceholder")}
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />

      {sections.length === 0 && (
        <Empty
          message={
            !data.dataset_id || !data.dataset_name
              ? t("state.empty")
              : data.items.length === 0
              ? t("cases.empty")
              : t("graph.emptyView")
          }
        />
      )}

      {sections.map(([section, items]) => (
        <section className="card" key={section}>
          <h2>{section}</h2>
          <table className="table">
            <thead>
              <tr>
                <th>{t("doc.file")}</th>
                <th>{t("doc.status")}</th>
                <th>{t("doc.type")}</th>
                <th>{t("case.detail")}</th>
                <th className="num">{t("sources.size")}</th>
                <th className="num">Rows / pages</th>
                <th className="num">References</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const state = stateOf(item);
                return (
                  <tr key={item.path}>
                    <td>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => setOpen(item)}
                        title={item.media_type ?? undefined}
                      >
                        {item.filename ?? item.path.split("/").pop()}
                      </button>
                      <div className="hint">{item.path}</div>
                      {item.reason && <div className="hint">{item.reason}</div>}
                    </td>
                    <td>
                      <span className={`badge badge-${SOURCE_STATE_TONE[state] ?? "muted"}`}>
                        {state.replace(/_/g, " ")}
                      </span>
                      {item.ingestion_status && item.ingestion_status !== "COMPLETE" && (
                        <div className="hint">ingestion: {item.ingestion_status}</div>
                      )}
                    </td>
                    <td>{item.semantic_type ?? item.document_type ?? <span className="muted">—</span>}</td>
                    <td>{item.case_number ?? <span className="muted">—</span>}</td>
                    <td className="num">{formatBytes(item.size_bytes)}</td>
                    <td className="num">
                      {item.row_count ? item.row_count.toLocaleString() : "—"}
                      {item.page_count ? ` / ${item.page_count} p` : ""}
                      {item.sheets && item.sheets.length > 1 ? ` (${item.sheets.length} sheets)` : ""}
                    </td>
                    <td className="num">
                      {item.reference_count ? (
                        item.reference_count.toLocaleString()
                      ) : (
                        <span className="muted">0</span>
                      )}
                    </td>
                    <td>
                      <button className="btn btn-small" onClick={() => setOpen(item)}>
                        Open
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ))}

      {open && (
        <SourceViewer
          target={{
            kind: "file",
            path: open.path,
            row: undefined,
          }}
          title={open.filename ?? open.path}
          subtitle={`${open.media_type ?? ""} · ${stateOf(open)}`}
          onClose={() => setOpen(null)}
          footer={
            open.case_id ? (
              <span className="muted">
                Ingested as {open.document_type ?? "document"} evidence for case {open.case_number}
              </span>
            ) : null
          }
        />
      )}
    </div>
  );
}
