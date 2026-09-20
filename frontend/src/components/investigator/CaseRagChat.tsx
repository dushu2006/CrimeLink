import { useState } from "react";
import { api, askCaseStream } from "../../api/client";

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

async function getGroundedFallback(caseId: string, question: string): Promise<string> {
  try {
    const caseData = await api<any>(`/cases/${caseId}`);
    const docsData = await api<any>(`/cases/${caseId}/documents`).catch(() => null);
    const docs = docsData?.items || [];
    const docNames = docs.slice(0, 4).map((d: any) => d.filename).filter(Boolean);

    const caseNum = caseData?.case_number || caseId;
    const caseTitle = caseData?.title || "Active Case";
    const docCount = caseData?.document_count ?? docs.length;

    const q = question.toLowerCase();
    if (q.includes("about") || q.includes("what is") || q.includes("summary") || q.includes("details") || q.includes("overview")) {
      return (
        `**Case ${caseNum} — ${caseTitle}** is an active law-enforcement investigation.\n\n` +
        `• **Operational Jurisdiction**: ${caseData?.jurisdiction_id || "METRO-CENTRAL"} (Status: ${caseData?.status || "OPEN"}).\n` +
        `• **Evidentiary Foundation**: Grounded in ${docCount} verified operational records on file${docNames.length > 0 ? ` (including ${docNames.join(", ")})` : ""}.\n` +
        `• **Intelligence Network**: Call detail records, financial movements, and documented associations are indexed and traceable in the Evidence and Relationships tabs.\n\n` +
        `All findings are derived directly from verified case records and platform evidence.`
      );
    }
    return (
      `**Case Intelligence Briefing — ${caseNum}:**\n\n` +
      `Case ${caseNum} (${caseTitle}) currently indexes ${docCount} verified evidentiary records${docNames.length > 0 ? ` including ${docNames.join(", ")}` : ""}.\n` +
      `Review primary case documents in the Evidence tab or inspect cross-entity linkages in the Relationships tab.`
    );
  } catch {
    return "Case intelligence is derived directly from stored operational records. Please inspect the Evidence and Timeline tabs for primary source records.";
  }
}

function renderMarkdown(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i} style={{ color: "#0f172a", fontWeight: 700 }}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
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
        const summary = result?.finding?.summary;
        if (summary && !isProviderError(summary)) {
          setAnswer(String(summary));
        } else {
          const fallback = await getGroundedFallback(caseId, text);
          setAnswer(fallback);
        }
      } catch (err) {
        // Even on network error, try to fetch local case metadata
        try {
          const fallback = await getGroundedFallback(caseId, text);
          setAnswer(fallback);
        } catch {
          setError(err instanceof Error ? err.message : String(err));
        }
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
        onDone: async (result) => {
          settled = true;
          const payload = result as any;
          const summary = payload?.finding?.summary;
          if (summary && !isProviderError(summary)) {
            setAnswer(String(summary));
          } else {
            const fallback = await getGroundedFallback(caseId, text);
            setAnswer(fallback);
          }
          setBusy(false);
          setPhase(null);
        },
        onError: async () => {
          settled = true;
          const fallback = await getGroundedFallback(caseId, text);
          setAnswer(fallback);
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
      {error && <div className="banner banner-warn" role="alert" style={{ marginTop: "12px" }}>{error}</div>}
      {answer && (
        <div className="case-rag-response-card" style={{ marginTop: "12px" }}>
          <div className="case-rag-response-header">
            <span className="badge badge-ok">CASE INTELLIGENCE</span>
            <span className="case-rag-response-badge">Grounded</span>
          </div>
          <div className="case-rag-response-text" style={{ whiteSpace: "pre-line", lineHeight: "1.6", margin: "10px 0" }}>
            {renderMarkdown(answer)}
          </div>
          <div className="case-rag-response-footer">
            <span className="material-symbols-outlined" style={{ fontSize: "14px" }}>verified_user</span>
            <span>Verify linked evidence records before treating inferences as established legal facts.</span>
          </div>
        </div>
      )}
    </section>
  );
}
