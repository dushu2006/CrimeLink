import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { api, askCaseStream } from "../../api/client";
import { EvidenceDrawer, type EvidenceDrawerData } from "./EvidenceDrawer";
import {
  type CaseContextSummary,
  degradedCaseContext,
  deriveCaseIntelligenceMetrics,
} from "../../lib/caseIntelligence";
import {
  type ChatFinding,
  type ChatMessage,
  answerText,
  answerSources,
  followupsFor,
  buildHistory,
  isProviderErrorText,
} from "../../lib/caseAiChat";

const PHASES: Record<string, string> = {
  started: "Looking into the case…",
  retrieving: "Checking the case records…",
  generating: "Writing the answer…",
  validating: "Checking the references…",
  fast_path: "Answering from the case context…",
};

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

export default function CaseRagChat({ caseId }: { caseId: string }) {
  const [caseContext, setCaseContext] = useState<CaseContextSummary | null>(null);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [phase, setPhase] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drawerEvidence, setDrawerEvidence] = useState<EvidenceDrawerData | null>(null);
  const nextId = useRef(1);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

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
    // The transcript is deliberately session-only: switching cases starts a
    // fresh conversation, and history never leaves this component's state
    // (page refresh / sign-out resets it).
    setMessages([]);
    setDrawerEvidence(null);
  }, [caseId, loadCaseContext]);

  // Keep the newest exchange in view as the transcript grows.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  // A successful answer proves the case context is reachable again; if the
  // header is still on its degraded state, re-request the authoritative
  // metrics so the two can never disagree for the rest of the session.
  const rehydrateMetricsIfDegraded = useCallback(() => {
    if (!metricsAvailableRef.current) void loadCaseContext();
  }, [loadCaseContext]);

  const lastAssistantId = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  })();

  function patchAssistant(id: number, patch: Partial<ChatMessage>) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }

  async function ask(queryText?: string) {
    const text = (queryText ?? question).trim();
    if (!text || busy) return;
    setBusy(true);
    setPhase("started");

    const history = buildHistory(messages, 6);
    const userMsg: ChatMessage = { id: nextId.current++, role: "user", text: String(text), status: "done" };
    const assistantId = nextId.current++;
    const placeholder: ChatMessage = { id: assistantId, role: "assistant", text: "", status: "streaming" };
    setMessages((prev) => [...prev, userMsg, placeholder]);
    setQuestion("");

    const applyResponse = (payload: Record<string, any> | null, streamedText: string) => {
      const finding = (payload?.finding ?? null) as ChatFinding | null;
      let finalText = answerText(finding).trim();
      if (!finalText && streamedText.trim() && !isProviderErrorText(streamedText)) {
        finalText = streamedText.trim();
      }
      const context = (payload?.context ?? {}) as Record<string, any>;
      const rewritten = context?.followup_resolution?.rewritten;
      if (!finalText || isProviderErrorText(finalText)) {
        patchAssistant(assistantId, {
          status: "error",
          text: "I couldn't complete the analysis just then — the service did not respond. Your question is still in the transcript; ask again in a moment.",
        });
        return false;
      }
      patchAssistant(assistantId, {
        status: "done",
        text: finalText,
        finding,
        resolvedQuestion: typeof rewritten === "string" ? rewritten : null,
      });
      rehydrateMetricsIfDegraded();
      return true;
    };

    const failAssistant = (message: string) => {
      patchAssistant(assistantId, { status: "error", text: message });
    };

    const plain = async (): Promise<boolean> => {
      try {
        const result = await api<any>(`/ai/cases/${encodeURIComponent(caseId)}/ask`, {
          method: "POST",
          body: JSON.stringify({ question: text, history }),
        });
        return applyResponse(result, "");
      } catch {
        const caseNum = caseContext?.case_number || caseId;
        failAssistant(
          `I couldn't reach the CrimeLink backend for case ${caseNum}. Check that the API server is running and ask again — the conversation above is intact.`
        );
        return false;
      } finally {
        setBusy(false);
        setPhase(null);
      }
    };

    let settled = false;
    let streamedText = "";
    let receivedDone: Record<string, any> | null = null;
    try {
      await askCaseStream(
        caseId,
        String(text),
        {
          onAck: () => setPhase("started"),
          onStage: (event) => setPhase(String(event.stage ?? "retrieving")),
          onDelta: (chunk) => {
            streamedText += chunk;
            patchAssistant(assistantId, { text: streamedText });
          },
          onDone: async (result) => {
            settled = true;
            receivedDone = result as Record<string, any>;
            const ok = applyResponse(receivedDone, streamedText);
            if (!ok) void plain();
            else {
              setBusy(false);
              setPhase(null);
            }
          },
          onError: async () => {
            settled = true;
            void plain();
          },
          onFallback: () => {
            if (settled) return;
            settled = true;
            void plain();
          },
        },
        { history }
      );
    } catch {
      if (!settled) await plain();
      return;
    }
    // The stream resolving without a `done` event (e.g. the connection was
    // acknowledged then dropped mid-answer) is not a settled turn either —
    // recover through the plain POST so the placeholder can never strand.
    if (!settled) await plain();
  }

  const openCitation = useCallback((id: string) => setDrawerEvidence({ id }), []);

  return (
    <section className="panel case-rag-chat" aria-label="Case evidence assistant">
      <div className="section-header">
        <div>
          <h2>CASE EVIDENCE ASSISTANT</h2>
          <p className="hint">
            Answers are drawn from this case's records only. Ask follow-ups the way you would ask a person.
          </p>
        </div>
        <span className="badge badge-ok">CASE-SCOPED</span>
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
      {caseContext?.suggested_questions && caseContext.suggested_questions.length > 0 && messages.length === 0 && (
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

      {/* Transcript — a continuous conversation; new turns append below. */}
      <div
        className="case-ai-transcript"
        ref={transcriptRef}
        aria-live="polite"
        aria-label="Conversation with the case assistant"
      >
        {messages.length === 0 && !busy && (
          <div className="case-ai-empty">
            Ask anything about this case — a straight question gets a straight
            answer; deeper questions get a deeper answer with their sources.
          </div>
        )}

        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div key={m.id} className="case-ai-turn case-ai-turn-user">
                <div className="case-ai-bubble case-ai-bubble-user">{m.text}</div>
              </div>
            );
          }
          const finding = m.finding ?? null;
          const sources = m.status === "done" ? answerSources(finding) : [];
          const followups =
            m.status === "done" && m.id === lastAssistantId
              ? followupsFor(finding)
              : [];
          const showRewritten =
            m.resolvedQuestion && m.resolvedQuestion.replace(/\s+/g, " ").trim() !== "";
          return (
            <div key={m.id} className="case-ai-turn case-ai-turn-assistant">
              <div
                className={
                  "case-ai-bubble case-ai-bubble-assistant" +
                  (m.status === "error" ? " case-ai-bubble-error" : "")
                }
              >
                {showRewritten && (
                  <div className="case-ai-understood">
                    Understood as: <em>{m.resolvedQuestion}</em>
                  </div>
                )}
                {m.status === "streaming" && !m.text ? (
                  <div className="case-ai-typing" aria-label="Assistant is typing">
                    <span className="case-ai-dot" />
                    <span className="case-ai-dot" />
                    <span className="case-ai-dot" />
                  </div>
                ) : (
                  renderMarkdown(m.text, openCitation)
                )}
                {sources.length > 0 && (
                  <div className="case-ai-source-row" aria-label="Sources">
                    <span className="case-ai-source-label">Sources</span>
                    {sources.map((src) => (
                      <button
                        key={src.doc_id}
                        type="button"
                        className="case-ai-source-chip"
                        onClick={() => openCitation(src.doc_id)}
                        title={src.filename || src.doc_id}
                      >
                        {src.document_type ? `${src.document_type} · ` : ""}{src.doc_id}
                      </button>
                    ))}
                  </div>
                )}
                {followups.length > 0 && (
                  <div className="case-ai-chips case-ai-followup-row">
                    {followups.map((fq, i) => (
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
                )}
              </div>
            </div>
          );
        })}
      </div>

      {busy && (
        <div className="ai-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          <span>{PHASES[phase ?? "started"] ?? "Looking into the case…"}</span>
        </div>
      )}

      {/* Inquiry Form */}
      <form
        className="form-row case-ai-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void ask();
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about people, links, timelines, case data, or files in this case…"
          aria-label="Ask a question about this case"
          className="case-rag-input"
          style={{ flex: 1, minWidth: 280 }}
        />
        <button className="cl-btn cl-btn-primary" type="submit" disabled={busy || !question.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
        {messages.length > 0 && (
          <button
            className="cl-btn"
            type="button"
            disabled={busy}
            onClick={() => setMessages([])}
            title="Clear this conversation (history is session-only)"
          >
            New chat
          </button>
        )}
      </form>

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
