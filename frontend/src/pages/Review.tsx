import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { t } from "../i18n";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";

interface NodeSide {
  provenance_key: string;
  name: string;
  label: string;
  confidence: number;
  aliases: string[];
  source_doc_ids: string[];
}

interface MatchItem {
  id: string;
  status: string;
  similarity_score: number;
  match_basis: string;
  evidence_doc_ids: string[];
  age_hours: number;
  sla_hours: number;
  sla_breached: boolean;
  resolution_note: string | null;
  source: NodeSide;
  target: NodeSide;
}

interface PatternItem {
  id: string;
  pattern_type: string;
  confidence: number;
  status: string;
  explanation: string;
  details: Record<string, unknown>;
  entities: { provenance_key: string; name: string; label: string }[];
  evidence_doc_ids: string[];
  detected_at: string | null;
}

const TABS = ["identity", "patterns"] as const;

export default function Review() {
  const { caseId = "" } = useParams();
  const [tab, setTab] = useState<(typeof TABS)[number]>("identity");
  const [matches, setMatches] = useState<MatchItem[] | null>(null);
  const [patterns, setPatterns] = useState<PatternItem[] | null>(null);
  const [sla, setSla] = useState<{ breached?: number; total?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Fetch only the data the visible tab renders.  Loading both lists on every
  // mount made a single Review visit issue two requests (four in dev under
  // StrictMode) and helped exhaust the server rate limiter; the hidden tab's
  // list is fetched lazily when the investigator switches to it.
  const load = useCallback(() => {
    setError(null);
    if (tab === "identity") {
      api<{ items: MatchItem[]; sla: { breached?: number; total?: number } }>(
        `/resolution?case_id=${caseId}&limit=200`,
      )
        .then((matchData) => {
          setMatches(matchData.items);
          setSla(matchData.sla ?? null);
        })
        .catch((err: Error) => setError(err.message));
    } else {
      api<{ items: PatternItem[] }>(`/patterns?case_id=${caseId}&limit=200`)
        .then((patternData) => setPatterns(patternData.items))
        .catch((err: Error) => setError(err.message));
    }
  }, [caseId, tab]);

  useEffect(load, [load]);

  async function decide(path: string, id: string, body: unknown) {
    const note = (notes[id] ?? "").trim();
    if (!note) {
      setMessage(t("review.noteHint"));
      return;
    }
    setBusyId(id);
    setMessage(null);
    try {
      await api(path, { method: "POST", body: JSON.stringify({ ...(body as object), note }) });
      setNotes({ ...notes, [id]: "" });
      load();
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    if (tab === "identity" && !matches) return <ErrorState message={error} onRetry={load} />;
    if (tab === "patterns" && !patterns) return <ErrorState message={error} onRetry={load} />;
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>{t("review.title")}</h1>
        {sla?.breached ? (
          <span className="pill pill-bad">
            {t("review.sla")} {t("review.breached")}: {sla.breached}
          </span>
        ) : null}
      </header>

      <div className="tabs">
        {TABS.map((name) => (
          <button
            key={name}
            className={tab === name ? "tab tab-on" : "tab"}
            onClick={() => setTab(name)}
          >
            {name === "identity" ? t("review.identity") : t("review.patterns")}
          </button>
        ))}
      </div>

      {message && <div className="alert">{message}</div>}

      {tab === "identity" && (
        <>
          {!matches && <Spinner />}
          {matches && matches.length === 0 && <Empty />}
          {matches?.map((item) => (
            <article className="panel" key={item.id}>
              <div className="match-head">
                <div>
                  <strong>{item.source.name}</strong> <span className="muted">({item.source.label})</span>
                </div>
                <div className="score">{(Number(item.similarity_score) * 100).toFixed(0)}%</div>
                <div>
                  <strong>{item.target.name}</strong> <span className="muted">({item.target.label})</span>
                </div>
                <Badge value={item.status} />
              </div>

              <p className="hint">
                {item.match_basis} · {item.source.source_doc_ids.length + item.target.source_doc_ids.length}{" "}
                supporting document(s) · {item.age_hours.toFixed(1)}h of {item.sla_hours}h SLA
                {item.sla_breached ? " — breached" : ""}
              </p>

              {item.status === "PENDING" ? (
                <div className="form-row">
                  <input
                    placeholder={t("review.note")}
                    value={notes[item.id] ?? ""}
                    onChange={(e) => setNotes({ ...notes, [item.id]: e.target.value })}
                  />
                  <button
                    className="btn btn-primary"
                    disabled={busyId === item.id}
                    onClick={() => decide(`/resolution/${item.id}/merge`, item.id, {})}
                  >
                    {t("review.merge")}
                  </button>
                  <button
                    className="btn"
                    disabled={busyId === item.id}
                    onClick={() => decide(`/resolution/${item.id}/reject`, item.id, {})}
                  >
                    {t("review.reject")}
                  </button>
                </div>
              ) : (
                <p className="hint">
                  {item.status} {item.resolution_note ? `— "${item.resolution_note}"` : ""}
                  {item.status === "MERGED" && (
                    <>
                      {" "}
                      <button
                        className="btn btn-small"
                        onClick={() =>
                          api(`/resolution/${item.id}/unmerge`, {
                            method: "POST",
                            body: JSON.stringify({ note: "Reversed by investigator" }),
                          }).then(load)
                        }
                      >
                        {t("review.unmerge")}
                      </button>
                    </>
                  )}
                </p>
              )}
            </article>
          ))}
        </>
      )}

      {tab === "patterns" && (
        <>
          {!patterns && <Spinner />}
          {patterns && patterns.length === 0 && <Empty />}
          {patterns?.map((item) => (
            <article className="panel" key={item.id}>
              <div className="match-head">
                <strong>{item.pattern_type}</strong>
                <div className="score">{(Number(item.confidence) * 100).toFixed(0)}%</div>
                <Badge value={item.status} />
              </div>
              <p>{item.explanation}</p>
              <ul className="compact">
                {item.entities.map((entity) => (
                  <li key={entity.provenance_key}>
                    {entity.name} <span className="muted">({entity.label})</span>
                  </li>
                ))}
              </ul>
              {item.status === "NEW" && (
                <div className="form-row">
                  <input
                    placeholder={t("review.note")}
                    value={notes[item.id] ?? ""}
                    onChange={(e) => setNotes({ ...notes, [item.id]: e.target.value })}
                  />
                  <button
                    className="btn btn-primary"
                    disabled={busyId === item.id}
                    onClick={() => decide(`/patterns/${item.id}/review`, item.id, { decision: "REVIEWED" })}
                  >
                    {t("review.confirm")}
                  </button>
                  <button
                    className="btn"
                    disabled={busyId === item.id}
                    onClick={() => decide(`/patterns/${item.id}/review`, item.id, { decision: "DISMISSED" })}
                  >
                    {t("review.dismiss")}
                  </button>
                </div>
              )}
              <p className="hint">{item.evidence_doc_ids.length} evidence document(s)</p>
            </article>
          ))}
        </>
      )}
    </div>
  );
}
