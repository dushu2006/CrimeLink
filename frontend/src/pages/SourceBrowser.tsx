/**
 * Dataset file explorer.
 *
 * Lists the real files the source adapter discovered, with the number of source
 * references actually recorded against each one. Every readable file opens in
 * the shared Source Viewer at a chosen position.
 *
 * Evaluation material (ground_truth/, metadata/) is listed as excluded and is
 * deliberately NOT openable: it exists to measure detection accuracy, and must
 * never become investigator-visible evidence.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import SourceViewer from "../components/SourceViewer";

interface FileRow {
  path: string;
  status: string;
  section: string | null;
  size_bytes: number;
  document_type: string | null;
  reason: string | null;
  readable: boolean;
  reference_count: number;
}

interface FilesResponse {
  root: string;
  dataset_name: string;
  ok: boolean;
  issues: string[];
  warnings: string[];
  counts: Record<string, number>;
  items: FileRow[];
}

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SourceBrowser() {
  const [data, setData] = useState<FilesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<FileRow | null>(null);
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
    const items = (data?.items ?? []).filter((item) =>
      item.path.toLowerCase().includes(filter.toLowerCase()),
    );
    const grouped = new Map<string, FileRow[]>();
    for (const item of items) {
      const key = item.section ?? "other";
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
          <h1>Dataset sources</h1>
          <p className="muted">
            {data.dataset_name} · {data.items.length} files discovered
          </p>
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
        placeholder="Filter files…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />

      {sections.length === 0 && <Empty message="No files match this filter." />}

      {sections.map(([section, items]) => (
        <section className="card" key={section}>
          <h2>{section}</h2>
          <table className="table">
            <thead>
              <tr>
                <th>File</th>
                <th>Status</th>
                <th>Type</th>
                <th className="num">Size</th>
                <th className="num">References</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.path}>
                  <td>
                    {item.readable ? (
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => setOpen(item)}
                      >
                        {item.path}
                      </button>
                    ) : (
                      <span>{item.path}</span>
                    )}
                    {item.reason && <div className="hint">{item.reason}</div>}
                  </td>
                  <td>
                    <Badge value={item.status.toUpperCase()} />
                  </td>
                  <td>{item.document_type ?? <span className="muted">—</span>}</td>
                  <td className="num">{formatBytes(item.size_bytes)}</td>
                  <td className="num">
                    {item.reference_count ? (
                      item.reference_count.toLocaleString()
                    ) : (
                      <span className="muted">0</span>
                    )}
                  </td>
                  <td>
                    {item.readable ? (
                      <button
                        className="btn btn-small"
                        onClick={() => setOpen(item)}
                      >
                        Open
                      </button>
                    ) : (
                      <span className="muted">Not evidence</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {open && (
        <SourceViewer
          target={{ kind: "file", path: open.path }}
          title={open.path}
          subtitle={`${open.section ?? ""} · ${open.status}`}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}
