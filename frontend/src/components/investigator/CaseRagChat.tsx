import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { api, askCaseStream } from "../../api/client";
import { EvidenceDrawer, type EvidenceDrawerData } from "./EvidenceDrawer";
import {
  type CaseContextSummary,
  degradedCaseContext,
  deriveCaseIntelligenceMetrics,
} from "../../lib/caseIntelligence";

const PHASES: Record<string, string> = {
  started: "Retrieving case context…",
  retrieving: "Retrieving case evidence…",
  generating: "Generating grounded answer…",
  validating: "Validating evidence references…",
  fast_path: "Answering from case context…",
};

interface ClaimCitation {
  claim_text?: string;
  claim?: string;
  evidence_id?: string;
  evidence_refs?: string[];
  support_status?: string;
  support_level?: string;
  corroboration?: string | null;
}

interface FindingResult {
  id?: string;
  title?: string;
  summary?: string;
  direct_answer?: string;
  evidence_explanation?: string;
  investigator_interpretation?: string;
  why_this_matters?: string;
  establishes?: string | string[];
  does_not_establish?: string | string[];
  limitations?: string | string[];
  claims?: ClaimCitation[];
  claim_citations?: ClaimCitation[];
  why_this_answer?: {
    sources_used?: Array<string | { doc_id?: string; document_type?: string }>;
    entities_considered?: Array<string | { id?: string; label?: string }>;
    relationship_paths?: string[];
  };
  evidence_coverage?: {
    claims?: number;
    supported?: number;
    unsupported?: number;
    total_claims?: number;
    supported_claims?: number;
    unsupported_claims?: number;
    inferred_claims?: number;
  };
  contradictions?: string[];
  temporal_analysis?: Record<string, unknown>;
  followup_questions?: string[];
}

function isProviderError(text: string | null | undefined): boolean {
  if (!text) return true;
  return (
    text.includes("AI reasoning is unavailable") ||
    text.includes("APIStatusError") ||
    text.includes("configured provider call failed") ||
    text.includes("CRIMELINK_AI_REASONING_API_KEY") ||
    text.includes("no_api_key_for_role")
  );
}

function renderMarkdown(text: string | string[] | null | undefined, onCitationClick?: (id: string) => void) {
  if (!text) return null;
  const raw = Array.isArray(text) ? text.map((t) => `- ${t}`).join("\n") : text;
  const lines = raw.split("\n");
  return (
    <div className="case-rag-markdown-flow">
      {lines.map((line, lineIdx) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={lineIdx} style={{ height: "6px" }} />;
        }
        const isBullet = trimmed.startsWith("- ") || trimmed.startsWith("* ");
        const content = isBullet ? trimmed.slice(2) : line;
        const parts = content.split(/(\*\*[^*]+\*\*|\[[A-Za-z0-9_-]+\])/g);
        const rendered = parts.map((part, i) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return (
              <strong key={i} style={{ color: "var(--cl-ink)", fontWeight: 700 }}>
                {part.slice(2, -2)}
              </strong>
            );
          }
          if (part.startsWith("[") && part.endsWith("]")) {
            const inner = part.slice(1, -1);
            if (/^[A-Za-z0-9_-]{2,}$/.test(inner)) {
              return (
                <button
                  key={i}
                  type="button"
                  className="claim-inline-citation"
                  onClick={() => onCitationClick?.(inner)}
                  title={`Inspect ${inner} in Evidence Drawer`}
                >
                  [{inner}]
                </button>
              );
            }
          }
          return part;
        });

        if (isBullet) {
          return (
            <div key={lineIdx} style={{ display: "flex", gap: "6px", marginBottom: "4px" }}>
              <span style={{ color: "var(--cl-primary, #2563eb)", userSelect: "none" }}>•</span>
              <div style={{ flex: 1 }}>{rendered}</div>
            </div>
          );
        }
        return (
          <p key={lineIdx} style={{ margin: "0 0 6px 0" }}>
            {rendered}
          </p>
        );
      })}
    </div>
  );
}

