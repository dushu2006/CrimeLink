/**
 * Administration → Dataset console.
 *
 * One screen for the whole dataset lifecycle: upload anything, watch the
 * import happen, see which dataset is active, switch between them, and
 * rebuild the graph.
 *
 * Two principles drive the design:
 *
 *  * **Progress is reported, never simulated.** Every percentage and stage
 *    name comes from the server's job row. When the live socket is not
 *    available the console says it is polling instead of quietly pretending
 *    the feed is live.
 *  * **State is never assumed.** After a job ends the dataset list is
 *    re-fetched, so what is on screen is what the server holds — a refresh
 *    can never resurrect a dataset that has been replaced.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  acceptDatasetMappings,
  activateDataset,
  importDatasetFiles,
  listDatasetMappings,
  listDatasets,
  rebuildDatasetGraph,
  watchDatasetJob,
  type DatasetJob,
  type DatasetMapping,
  type DatasetSummary,
  type JobWatchTransport,
} from "../api/client";
import { Badge, Empty, Spinner } from "./Status";

/** Stages the pipeline reports, in the order they happen. */
const STAGES = [
  "VALIDATING",
  "NORMALIZING",
  "INGESTING",
  "BUILDING_RELATIONSHIPS",
  "BUILDING_GRAPH",
  "INDEXING",
  "READY",
] as const;

function stageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  const upper = stage.toUpperCase();
  if (upper === "COMPLETED" || upper === "READY") return STAGES.length - 1;
  return STAGES.findIndex((name) => name === upper);
}

function count(stats: Record<string, unknown> | undefined, key: string): string {
  const value = stats?.[key];
  return typeof value === "number" ? value.toLocaleString() : "—";
}

function when(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}


/**
 * Schema mappings the importer was not certain about.
 *
 * A header is a claim about a column; the values are the evidence. Where the
 * two disagreed the importer went with the values and recorded why — this
 * panel is where that reasoning is shown, and where an operator signs it off.
 * Nothing here is hidden behind a log file.
 */
