import { useState } from "react";
import { api, askCaseStream } from "../api/client";

const PHASES: Record<string, string> = {
  started: "Retrieving case context…",
  retrieving: "Retrieving case evidence…",
  generating: "Generating grounded answer…",
  validating: "Validating evidence references…",
  fast_path: "Answering from case context…",
};

function fallbackReason(reason: unknown): string {
  const value = String(reason ?? "");
  if (value.startsWith("no_api_key_for_role_")) {
    return "The configured AI provider is not available for this role.";
  }
  if (value.includes("timeout") || value.startsWith("invocation_failed:")) {
    return "The AI provider timed out. The case evidence remains available below.";
  }
  return value ? `AI unavailable: ${value}` : "AI is currently unavailable.";
}

export default function CaseRagChat({ caseId }: { caseId: string }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask() {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setAnswer(null);
    setError(null);
    setPhase("started");
    let settled = false;

    const plain = async () => {
      try {
        const result = await api<any>(`/ai/cases/${caseId}/ask`, {
          method: "POST",
          body: JSON.stringify({ question: text }),
        });
        settled = true;
        if (result?.available === false) {
          setError(fallbackReason(result.fallback_reason));
        } else {
          setAnswer(String(result?.finding?.summary ?? "No grounded finding was returned."));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        setPhase(null);
      }
    };

    try {
      await askCaseStream(caseId, text, {
        onAck: () => setPhase("started"),
        onStage: (event) => setPhase(String(event.stage ?? "retrieving")),
        onDelta: () => undefined,
        onDone: (result) => {
          settled = true;
          const payload = result as any;
          if (payload?.available === false) {
            setError(fallbackReason(payload.fallback_reason));
          } else {
            setAnswer(String(payload?.finding?.summary ?? "No grounded finding was returned."));
          }
          setBusy(false);
          setPhase(null);
        },
        onError: (event) => {
          settled = true;
          setError(String(event.message ?? "Unable to answer this question."));
          setBusy(false);
          setPhase(null);
        },
        onFallback: () => {
          if (!settled) void plain();
        },
      });
    } catch {
      if (!settled) await plain();
    }

    if (!settled) await plain();
  }

  return (
    <section className="panel case-rag-chat" aria-label="Case evidence assistant">
      <div className="section-header">
        <div>
          <h2>CASE EVIDENCE ASSISTANT</h2>
          <p className="hint">RAG-grounded to this case only. Answers are derived from stored case evidence and graph context.</p>
        </div>
        <span className="badge badge-ok">RAG · CASE-SCOPED</span>
      </div>
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
          style={{ flex: 1, minWidth: 280 }}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !question.trim()}>
          {busy ? "Searching…" : "Ask"}
        </button>
      </form>

      {busy && (
        <div className="ai-progress" role="status" style={{ marginTop: "var(--space-3)" }}>
          <span className="spinner" aria-hidden="true" />
          <span>{PHASES[phase ?? "started"] ?? "Searching case evidence…"}</span>
        </div>
      )}
      {error && <div className="banner banner-warn" role="alert" style={{ marginTop: "var(--space-3)" }}>{error}</div>}
      {answer && (
        <div className="ai-answer" style={{ marginTop: "var(--space-3)" }}>
          <div className="badge badge-ok" style={{ marginBottom: "8px" }}>GROUNDED ANSWER</div>
          <blockquote style={{ margin: 0 }}>{answer}</blockquote>
          <p className="hint" style={{ marginTop: "8px" }}>
            Verify the linked evidence records before treating any inference as established fact.
          </p>
        </div>
      )}
    </section>
  );
}
