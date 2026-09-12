/**
 * Investigator analysis workspace.
 *
 * This is the page that turns CrimeLink from a graph viewer plus a chat box
 * into an investigation surface. It renders the backend's structured
 * investigator answer rather than a wall of prose, in the order an
 * investigator actually works:
 *
 *   objective → scope → entities → patterns → relationships → hypotheses →
 *   supporting vs contradictory evidence → alternatives → assessment →
 *   data gaps → next direction → provenance
 *
 * Three rules shape the whole page:
 *
 *  1. The objective stays on screen: the investigator always knows what is
 *     being investigated, and follow-up questions continue the same thread.
 *  2. Model text is never presented as if it were the deterministic analysis;
 *     the narrative is labelled with who produced it.
 *  3. Criminal status and network importance are never conflated — a person
 *     can be analytically central with no recorded status, or confirmed with
 *     low centrality, and the UI says so in words.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  investigate,
  investigationPatterns,
  type AssessmentSection,
  type DataGap,
  type Hypothesis,
  type InvestigatorResponse,
  type NextStep,
  type ResolvedEntity,
  type SuspiciousPattern,
} from "../api/client";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";
import { TechnicalDetails } from "../components/TechnicalDetails";
import {
  EvidenceList,
  LabelLegend,
  NoteList,
  ProvenanceChip,
} from "../components/investigator/InvestigatorEvidence";
import { HypothesisCard } from "../components/investigator/HypothesisCard";
import { PatternCard, PatternList } from "../components/investigator/PatternCard";
import {
  FocusedEvidenceGraph,
  type GraphSelection,
} from "../components/investigator/FocusedEvidenceGraph";
import { InvestigationTimeline } from "../components/investigator/InvestigationTimeline";
import {
  centralityNarrative,
  convergenceSentence,
  entityStanding,
  gapHeading,
  gapSentence,
  labelText,
  labelsPresent,
  modelUnavailableNote,
  nextStepLink,
  orderHypotheses,
  orderNextSteps,
  patternKey,
  patternSourceCount,
  patternTypeLabel,
  provenanceOfProse,
  scopeLabel,
  scopeSentence,
  selectionEntityKeys,
  subgraphFor,
  suggestedQuestions,
} from "../lib/investigator";

type Selection =
  | { kind: "pattern"; value: SuspiciousPattern }
  | { kind: "hypothesis"; value: Hypothesis }
  | { kind: "relationship"; value: InvestigatorResponse["relationships"][number] }
  | null;

const STORAGE_PREFIX = "crimelink:investigation-thread:";

export default function InvestigatorWorkspace() {
  const { caseId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const masterScope = searchParams.get("scope") === "master";
  const scopeCaseId = masterScope ? "" : caseId;
  const scopeKey = scopeCaseId ? `case:${scopeCaseId}` : "master";

  const [question, setQuestion] = useState("");
  const [objective, setObjective] = useState("");
  const [response, setResponse] = useState<InvestigatorResponse | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [graphSelection, setGraphSelection] = useState<GraphSelection | null>(null);
  const [scan, setScan] = useState<SuspiciousPattern[] | null>(null);
  const [scanLabel, setScanLabel] = useState("");
  const [scanError, setScanError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  /* ---------------------------------------------------------------- scan */

  const refreshScan = useCallback(() => {
    setScan(null);
    setScanError(null);
    investigationPatterns({ caseId: scopeCaseId || null, maxPatterns: 25, includeExcluded: true })
      .then((result) => {
        setScan(result.patterns);
        setScanLabel(result.scope_label);
      })
      .catch((err: Error) => setScanError(err.message));
  }, [scopeCaseId]);

  useEffect(() => {
    refreshScan();
    // Scope change invalidates the previous answer and its thread.
    setResponse(null);
    setSelection(null);
    setGraphSelection(null);
    setHistory([]);
    setError(null);
    const remembered = window.sessionStorage.getItem(STORAGE_PREFIX + scopeKey);
    setThreadId(remembered);
  }, [refreshScan, scopeKey]);

  /* -------------------------------------------------------------- submit */

  const run = useCallback(
    async (raw: string, explicitObjective?: string) => {
      const asked = raw.trim();
      if (asked.length < 3 || loading) return;
      setLoading(true);
      setError(null);
      try {
        const continuing = Boolean(threadId);
        const next = await investigate({
          question: asked,
          case_id: scopeCaseId || null,
          investigation_id: continuing ? threadId : null,
          objective: explicitObjective?.trim() ? explicitObjective.trim() : null,
        });
        setResponse(next);
        setThreadId(next.investigation_id);
        window.sessionStorage.setItem(STORAGE_PREFIX + scopeKey, next.investigation_id);
        setHistory((prior) => [...prior, asked]);
        setSelection(null);
        setGraphSelection(null);
        setQuestion("");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [loading, scopeCaseId, scopeKey, threadId],
  );

  const startNewThread = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_PREFIX + scopeKey);
    setThreadId(null);
    setResponse(null);
    setSelection(null);
    setHistory([]);
    setQuestion("");
  }, [scopeKey]);

  /* ------------------------------------------------------------ derived */

  const entities: ResolvedEntity[] = response?.entities ?? [];
  const patterns = response?.patterns ?? [];
  const livePatternsForChips = response ? patterns : scan ?? [];
  const chips = useMemo(
    () => suggestedQuestions(livePatternsForChips, entities.map((entity) => entity.display_name)),
    [livePatternsForChips, entities],
  );

  const highlightKeys = selectionEntityKeys(selection, entities);
  const focused = response?.focused_graph ?? null;
  const selectionGraph = useMemo(
    () => (selection ? subgraphFor(focused, highlightKeys) : null),
    [selection, focused, highlightKeys],
  );

  const livePatterns = useMemo(() => patterns.filter((pattern) => !pattern.excluded), [patterns]);
  const excludedPatterns = useMemo(() => patterns.filter((pattern) => pattern.excluded), [patterns]);
  const hypotheses = useMemo(() => orderHypotheses(response?.hypotheses), [response]);
  const assessment: AssessmentSection | null = response?.assessment ?? null;

  const statedObjective = response?.objective ?? (objective.trim() || "Not stated yet");
  const metricLines =
    selection?.kind === "pattern" &&
    (selection.value.kind === "COMMUNITY_SIGNAL" || selection.value.kind === "NETWORK_BRIDGE")
      ? centralityNarrative(selection.value)
      : [];
  const continuing = Boolean(threadId) && history.length > 0;

  /* --------------------------------------------------------------- view */

  return (
    <div className="page inv-workspace">
      <header className="page-head">
        <div>
          <h1>Investigation Analysis</h1>
          <p className="muted">
            Evidence-backed assessment of the active dataset — findings, patterns, hypotheses,
            contradictions, gaps and next direction, each traceable to its source.
          </p>
        </div>
        <div className="row-actions">
          <span className="badge badge-navy" title="What this analysis is allowed to read">
            Scope: {response ? scopeLabel(response.scope) : scanLabel || (scopeCaseId ? "Case" : "Master Network")}
          </span>
          {scopeCaseId ? (
            <>
              <Link className="btn btn-secondary" to={`/cases/${scopeCaseId}/investigation`}>
                Stage workflow
              </Link>
              <Link className="btn btn-secondary" to={`/cases/${scopeCaseId}/investigate?scope=master`}>
                Switch to master network
              </Link>
            </>
          ) : (
            caseId && (
              <Link className="btn btn-secondary" to={`/cases/${caseId}/investigate`}>
                Switch to case scope
              </Link>
            )
          )}
        </div>
      </header>

      {/* ------------------------------------------------ objective (34.2) */}
      <section className="panel inv-objective" aria-labelledby="inv-objective-title">
        <div className="inv-objective-head">
          <h2 id="inv-objective-title">Investigation objective</h2>
          <span className={`badge badge-${continuing ? "navy" : "muted"}`}>
            {continuing ? `Thread · ${history.length} question(s)` : "New investigation"}
          </span>
        </div>
        <p className="inv-objective-text">{statedObjective}</p>
        <div className="inv-objective-meta">
          <span className="muted">
            {response
              ? scopeSentence(response.scope)
              : scanLabel
                ? `Signal scan over ${scanLabel}. Ask a question to run a full investigation.`
                : "Loading the scope…"}
          </span>
          {history.length > 0 && (
            <button type="button" className="btn btn-tertiary btn-small" onClick={startNewThread}>
              Start a new thread
            </button>
          )}
        </div>
        {!response && (
          <div className="form-row inv-objective-form">
            <input
              className="inline-input"
              placeholder="Optional: state the objective explicitly (e.g. determine whether these two cases are connected)"
              value={objective}
              maxLength={400}
              onChange={(event) => setObjective(event.target.value)}
              aria-label="Investigation objective"
            />
          </div>
        )}
      </section>

      {/* ------------------------------------------------- question (34.12) */}
      <section className="panel inv-ask">
        <form
          className="inv-ask-form"
          onSubmit={(event) => {
            event.preventDefault();
            void run(question, objective);
          }}
        >
          <input
            className="inline-input inv-ask-input"
            placeholder={
              continuing
                ? "Follow up — the thread keeps the same objective and memory"
                : "Ask an investigative question about named people, phones, vehicles, accounts or cases"
            }
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            aria-label="Investigation question"
          />
          <button className="btn btn-primary" type="submit" disabled={loading || question.trim().length < 3}>
            {loading ? "Investigating…" : continuing ? "Ask follow-up" : "Investigate"}
          </button>
        </form>
        {chips.length > 0 && (
          <div className="inv-chips">
            {chips.map((chip) => (
              <button
                key={chip}
                type="button"
                className="chip"
                onClick={() => {
                  setQuestion(chip);
                  void run(chip, objective);
                }}
                disabled={loading}
              >
                {chip}
              </button>
            ))}
          </div>
        )}
        {continuing && history.length > 0 && (
          <p className="muted inv-thread-note">
            This question continues the same investigation, so earlier findings, hypotheses and
            gaps are carried forward. Previous questions: {history.slice(-3).join(" · ")}
          </p>
        )}
      </section>

      {error && <ErrorState message={error} onRetry={() => void run(history[history.length - 1] ?? "")} />}

      {loading && !response && <Spinner label="Running the investigation pipeline…" />}

      {/* ------------------------------------ pre-question signal board */}
      {!response && !loading && (
        <>
          {scanError && (
            <ErrorState
              message={`Pattern scan unavailable: ${scanError}`}
              onRetry={() => refreshScan()}
            />
          )}
          {!scan && !scanError && <Spinner label="Scanning the active dataset for unusual patterns…" />}
          {scan && (
            <section className="panel">
              <div className="inv-objective-head">
                <h2>Suspicious / unusual patterns</h2>
                <button type="button" className="btn btn-tertiary btn-small" onClick={() => refreshScan()}>
                  Re-scan
                </button>
              </div>
              <p className="muted">
                Deterministic detectors over the active dataset. These are investigative signals
                that prioritise where to look — they never establish criminal status.
              </p>
              <PatternList
                patterns={scan}
                onSelect={(pattern) =>
                  setSelection((current) =>
                    current?.kind === "pattern" && patternKey(current.value) === patternKey(pattern)
                      ? null
                      : { kind: "pattern", value: pattern },
                  )
                }
              />
              {selection?.kind === "pattern" && (
                <div className="inv-finding-detail">
                  <FindingDetail
                    title={patternTypeLabel(selection.value.kind)}
                    subtitle={selection.value.title}
                    entities={entities}
                    seeds={selection.value.entity_keys}
                    graph={subgraphFor(focused, selection.value.entity_keys)}
                    onGraphSelect={setGraphSelection}
                    evidence={selection.value.evidence}
                    contradictions={selection.value.contradictions_considered}
                    alternatives={selection.value.innocent_alternatives}
                    caseId={scopeCaseId}
                  />
                </div>
              )}
            </section>
          )}
        </>
      )}

      {/* ----------------------------------------------- full answer view */}
      {response && (
        <>
          <section className="panel inv-answer-head">
            <div className="inv-objective-head">
              <h2>Findings</h2>
              <span className="badge badge-navy">
                {response.facts.length} fact(s) · {response.relationships.length} relationship(s)
              </span>
            </div>
            <LabelLegend labels={labelsPresent(response)} />
            <p className="muted">
              What the records directly establish. Facts are computed from the dataset; the
              interpretations further down are labelled separately.
            </p>
            <EvidenceList
              items={response.facts}
              empty="No fact was established for this question."
              caseId={scopeCaseId}
            />
          </section>

          <section className="panel">
            <h2>Entities in scope</h2>
            {entities.length === 0 ? (
              <p className="muted">
                No entity from the question could be matched to the active dataset. Unknown names
                stay unknown rather than being guessed at.
              </p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Resolved to</th>
                    <th>Criminal status (from the record)</th>
                    <th>Analytical standing</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((entity) => {
                    const standing = entityStanding(entity);
                    return (
                      <tr key={entity.canonical_id}>
                        <td>
                          <strong>{entity.display_name}</strong>
                          {entity.aliases.length > 0 && (
                            <span className="muted"> · a.k.a. {entity.aliases.join(", ")}</span>
                          )}
                        </td>
                        <td>
                          {entity.resolved ? (
                            <>
                              <Badge value={entity.label} />{" "}
                              <span className="muted">
                                {entity.matched_by} match, {Math.round(entity.confidence * 100)}%
                              </span>
                            </>
                          ) : (
                            <span className="badge badge-muted">no matching record</span>
                          )}
                        </td>
                        <td>
                          {standing.criminalStatus ? (
                            <Badge value={standing.criminalStatus} />
                          ) : (
                            <span className="muted">{standing.criminalStatusText}</span>
                          )}
                        </td>
                        <td className="muted">{standing.analyticStanding}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            <p className="muted inv-standing-note">
              Criminal status is read only from the dataset's criminal-record / charge-sheet
              evidence. Network position, centrality and pattern signals never change it.
            </p>
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Suspicious / unusual patterns</h2>
              <span className="badge badge-muted">{livePatterns.length} live signal(s)</span>
            </div>
            <p className="muted">
              Detected by deterministic analysis. A signal is a reason to look, not a conclusion.
            </p>
            <PatternList
              patterns={patterns}
              selectedKind={selection?.kind === "pattern" ? patternKey(selection.value) : null}
              onSelect={(pattern) =>
                setSelection((current) =>
                  current?.kind === "pattern" && patternKey(current.value) === patternKey(pattern)
                    ? null
                    : { kind: "pattern", value: pattern },
                )
              }
            />
            {excludedPatterns.length > 0 && (
              <p className="muted">
                {excludedPatterns.length} combination(s) were examined and set aside openly; they
                are listed above so the decision is reviewable.
              </p>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Relationship analysis</h2>
              <span className="badge badge-muted">
                {response.relationships.length} relationship(s)
              </span>
            </div>
            {response.relationships.length === 0 ? (
              <p className="muted">
                No record joins the entities in this question inside the active scope. That may
                mean the connection does not exist, or that the connecting record is not in the
                dataset — see the data gaps below.
              </p>
            ) : (
              <ul className="inv-relationship-list">
                {response.relationships.map((relationship) => (
                  <li
                    key={`${relationship.kind}-${relationship.title}`}
                    className={`inv-relationship ${selection?.kind === "relationship" && selection.value.title === relationship.title ? "inv-selected" : ""}`}
                  >
                    <div className="inv-relationship-head">
                      <span className="badge badge-navy">{relationship.kind.replaceAll("_", " ")}</span>
                      <Badge value={relationship.inference_label} />
                      <strong>{relationship.title}</strong>
                    </div>
                    <p>{relationship.description}</p>
                    {relationship.path && relationship.path.nodes.length > 0 && (
                      <p className="muted">
                        Path: {relationship.path.nodes.join(" → ")}
                        {relationship.path.description ? ` (${relationship.path.description})` : ""}
                      </p>
                    )}
                    {relationship.analysis && (
                      <dl className="inv-observation">
                        <div>
                          <dt>Observation</dt>
                          <dd>{relationship.analysis.observation}</dd>
                        </div>
                        <div>
                          <dt>Interpretation</dt>
                          <dd>{relationship.analysis.interpretation}</dd>
                        </div>
                        <div>
                          <dt>Assessment</dt>
                          <dd>{relationship.analysis.assessment}</dd>
                        </div>
                      </dl>
                    )}
                    <div className="row-actions">
                      <button
                        type="button"
                        className="btn btn-tertiary btn-small"
                        onClick={() =>
                          setSelection(
                            selection?.kind === "relationship" && selection.value.title === relationship.title
                              ? null
                              : { kind: "relationship", value: relationship },
                          )
                        }
                      >
                        {selection?.kind === "relationship" && selection.value.title === relationship.title
                          ? "Hide evidence"
                          : "Why is this relationship here?"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <div className="inv-objective-head">
              <h2>Hypotheses</h2>
              <span className="badge badge-muted">{hypotheses.length} under test</span>
            </div>
            <p className="muted">
              Each reading is tested against the evidence — including evidence that weakens it.
            </p>
            {hypotheses.length === 0 ? (
              <p className="muted">
                No hypothesis was generated: the question produced no resolved entity pair to test.
              </p>
            ) : (
              hypotheses.map((hypothesis) => (
                <HypothesisCard
                  key={hypothesis.id}
                  hypothesis={hypothesis}
                  selected={
                    selection?.kind === "hypothesis" && selection.value.id === hypothesis.id
                  }
                  onSelect={(value) =>
                    setSelection((current) =>
                      current?.kind === "hypothesis" && current.value.id === value.id
                        ? null
                        : { kind: "hypothesis", value },
                    )
                  }
                />
              ))
            )}
          </section>

          <section className="panel">
            <h2>Alternative explanations</h2>
            <p className="muted">
              Reasonable non-criminal readings of the same records. They do not cancel a signal —
              they set the bar the signal has to clear.
            </p>
            <NoteList
              items={response.alternative_explanations}
              empty="No alternative reading was recorded for this question."
            />
          </section>

          {assessment && (
            <section className="panel inv-assessment">
              <h2>Assessment</h2>
              <div className={`inv-strength-banner inv-strength-${assessment.overall_strength.toLowerCase()}`}>
                <strong>{assessment.overall_strength} support</strong>
                <span className="muted">
                  {Math.round((assessment.overall_confidence ?? 0) * 100)}% weighting ·{" "}
                  {convergenceSentence(assessment)}
                </span>
              </div>
              <dl className="inv-observation inv-observation-wide">
                <div>
                  <dt>Observation</dt>
                  <dd>{assessment.observation}</dd>
                </div>
                <div>
                  <dt>Interpretation</dt>
                  <dd>{assessment.interpretation}</dd>
                </div>
                <div>
                  <dt>Assessment</dt>
                  <dd>{assessment.assessment}</dd>
                </div>
              </dl>
              <p className="muted inv-prose-source">{provenanceOfProse(assessment)}</p>
              {modelUnavailableNote(assessment) && (
                <p className="muted">{modelUnavailableNote(assessment)}</p>
              )}
              {assessment.model.available && assessment.model.summary && (
                <div className="inv-narrative">
                  <h4>Narrative explanation</h4>
                  <p>{assessment.model.summary}</p>
                  {assessment.model.interpretation && <p>{assessment.model.interpretation}</p>}
                  {assessment.model.assessment && <p>{assessment.model.assessment}</p>}
                  <NoteList items={assessment.model.caveats} className="inv-note-warn" />
                  {assessment.model.language_edits.length > 0 && (
                    <TechnicalDetails
                      label={`language guard (${assessment.model.language_edits.length} edit(s))`}
                    >
                      <NoteList items={assessment.model.language_edits} />
                    </TechnicalDetails>
                  )}
                </div>
              )}
              <NoteList items={assessment.caveats} className="inv-note-warn" empty="" />
            </section>
          )}

          <section className="panel inv-gaps">
            <h2>Data gaps</h2>
            <p className="muted">
              Missing or incomplete evidence. A missing source is never read as proof that nothing
              happened.
            </p>
            {response.gaps.length === 0 ? (
              <p className="muted">No data gap was recorded for this question.</p>
            ) : (
              <ul className="inv-gap-list">
                {response.gaps.map((gap: DataGap, index) => (
                  <li key={`${gap.category}-${index}`} className="inv-gap">
                    <div className="inv-gap-head">
                      <span className="badge badge-muted">{labelText(gap.inference_label)}</span>
                      <strong>{gapHeading(gap)}</strong>
                    </div>
                    <p>{gapSentence(gap)}</p>
                    {gap.what_would_help && (
                      <p className="muted">
                        <strong>What would close it:</strong> {gap.what_would_help}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h2>Next investigative direction</h2>
            <p className="muted">
              Recommendations derived from the gaps and the strongest current reading. They ask for
              records, checks and reviews — the investigative decision stays with the investigator.
            </p>
            {response.next_steps.length === 0 ? (
              <p className="muted">No follow-up direction was derived.</p>
            ) : (
              <ol className="inv-next-list">
                {orderNextSteps(response.next_steps).map((step: NextStep, index) => {
                  const link = nextStepLink(step);
                  return (
                    <li key={`${index}-${step.action.slice(0, 24)}`} className={`inv-next inv-next-${step.priority}`}>
                      <div className="inv-next-head">
                        <span className={`badge badge-${step.priority === "high" ? "warn" : "muted"}`}>
                          {step.priority}
                        </span>
                        <strong>{step.action}</strong>
                      </div>
                      <p className="muted">{step.rationale}</p>
                      {link.kind === "case" && link.value && (
                        <Link className="btn btn-tertiary btn-small" to={`/cases/${link.value}/investigate`}>
                          Open case scope
                        </Link>
                      )}
                      {link.kind === "entity" && link.value && (
                        <Link className="btn btn-tertiary btn-small" to={`/entities/${encodeURIComponent(link.value)}`}>
                          Open entity
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          {/* -------------------------------------------------- focused view */}
          <section className="panel inv-detail-panel">
            <div className="inv-objective-head">
              <h2>
                {selection
                  ? "Focused evidence view"
                  : "Focused evidence graph"}
              </h2>
              {selection && (
                <button type="button" className="btn btn-tertiary btn-small" onClick={() => setSelection(null)}>
                  Back to the question graph
                </button>
              )}
            </div>
            {selection ? (
              <FindingDetail
                title={
                  selection.kind === "pattern"
                    ? patternTypeLabel(selection.value.kind)
                    : selection.kind === "hypothesis"
                      ? `Hypothesis ${selection.value.id}`
                      : `Relationship · ${selection.value.kind}`
                }
                subtitle={
                  selection.kind === "pattern"
                    ? selection.value.title
                    : selection.kind === "hypothesis"
                      ? selection.value.statement
                      : selection.value.title
                }
                entities={entities}
                seeds={highlightKeys}
                graph={selectionGraph}
                selection={graphSelection}
                onGraphSelect={setGraphSelection}
                evidence={
                  selection.kind === "pattern"
                    ? selection.value.evidence
                    : selection.kind === "hypothesis"
                      ? selection.value.supporting
                      : selection.value.evidence
                }
                contradictions={
                  selection.kind === "pattern"
                    ? selection.value.contradictions_considered
                    : selection.kind === "hypothesis"
                      ? (selection.value.contradicting ?? []).map((item) => item.summary)
                      : []
                }
                alternatives={
                  selection.kind === "pattern"
                    ? selection.value.innocent_alternatives
                    : selection.kind === "hypothesis"
                      ? selection.value.innocent_alternatives
                      : []
                }
                sources={
                  selection.kind === "pattern" ? patternSourceCount(selection.value) : undefined
                }
                caseId={scopeCaseId}
                whyLines={selection.kind === "pattern" ? metricLines : []}
              />
            ) : (
              <>
                <p className="muted">
                  Only the entities and relationships involved in this question — never the whole
                  dataset graph. Select a pattern, hypothesis or relationship above to narrow it
                  further.
                </p>
                <FocusedEvidenceGraph
                  graph={focused}
                  onSelect={setGraphSelection}
                  highlightKeys={highlightKeys}
                />
                {graphSelection && (
                  <p className="muted inv-graph-selection">
                    Selected {graphSelection.kind}: <strong>{graphSelection.name}</strong>{" "}
                    ({graphSelection.label})
                  </p>
                )}
              </>
            )}
          </section>

          <section className="panel">
            <h2>Timeline</h2>
            <InvestigationTimeline entries={response.timeline} />
          </section>

          <section className="panel">
            <h2>Provenance</h2>
            <p className="muted">
              Every record the analysis is allowed to open. Findings above link back into these
              sources; the answer never cites a document that is not in the active dataset.
            </p>
            {response.provenance.length === 0 ? (
              <p className="muted">
                No source document is in scope, so nothing in this answer can be opened against a
                record yet — a data gap rather than a clean result.
              </p>
            ) : (
              <ul className="inv-provenance-list">
                {response.provenance.map((pointer) => (
                  <li key={`${pointer.kind}-${pointer.ref}`}>
                    {/* Rendered by kind: documents and source rows open, graph
                        edges, metrics and dataset-level records are shown as the
                        references they are instead of as links that dead-end. */}
                    <ProvenanceChip pointer={pointer} caseId={scopeCaseId} />
                    {pointer.detail && <span className="muted"> · {pointer.detail}</span>}
                    {pointer.content_hash && (
                      <span className="hash" title="Content hash (tamper-evident evidence fingerprint)">
                        {pointer.content_hash.slice(0, 12)}…
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {response.memory && (
              <TechnicalDetails
                label={`investigation memory (${response.memory.questions_asked} question(s))`}
              >
                {/* What this thread has established — and, just as important,
                    what it has already ruled out, so a follow-up cannot quietly
                    re-open a reading the investigation set aside. */}
                <ul className="kv">
                  <li>
                    <dt>Objective</dt>
                    <dd>{response.memory.objective || response.objective}</dd>
                  </li>
                  <li>
                    <dt>Questions</dt>
                    <dd>{response.memory.prior_questions.join(" · ")}</dd>
                  </li>
                  <li>
                    <dt>Facts</dt>
                    <dd>
                      {response.memory.confirmed_facts.length > 0
                        ? `${response.memory.confirmed_facts.length} recorded`
                        : "none yet"}
                    </dd>
                  </li>
                  <li>
                    <dt>Open gaps</dt>
                    <dd>{response.memory.open_gaps.length}</dd>
                  </li>
                  <li>
                    <dt>Open contradictions</dt>
                    <dd>
                      {response.memory.contradictions.length > 0
                        ? `${response.memory.contradictions.length} recorded`
                        : "none recorded"}
                    </dd>
                  </li>
                  <li>
                    <dt>Readings set aside</dt>
                    <dd>
                      {response.memory.rejected_hypotheses.length === 0
                        ? "none yet"
                        : response.memory.rejected_hypotheses
                            .map((item) => `${item.id}: ${item.reason}`)
                            .join(" · ")}
                    </dd>
                  </li>
                </ul>
                <EvidenceList
                  items={[
                    ...response.memory.relationships.map((line) => ({
                      kind: "relationship" as const,
                      summary: line,
                      inference_label: "FACT",
                      stance: "context" as const,
                      provenance: [],
                    })),
                  ]}
                  empty="No relationship has been established in this thread yet."
                  className="inv-memory-notes"
                />
              </TechnicalDetails>
            )}
          </section>
        </>
      )}

      {!response && !loading && scan && scan.length === 0 && (
        <section className="panel">
          <Empty message="No unusual pattern is visible for this scope. Ask a question above to run a full evidence-backed investigation." />
        </section>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Finding detail (34.5)                                                   */
/* ---------------------------------------------------------------------- */

function FindingDetail({
  title,
  subtitle,
  entities,
  seeds,
  graph,
  selection,
  onGraphSelect,
  evidence,
  contradictions,
  alternatives,
  sources,
  caseId = "",
  whyLines = [],
}: {
  title: string;
  subtitle: string;
  entities: ResolvedEntity[];
  seeds: string[];
  graph: ReturnType<typeof subgraphFor> | null;
  selection?: GraphSelection | null;
  onGraphSelect?: (selection: GraphSelection | null) => void;
  evidence: InvestigatorResponse["relationships"][number]["evidence"];
  contradictions: string[];
  alternatives: string[];
  sources?: number;
  caseId?: string;
  whyLines?: string[];
}) {
  const seeded = graph ?? { nodes: [], edges: [], truncated: false };
  const involved = entities.filter((entity) => seeds.includes(entity.canonical_id));

  return (
    <div className="inv-finding">
      <header className="inv-finding-head">
        <span className="badge badge-navy">{title}</span>
        <h3>{subtitle}</h3>
        {sources !== undefined && (
          <span className="badge badge-muted">
            {sources} source{sources === 1 ? "" : "s"}
          </span>
        )}
      </header>

      <div className="inv-finding-grid">
        <div>
          <h4>Supporting evidence</h4>
          <EvidenceList
            items={evidence}
            empty="No evidence item is attached to this finding."
            caseId={caseId}
          />

          {contradictions.length > 0 && (
            <>
              <h4>Contradictory evidence</h4>
              <NoteList items={contradictions} />
            </>
          )}

          <h4>Alternative explanations</h4>
          <NoteList
            items={alternatives}
            empty="No alternative reading was recorded for this finding."
          />

          {involved.length > 0 && (
            <>
              <h4>Related entities</h4>
              <table className="table">
                <thead>
                  <tr>
                    <th>Entity</th>
                    <th>Criminal status (from the record)</th>
                    <th>Analytical standing</th>
                  </tr>
                </thead>
                <tbody>
                  {involved.map((entity) => {
                    const standing = entityStanding(entity);
                    return (
                      <tr key={entity.canonical_id}>
                        <td>{entity.display_name}</td>
                        <td>
                          {standing.criminalStatus ? (
                            <Badge value={standing.criminalStatus} />
                          ) : (
                            <span className="muted">{standing.criminalStatusText}</span>
                          )}
                        </td>
                        <td className="muted">{standing.analyticStanding}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}

          {involved.length === 0 && (
            <p className="muted">
              This finding names canonical entity keys that are not among the question's resolved
              entities, so no status is shown for them.
            </p>
          )}
        </div>

        <div>
          <h4>Focused evidence graph</h4>
          <FocusedEvidenceGraph graph={seeded} height={280} onSelect={onGraphSelect} highlightKeys={seeds} />
          {selection && (
            <p className="muted inv-graph-selection">
              Selected {selection.kind}: <strong>{selection.name}</strong> ({selection.label})
            </p>
          )}
          <p className="muted">
            Every relationship drawn here is backed by the records listed on the left; the graph is
            a view of that evidence, not a separate source.
          </p>
        </div>
      </div>

      <TechnicalDetails label="why this finding is highlighted">
        <NoteList
          items={[
            ...whyLines,
            "Relationships are computed from the records in scope; edge counts and metrics are deterministic, not model-generated.",
            "Network position (degree, betweenness, communities) describes the shape of the network and never changes criminal status.",
          ]}
        />
      </TechnicalDetails>
    </div>
  );
}