function MappingReview({ dataset }: { dataset: DatasetSummary }) {
  const [items, setItems] = useState<DatasetMapping[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await listDatasetMappings(dataset.id, { needsReview: true });
      setItems(page.items);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [dataset.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function accept(fileIds: string[]) {
    setBusy(true);
    try {
      await acceptDatasetMappings(dataset.id, fileIds);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <div className="alert" role="alert">
        {error}
      </div>
    );
  }
  if (items === null) return <Spinner />;
  if (items.length === 0) {
    return (
      <p className="hint">
        Every table in this dataset mapped cleanly onto the canonical schema — no column
        needed a guess, and no header was contradicted by its own values.
      </p>
    );
  }

  return (
    <>
      <div className="row-actions">
        <span className="hint">
          {items.length} file{items.length === 1 ? "" : "s"} mapped with low confidence or a
          header the data disagreed with.
        </span>
        <button
          className="btn"
          disabled={busy}
          onClick={() => void accept(items.map((item) => item.file_id))}
          title="Record that these mappings have been reviewed"
        >
          Accept all mappings
        </button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>File</th>
            <th>Read as</th>
            <th>Confidence</th>
            <th>Rows</th>
            <th>Findings</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.file_id}>
              <td>
                <button
                  className="link-button"
                  onClick={() => setOpen(open === item.file_id ? null : item.file_id)}
                >
                  {item.path}
                </button>
                {open === item.file_id && (
                  <div className="hint">
                    {item.notes.map((note) => (
                      <div key={note.sheet}>
                        <strong>{note.sheet}</strong>
                        <ul>
                          {note.notes.map((line) => (
                            <li key={line}>{line}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                    {item.unmapped_columns.length > 0 && (
                      <div>Unmapped columns: {item.unmapped_columns.join(", ")}</div>
                    )}
                  </div>
                )}
              </td>
              <td>{item.semantic_type}</td>
              <td>{Math.round(item.confidence * 100)}%</td>
              <td>{item.row_count.toLocaleString()}</td>
              <td className="hint">
                {item.notes.reduce((total, note) => total + note.contradictions.length, 0)}{" "}
                contradicted column(s)
              </td>
              <td>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void accept([item.file_id])}
                >
                  Accept
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** A live progress read-out for one job. */
function JobProgress({
  job,
  transport,
}: {
  job: DatasetJob;
  transport: JobWatchTransport | null;
}) {
  const reached = stageIndex(job.stage);
  const failed = job.status === "FAILED";
  return (
    <div className="job-progress">
      <div className="job-progress-head">
        <strong>{job.kind === "import" ? "Importing dataset" : "Building graph"}</strong>
        <Badge value={job.status} />
        <span className="hint">{job.progress_pct}%</span>
      </div>

      <div
        className="progress-track"
        role="progressbar"
        aria-valuenow={job.progress_pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Job progress"
      >
        <div
          className={failed ? "progress-fill progress-fill-failed" : "progress-fill"}
          style={{ width: `${Math.max(2, Math.min(100, job.progress_pct))}%` }}
        />
      </div>

      <ol className="stage-list">
        {STAGES.map((stage, index) => {
          const state =
            failed && index === reached
              ? "failed"
              : index < reached || job.status === "SUCCEEDED"
                ? "done"
                : index === reached
                  ? "active"
                  : "pending";
          return (
            <li key={stage} className={`stage stage-${state}`}>
              <span className="stage-dot" aria-hidden="true" />
              <span className="stage-name">{stage.replace(/_/g, " ").toLowerCase()}</span>
            </li>
          );
        })}
      </ol>

      <p className="hint">{job.message ?? "Working…"}</p>

      {transport?.transport === "polling" && (
        <p className="hint">
          Live updates unavailable ({transport.reason}); polling for progress instead. The
          job is unaffected.
        </p>
      )}

      {failed && job.error && (
        <div className="alert" role="alert">
          {job.error}
        </div>
      )}

      {job.status === "SUCCEEDED" && Object.keys(job.result).length > 0 && (
        <ResultSummary result={job.result} />
      )}
    </div>
  );
}

function ResultSummary({ result }: { result: Record<string, unknown> }) {
  const graph = (result.graph ?? {}) as Record<string, unknown>;
  const canonical = (result.canonical ?? {}) as Record<string, unknown>;
  const files = (result.files ?? {}) as Record<string, unknown>;
  const rows: [string, unknown][] = [
    ["files read", files.files_usable ?? files.files_discovered],
    ["entities", canonical.entities ?? result.entities_considered],
    ["relationships", canonical.relationships ?? result.relationships_considered],
    ["cases", result.cases_created ?? result.cases],
    ["documents", result.documents_created],
    ["graph nodes", graph.nodes_written ?? result.nodes_written],
    ["graph edges", graph.edges_written ?? result.edges_written],
  ];
  const shown = rows.filter(([, value]) => typeof value === "number");
  if (shown.length === 0) return null;
  return (
    <p className="hint">
      {shown.map(([label, value]) => `${label} ${(value as number).toLocaleString()}`).join(" · ")}
    </p>
  );
}

export default function DatasetConsole({ jurisdictionId }: { jurisdictionId?: string }) {
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null);
  const [job, setJob] = useState<DatasetJob | null>(null);
  const [transport, setTransport] = useState<JobWatchTransport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [datasetName, setDatasetName] = useState("");
  const [selected, setSelected] = useState<File[]>([]);

  const filesRef = useRef<HTMLInputElement | null>(null);
  const folderRef = useRef<HTMLInputElement | null>(null);
  const unwatchRef = useRef<(() => void) | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listDatasets();
      setDatasets(data.items);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A watch must not outlive the panel: leaving Administration mid-build
  // should close the socket, not leak one per visit.
  useEffect(() => () => unwatchRef.current?.(), []);

  const watch = useCallback(
    (jobId: string) => {
      unwatchRef.current?.();
      setTransport(null);
      unwatchRef.current = watchDatasetJob(
        jobId,
        (update) => {
          setJob(update);
          if (update.terminal) {
            void refresh();
            setBusy(false);
            if (update.status === "FAILED") {
              setError(update.error ?? "The job failed.");
            }
          }
        },
        setTransport,
      );
    },
    [refresh],
  );

  async function upload() {
    if (selected.length === 0) {
      setError("Choose at least one file, a folder, or a ZIP archive first.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    setJob(null);
    try {
      const started = await importDatasetFiles(selected, { name: datasetName.trim() });
      setNotice(
        `Uploaded ${started.files_received} file(s). Import started — this dataset becomes ` +
          "the active one when it finishes.",
      );
      setJob(started.job);
      watch(started.job_id);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function activate(dataset: DatasetSummary) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await activateDataset(dataset.id);
      setNotice(`"${dataset.name}" is now the active dataset. Rebuilding its graph…`);
      await refresh();
      if (updated.job_id) {
        watch(updated.job_id);
      } else {
        setBusy(false);
      }
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function rebuild(dataset: DatasetSummary) {
    setBusy(true);
    setError(null);
    setNotice(null);
    setJob(null);
    try {
      const started = await rebuildDatasetGraph(dataset.id);
      setJob(started.job);
      watch(started.job_id);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  function pick(input: HTMLInputElement | null) {
    const files = Array.from(input?.files ?? []);
    setSelected(files);
    setError(null);
  }

  const active = datasets?.find((item) => item.is_active) ?? null;

  return (
    <section className="panel">
      <h2>Dataset</h2>
      <p className="hint">
        Everything the console shows — cases, people, the graph, search, AI retrieval — comes
        from the active dataset. Importing a replacement makes it active and removes the
        previous one from every page.
      </p>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="alert alert-ok" role="status">
          {notice}
        </div>
      )}

      {/* ---------------------------------------------------------------- */}
      <h3>Import</h3>
      <p className="hint">
        Any arrangement works: a single CSV or XLSX, a pile of mixed files, a ZIP, or a whole
        folder. No particular folder names or column headings are required — files are
        detected, parsed and mapped on the way in, and anything that cannot be mapped
        confidently is listed for review rather than guessed at.
      </p>

      <div className="row-actions">
        <input
          ref={filesRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(event) => pick(event.currentTarget)}
        />
        <input
          ref={folderRef}
          type="file"
          multiple
          // Folder picking is a Chromium/WebKit extension; the plain file
          // button above is always available as the fallback.
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          style={{ display: "none" }}
          onChange={(event) => pick(event.currentTarget)}
        />
        <button className="btn" onClick={() => filesRef.current?.click()} disabled={busy}>
          Choose files…
        </button>
        <button className="btn" onClick={() => folderRef.current?.click()} disabled={busy}>
          Choose folder…
        </button>
        <input
          placeholder="Dataset name (optional)"
          value={datasetName}
          onChange={(event) => setDatasetName(event.target.value)}
          disabled={busy}
        />
        <button
          className="btn btn-primary"
          onClick={() => void upload()}
          disabled={busy || selected.length === 0}
        >
          {busy ? "Working…" : `Import ${selected.length || ""} file(s)`.trim()}
        </button>
      </div>

      {selected.length > 0 && (
        <p className="hint">
          Selected: {selected.slice(0, 6).map((file) => file.name).join(", ")}
          {selected.length > 6 ? ` and ${selected.length - 6} more` : ""} (
          {(selected.reduce((total, file) => total + file.size, 0) / 1_048_576).toFixed(1)} MB)
        </p>
      )}

      {job && <JobProgress job={job} transport={transport} />}

      {/* ---------------------------------------------------------------- */}
      <h3>Datasets</h3>
      {datasets === null && !error && <Spinner />}
      {datasets?.length === 0 && (
        <Empty message="No dataset has been imported yet. Import one above and every page will fill in." />
      )}

      {datasets && datasets.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Entities</th>
              <th>Relationships</th>
              <th>Cases</th>
              <th>Graph built</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((dataset) => (
              <tr key={dataset.id} className={dataset.is_active ? "row-active" : undefined}>
                <td>
                  {dataset.name} <span className="hint">v{dataset.version}</span>
                  {dataset.is_active && (
                    <>
                      {" "}
                      <Badge value="ACTIVE" />
                    </>
                  )}
                </td>
                <td>
                  <Badge value={dataset.status} />
                  {dataset.error && <div className="hint">{dataset.error}</div>}
                </td>
                <td>{count(dataset.stats, "entities")}</td>
                <td>{count(dataset.stats, "relationships")}</td>
                <td>{count(dataset.stats, "cases")}</td>
                <td className="hint">{when(dataset.graph_built_at)}</td>
                <td>
                  <div className="row-actions">
                    {!dataset.is_active && (
                      <button
                        className="btn"
                        onClick={() => void activate(dataset)}
                        disabled={busy}
                        title="Make this the dataset every page reads"
                      >
                        Activate
                      </button>
                    )}
                    <button
                      className="btn"
                      onClick={() => void rebuild(dataset)}
                      disabled={busy}
                      title="Re-project this dataset into the graph"
                    >
                      {dataset.graph_built_at ? "Rebuild graph" : "Build graph"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {active && (
        <>
          <h3>Schema mapping review</h3>
          <MappingReview dataset={active} />
        </>
      )}

      {active && (
        <p className="hint">
          Active: <strong>{active.name}</strong> v{active.version} · activated{" "}
          {when(active.activated_at)} · {count(active.stats, "entities")} entities ·{" "}
          {count(active.stats, "documents")} documents
          {jurisdictionId ? ` · your jurisdiction ${jurisdictionId}` : ""}
        </p>
      )}
    </section>
  );
}
