/**
 * Case AI chat — transcript helpers (pure, no React).
 *
 * The chat is session-only: the transcript lives in component state, so a
 * page refresh or sign-out resets it.  Nothing here talks to storage and
 * nothing here fabricates source chips — `answerSources` only returns what
 * the backend's presentation block attached to the finding, which the
 * backend itself derived from validated claim citations intersected with
 * documents that exist in the case.
 */

export interface ChatSource {
  doc_id: string;
  filename?: string;
  document_type?: string;
}

export interface ChatFinding {
  summary?: string;
  direct_answer?: string;
  finding_type?: string;
  available?: boolean;
  claims?: Array<{
    claim?: string;
    evidence_refs?: string[];
    evidence_level?: string;
  }>;
  followup_questions?: string[];
  presentation?: {
    sources?: ChatSource[];
    intent?: string;
  };
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  /** Visible text. Grows while an assistant message streams. */
  text: string;
  finding?: ChatFinding | null;
  /** The standalone form the backend resolved this turn to (when it differs). */
  resolvedQuestion?: string | null;
  status: "streaming" | "done" | "error";
}

/** The text an assistant bubble shows once the answer is final. */
export function answerText(finding: ChatFinding | null | undefined): string {
  const summary = String(finding?.summary ?? finding?.direct_answer ?? "").trim();
  if (summary) return summary;
  return "The available evidence does not answer that question in this case.";
}

/**
 * Compact source chips for an answer.  The backend already suppresses
 * sources for general-knowledge / unverified answers and only attaches
 * document-backed refs, so the UI's only job is: show what arrived, when
 * anything did.  Answers whose text cites a document id but whose sources
 * list is empty (e.g. a finding built before presentation) still surface
 * nothing — chips are never invented client-side.
 */
export function answerSources(finding: ChatFinding | null | undefined): ChatSource[] {
  const sources = finding?.presentation?.sources;
  if (!Array.isArray(sources)) return [];
  const seen = new Set<string>();
  const out: ChatSource[] = [];
  for (const src of sources) {
    const id = String(src?.doc_id ?? "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ doc_id: id, filename: src?.filename, document_type: src?.document_type });
  }
  return out;
}

/** Suggested follow-ups under an assistant answer (deduped, capped). */
export function followupsFor(
  finding: ChatFinding | null | undefined,
  askedQuestion?: string,
): string[] {
  const raw = finding?.followup_questions;
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  const asked = (askedQuestion ?? "").trim().toLowerCase();
  for (const item of raw) {
    const q = String(item ?? "").trim();
    if (!q) continue;
    const key = q.toLowerCase();
    if (seen.has(key) || (asked && key === asked)) continue;
    seen.add(key);
    out.push(q);
    if (out.length >= 3) break;
  }
  return out;
}

/**
 * Serialise the transcript for the backend's `history` field.  Only
 * completed turns are sent — a message still streaming has no final text
 * and would teach the resolver a truncated answer.  Capped at the most
 * recent `maxTurns` exchanges so the payload stays small; the backend
 * resolver itself works from the same window.
 */
export function buildHistory(
  messages: ReadonlyArray<Pick<ChatMessage, "role" | "text" | "status">>,
  maxTurns = 6,
): Array<{ role: string; content: string }> {
  const turns: Array<{ role: string; content: string }> = [];
  for (const m of messages) {
    if (m.status !== "done") continue;
    const text = String(m.text ?? "").trim();
    if (!text) continue;
    turns.push({ role: m.role, content: text });
  }
  const maxMessages = Math.max(2, maxTurns * 2);
  return turns.slice(-maxMessages);
}

/**
 * Raw-provider errors must never surface as answer text — the chat shows
 * the honest fallback sentence instead (this mirrors the backend's own
 * guard so a regression cannot leak "APIStatusError" into the UI).
 */
export function isProviderErrorText(text: string | null | undefined): boolean {
  if (!text) return true;
  return (
    text.includes("APIStatusError") ||
    text.includes("configured provider call failed") ||
    text.includes("CRIMELINK_AI_REASONING_API_KEY") ||
    text.includes("no_api_key_for_role") ||
    text.includes("AI reasoning is unavailable")
  );
}