function isGenericBoilerplate(text: string | string[] | null | undefined): boolean {
  if (!text) return true;
  const s = (Array.isArray(text) ? text.join(" ") : text).toLowerCase();
  if (s.length < 30) return true;
  return (
    s.includes("this intelligence establishes documented connections") ||
    s.includes("authoritative case verification requires") ||
    s.includes("operational timelines from verified platform evidence")
  );
}

function isBoilerplateList(items: string | string[] | null | undefined): boolean {
  if (!items) return true;
  const arr = Array.isArray(items) ? items : [items];
  if (arr.length === 0) return true;
  const joined = arr.join(" ").toLowerCase();
  // Hide the old stock disclaimer unless it's informative for THIS answer
  if (
    arr.length <= 2 &&
    (joined.includes("do not by themselves determine guilt") ||
      joined.includes("do not by themselves establish criminal intent")) &&
    joined.length < 200
  ) {
    return true;
  }
  if (
    arr.length === 1 &&
    joined.includes("constrained strictly to currently indexed case records")
  ) {
    return true;
  }
  return false;
}

function isEstablishesBoilerplate(items: string | string[] | null | undefined): boolean {
  if (!items) return true;
  const arr = Array.isArray(items) ? items : [items];
  if (arr.length === 0) return true;
  // If the establishes list appears to just repeat counts like "12 evidence records", hide it
  const joined = arr.join(" ").toLowerCase();
  if (arr.length <= 2 && /\d+\s+(evidence|verified|operational|individual)/.test(joined) && joined.length < 200) {
    return true;
  }
  return false;
}

function getStatusBadge(status: string) {
  const norm = (status || "").toUpperCase();
  if (norm.includes("FACT")) {
    return <span className="claim-badge claim-badge-fact">✓ DOCUMENTED FACT</span>;
  }
  if (norm.includes("INFERENCE")) {
    return <span className="claim-badge claim-badge-inference">~ INFERENCE</span>;
  }
  return <span className="claim-badge claim-badge-unsupported">? UNVERIFIED</span>;
}

export default function CaseRagChat({ caseId }: { caseId: string }) {
  const [caseContext, setCaseContext] = useState<CaseContextSummary | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [finding, setFinding] = useState<FindingResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Array<{ role: string; content: string }>>([]);
  const [drawerEvidence, setDrawerEvidence] = useState<EvidenceDrawerData | null>(null);

  // The CASE INTELLIGENCE cells are derived from the authoritative context
  // only — never from the assistant's answer text.
  const metrics = useMemo(() => deriveCaseIntelligenceMetrics(caseContext), [caseContext]);
  const metricsAvailableRef = useRef(false);
  metricsAvailableRef.current = metrics.available;

  const loadCaseContext = useCallback(async () => {
    try {
      const data = await api<CaseContextSummary>(`/ai/cases/${encodeURIComponent(caseId)}/context`);
      setCaseContext(data);
    } catch {
      // The authoritative context is unavailable. Keep the panel usable with
      // the case identity, but do not fabricate metrics: a header reading
      // "0 entities · 0 relationships · N/A" for a populated case is a false
      // statement about the evidence, not a fallback.
      try {
        const caseData = await api<{ case_number?: string; title?: string }>(
          `/cases/${encodeURIComponent(caseId)}`
        );
        setCaseContext(degradedCaseContext(caseId, caseData));
      } catch {
        // Leave as null if entirely unreachable
      }
    }
  }, [caseId]);

  useEffect(() => {
    void loadCaseContext();
    // Reset state on case change
    setAnswer(null);
    setFinding(null);
    setError(null);
    setHistory([]);
    setDrawerEvidence(null);
  }, [caseId, loadCaseContext]);

  // A successful answer proves the case context is reachable again; if the
  // header is still on its degraded state, re-request the authoritative
  // metrics so the two can never disagree for the rest of the session.
  const rehydrateMetricsIfDegraded = useCallback(() => {
    if (!metricsAvailableRef.current) void loadCaseContext();
  }, [loadCaseContext]);

  function getNetworkFallback(text: string): { answer: string; finding: FindingResult } {
    const caseNum = caseContext?.case_number || caseId;
    const direct = `Unable to connect to the CrimeLink investigation assistant backend for Case ${caseNum}. Please check network connectivity and ensure the API server is running.`;
    const fallbackFinding: FindingResult = {
      title: `Service Notification — ${caseNum}`,
      summary: direct,
      direct_answer: direct,
      why_this_matters: `Investigation queries require an active connection to the CrimeLink backend and case records database.`,
      limitations: `Client network request failed. No case data could be loaded.`,
      evidence_coverage: { total_claims: 0, supported_claims: 0, claims: 0, supported: 0, unsupported: 0 },
      followup_questions: caseContext?.suggested_questions?.slice(0, 3) || [],
    };
    return { answer: direct, finding: fallbackFinding };
  }

  async function ask(queryText?: string) {
    const text = (queryText ?? question).trim();
    if (!text || busy) return;
    setBusy(true);
    setAnswer(null);
    setFinding(null);
    setError(null);
    setPhase("started");
    let settled = false;

    const currentHistory = history.slice(-6);

    const plain = async () => {
      try {
        const result = await api<any>(`/ai/cases/${encodeURIComponent(caseId)}/ask`, {
          method: "POST",
          body: JSON.stringify({ question: text, history: currentHistory }),
        });
        settled = true;
        const resFinding = (result?.finding || {}) as FindingResult;
        const summary = resFinding?.direct_answer || resFinding?.summary || result?.summary;

        if (summary && !isProviderError(summary)) {
          setFinding(resFinding);
          setAnswer(summary);
          setHistory((prev) => [
            ...prev,
            { role: "user", content: text },
            { role: "assistant", content: summary },
          ]);
          rehydrateMetricsIfDegraded();
        } else {
          setError(result?.error || "Investigation service was unable to analyze this inquiry.");
        }
      } catch (err) {
        const fallback = getNetworkFallback(text);
        setAnswer(fallback.answer);
        setFinding(fallback.finding);
      } finally {
        setBusy(false);
        setPhase(null);
      }
    };

    try {
      await askCaseStream(
        caseId,
        text,
        {
          onAck: () => setPhase("started"),
          onStage: (event) => setPhase(String(event.stage ?? "retrieving")),
          onDelta: (chunk) => {
            setAnswer((prev) => (prev ? prev + chunk : chunk));
          },
          onDone: async (result) => {
            settled = true;
            const payload = result as any;
            const resFinding = (payload?.finding || {}) as FindingResult;
            const summary = resFinding?.direct_answer || resFinding?.summary || payload?.summary;

            if (summary && !isProviderError(summary)) {
              setFinding(resFinding);
              setAnswer(summary);
              setHistory((prev) => [
                ...prev,
                { role: "user", content: text },
                { role: "assistant", content: summary },
              ]);
              rehydrateMetricsIfDegraded();
            } else {
              void plain();
            }
            setBusy(false);
            setPhase(null);
          },
          onError: async () => {
            settled = true;
            void plain();
          },
          onFallback: () => {
            if (!settled) void plain();
          },
        },
        { history: currentHistory }
      );
    } catch {
      if (!settled) await plain();
    }
  }

  return (
    <section className="panel case-rag-chat" aria-label="Case evidence assistant">
      <div className="section-header">
        <div>
          <h2>CASE EVIDENCE ASSISTANT</h2>
          <p className="hint">
            RAG-grounded to this case only. Factual claims link directly to stored evidence records.
          </p>
        </div>
        <span className="badge badge-ok">RAG · CASE-SCOPED</span>
      </div>

      {/* Dynamic Authoritative Case Context Block */}
      {caseContext && (
        <div className="case-ai-context-card" aria-label="Authoritative Case Context">
          <div className="case-ai-context-top">
            <span className="case-ai-kicker">CASE INTELLIGENCE</span>
            <div className="case-ai-title">
              <strong>{caseContext.case_number}</strong> · {caseContext.title}
            </div>
          </div>
          {metrics.available ? (
            <div className="case-ai-stat-grid">
              <div className="case-ai-stat-item">
                <span className="case-ai-stat-label">Evidence</span>
                <span className="case-ai-stat-val">{metrics.evidenceLabel}</span>
              </div>
              <div className="case-ai-stat-item">
                <span className="case-ai-stat-label">Entities</span>
                <span className="case-ai-stat-val">{metrics.entitiesLabel}</span>
              </div>
              <div className="case-ai-stat-item">
                <span className="case-ai-stat-label">Relationships</span>
                <span className="case-ai-stat-val">{metrics.relationshipsLabel}</span>
              </div>
              <div className="case-ai-stat-item">
                <span className="case-ai-stat-label">Timeline</span>
                <span className="case-ai-stat-val">
                  First recorded: {metrics.firstRecorded}
                  <br />
                  Latest recorded: {metrics.latestRecorded}
                </span>
              </div>
            </div>
          ) : (
            <div className="case-ai-stat-grid" role="status">
              <div className="case-ai-stat-item">
                <span className="case-ai-stat-label">Case intelligence</span>
                <span className="case-ai-stat-val">
                  Metrics unavailable — the case context service did not respond.{" "}
                  <button
                    type="button"
                    className="claim-inline-citation"
                    onClick={() => void loadCaseContext()}
                    disabled={busy}
                  >
                    Retry
                  </button>
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Dynamic Case-Tailored Suggested Questions */}
      {caseContext?.suggested_questions && caseContext.suggested_questions.length > 0 && !finding && (
        <div className="case-ai-suggestions">
          <span className="case-ai-suggestions-label">Suggested Inquiries:</span>
          <div className="case-ai-chips">
            {caseContext.suggested_questions.map((q, i) => (
              <button
                key={i}
                type="button"
                className="case-ai-chip"
                disabled={busy}
                onClick={() => {
                  setQuestion(q);
                  void ask(q);
                }}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Inquiry Form */}
      <form
        className="form-row"
        onSubmit={(event) => {
          event.preventDefault();
          void ask();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask: What connects these people? Which evidence supports it? What happened first?"
          aria-label="Ask a question about this case"
          className="case-rag-input"
          style={{ flex: 1, minWidth: 280 }}
        />
        <button className="cl-btn cl-btn-primary" type="submit" disabled={busy || !question.trim()}>
          {busy ? "Analyzing…" : "Ask Assistant"}
        </button>
      </form>

      {busy && (
        <div className="ai-progress" role="status" style={{ marginTop: "12px" }}>
          <span className="spinner" aria-hidden="true" />
          <span>{PHASES[phase ?? "started"] ?? "Searching case evidence…"}</span>
        </div>
      )}

      {error && (
        <div className="banner banner-warn" role="alert" style={{ marginTop: "12px" }}>
          {error}
        </div>
      )}

      {/* Response Card */}
      {(finding || answer) && (() => {
        const activeClaims = (finding?.claim_citations && finding.claim_citations.length > 0)
          ? finding.claim_citations
          : (finding?.claims && finding.claims.length > 0 ? finding.claims : []);

        const coverageTotal = finding?.evidence_coverage?.total_claims ?? finding?.evidence_coverage?.claims ?? activeClaims.length;
        const coverageSupported = finding?.evidence_coverage?.supported_claims ?? finding?.evidence_coverage?.supported ?? (
          activeClaims.filter((c) => {
            const s = (c.support_status || c.support_level || "").toUpperCase();
            return s.includes("FACT") || s.includes("SUPPORTED");
          }).length
        );
        const coverageUnsupported = finding?.evidence_coverage?.unsupported_claims ?? finding?.evidence_coverage?.unsupported ?? (
          activeClaims.filter((c) => {
            const s = (c.support_status || c.support_level || "").toUpperCase();
            return s.includes("UNSUPPORTED");
          }).length
        );

        const answerStr = finding?.direct_answer || answer || "";
        const hasCitations = /\[[A-Za-z0-9_-]{2,}\]/.test(answerStr) || activeClaims.some((c) => Boolean(c.evidence_id || (c.evidence_refs && c.evidence_refs.length > 0)));

        return (
          <div className="case-rag-response-card" style={{ marginTop: "12px" }}>
            <div className="case-rag-response-header">
              <span className="badge badge-ok">CASE INTELLIGENCE</span>
              <span className="case-rag-response-badge">Grounded</span>
              {coverageTotal > 0 && (
                <span className="claim-corroboration-badge" style={{ marginLeft: "auto" }}>
                  Coverage: {coverageSupported}/{coverageTotal} supported
                </span>
              )}
            </div>

            <div className="case-rag-structured-box">
              {/* NATURAL ANSWER — the primary content */}
              <div className="case-rag-section">
                <div className="case-rag-section-body case-rag-natural-answer">
                  {renderMarkdown(finding?.direct_answer || answer || "", (id) =>
                    setDrawerEvidence({ id })
                  )}
                </div>
              </div>

              {/* EVIDENCE RECORD CLAIMS — collapsible list of sourced claims */}
              {activeClaims.length > 0 && (
                <details className="case-rag-section case-rag-collapsible">
                  <summary className="case-rag-section-title">
                    ▶ EVIDENCE ({activeClaims.length} sourced claim{activeClaims.length === 1 ? "" : "s"})
                  </summary>
                  <div className="case-rag-claims-list">
                    {activeClaims.map((c, i) => {
                      const claimText = c.claim_text || c.claim || "";
                      const evidId = c.evidence_id || (c.evidence_refs && c.evidence_refs[0]) || "";
                      const status = c.support_status || c.support_level || "DOCUMENTED FACT";
                      // Skip claims whose text is a near-duplicate of the main answer
                      if (claimText && (finding?.direct_answer || answer || "").includes(claimText.slice(0, 80)) && evidId) {
                        return null;
                      }
                      return (
                        <div key={i} className="case-rag-claim-card">
                          <div className="case-rag-claim-header">
                            {getStatusBadge(status)}
                            {c.corroboration && (
                              <span className="claim-corroboration-badge">★ {c.corroboration}</span>
                            )}
                            {evidId && (
                              <button
                                type="button"
                                className="claim-citation-btn"
                                onClick={() => setDrawerEvidence({ id: evidId })}
                                title={`Inspect ${evidId} in Evidence Drawer`}
                              >
                                [{evidId}]
                              </button>
                            )}
                          </div>
                          <div className="case-rag-section-body">
                            {renderMarkdown(claimText, (id) => setDrawerEvidence({ id }))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </details>
              )}

              {/* WHY THIS MATTERS — only when genuinely informative */}
              {finding?.why_this_matters && !isGenericBoilerplate(finding.why_this_matters) && (
                <div className="case-rag-section">
                  <h4 className="case-rag-section-title">WHY THIS MATTERS</h4>
                  <div className="case-rag-section-body">
                    {renderMarkdown(finding.why_this_matters, (id) => setDrawerEvidence({ id }))}
                  </div>
                </div>
              )}

              {/* WHAT THE EVIDENCE ESTABLISHES — only when explicitly present */}
              {finding?.establishes && Array.isArray(finding.establishes) && finding.establishes.length > 0 && !isEstablishesBoilerplate(finding.establishes) && (
                <div className="case-rag-section case-rag-establishes">
                  <h4 className="case-rag-section-title">KEY FINDINGS</h4>
                  <div className="case-rag-section-body">
                    {renderMarkdown(finding.establishes, (id) => setDrawerEvidence({ id }))}
                  </div>
                </div>
              )}

              {/* WHAT THE EVIDENCE DOES NOT ESTABLISH — only when non-generic */}
              {finding?.does_not_establish && Array.isArray(finding.does_not_establish) && finding.does_not_establish.length > 0 && !isBoilerplateList(finding.does_not_establish) && (
                <div className="case-rag-section case-rag-not-establishes">
                  <h4 className="case-rag-section-title">WHAT THE EVIDENCE DOES NOT ESTABLISH</h4>
                  <div className="case-rag-section-body">
                    {renderMarkdown(finding.does_not_establish, (id) => setDrawerEvidence({ id }))}
                  </div>
                </div>
              )}

              {/* LIMITATIONS */}
              {finding?.limitations && Array.isArray(finding.limitations) && finding.limitations.length > 0 && !isBoilerplateList(finding.limitations) && (
                <div className="case-rag-section case-rag-limitations">
                  <h4 className="case-rag-section-title">NOTES & LIMITATIONS</h4>
                  <div className="case-rag-section-body">
                    {renderMarkdown(finding.limitations, (id) => setDrawerEvidence({ id }))}
                  </div>
                </div>
              )}

              {/* WHY THIS ANSWER (Provenance & Coverage) */}
              {finding?.why_this_answer &&
                ((finding.why_this_answer.sources_used && finding.why_this_answer.sources_used.length > 0) ||
                  (finding.why_this_answer.entities_considered && finding.why_this_answer.entities_considered.length > 0) ||
                  (finding.why_this_answer.relationship_paths && finding.why_this_answer.relationship_paths.length > 0)) ? (
                <details className="case-rag-provenance">
                  <summary className="case-rag-provenance-summary">
                    ▶ WHY THIS ANSWER (Evidence Provenance & Coverage)
                  </summary>
                  <div className="case-rag-provenance-body">
                    {coverageTotal > 0 && (
                      <div className="case-rag-coverage-bar">
                        <span>
                          Claims: <strong>{coverageTotal}</strong>
                        </span>
                        <span>
                          Supported:{" "}
                          <strong style={{ color: "#059669" }}>
                            {coverageSupported}
                          </strong>
                        </span>
                        <span>
                          Unsupported:{" "}
                          <strong
                            style={{
                              color: coverageUnsupported > 0 ? "#e11d48" : "#64748b",
                            }}
                          >
                            {coverageUnsupported}
                          </strong>
                        </span>
                      </div>
                    )}

                    {finding.why_this_answer.sources_used &&
                      finding.why_this_answer.sources_used.length > 0 && (
                        <div>
                          <strong>Sources used:</strong>
                          <pre className="provenance-tree">
                            {finding.why_this_answer.sources_used
                              .map((s, idx, arr) => {
                                const text = typeof s === "string" ? s : `${s.document_type || "DOCUMENT"} [${s.doc_id}]`;
                                return `${idx === arr.length - 1 ? "└── " : "├── "}${text}`;
                              })
                              .join("\n")}
                          </pre>
                        </div>
                      )}

                    {finding.why_this_answer.entities_considered &&
                      finding.why_this_answer.entities_considered.length > 0 && (
                        <div>
                          <strong>Entities considered:</strong>
                          <div className="case-ai-chips" style={{ marginTop: "4px" }}>
                            {finding.why_this_answer.entities_considered.map((e, idx) => {
                              const text = typeof e === "string" ? e : (e.label || e.id || "Entity");
                              return (
                                <span key={idx} className="case-ai-chip" style={{ cursor: "default" }}>
                                  {text}
                                </span>
                              );
                            })}
                          </div>
                        </div>
                      )}

                    {finding.why_this_answer.relationship_paths &&
                      finding.why_this_answer.relationship_paths.length > 0 && (
                        <div>
                          <strong>Relationship paths:</strong>
                          <pre className="provenance-tree">
                            {finding.why_this_answer.relationship_paths.join("\n")}
                          </pre>
                        </div>
                      )}
                  </div>
                </details>
              ) : null}

              {/* SUGGESTED FOLLOW-UPS */}
              {finding?.followup_questions && finding.followup_questions.length > 0 && (
                <div className="case-ai-suggestions" style={{ marginTop: "10px" }}>
                  <span className="case-ai-suggestions-label">Suggested Follow-ups:</span>
                  <div className="case-ai-chips">
                    {finding.followup_questions.map((fq, i) => (
                      <button
                        key={i}
                        type="button"
                        className="case-ai-chip case-ai-chip-followup"
                        disabled={busy}
                        onClick={() => {
                          setQuestion(fq);
                          void ask(fq);
                        }}
                      >
                        → {fq}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="case-rag-response-footer">
              <span className="material-symbols-outlined" style={{ fontSize: "14px" }}>
                verified_user
              </span>
              <span>
                {hasCitations
                  ? "Click any [DOC-ID] citation to view verified provenance in the Evidence Drawer."
                  : "All statements are strictly derived from verified case-scoped records and graph context."}
              </span>
            </div>
          </div>
        );
      })()}

      {/* Integrated Evidence Drawer */}
      {drawerEvidence && (
        <EvidenceDrawer
          open={true}
          data={drawerEvidence}
          onClose={() => setDrawerEvidence(null)}
        />
      )}
    </section>
  );
}
