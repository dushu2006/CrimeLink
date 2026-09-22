/**
 * Case AI chat transcript behavior — the helpers behind CaseRagChat.
 *
 * The task these pin down:
 *   - the transcript appends and serialises itself into the backend history;
 *   - source chips come from the backend presentation block only, and are
 *     absent for general-knowledge / unverified answers;
 *   - suggested follow-ups are deduped and never echo the asked question;
 *   - provider-error blobs never surface as answer text.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const LIB = await import(
  new URL("../src/lib/caseAiChat.ts", import.meta.url).href
);

// ---------------------------------------------------------------------------
// answerText
// ---------------------------------------------------------------------------

test("answerText prefers the composed summary and never returns empty markup", () => {
  assert.equal(
    LIB.answerText({ summary: "Dinesh Malhotra's phone number is +919812345670. [CDR-001]", direct_answer: "x" }),
    "Dinesh Malhotra's phone number is +919812345670. [CDR-001]",
  );
  assert.equal(LIB.answerText({ direct_answer: "Only direct." }), "Only direct.");
  assert.equal(
    LIB.answerText({}),
    "The available evidence does not answer that question in this case.",
  );
  assert.equal(
    LIB.answerText(null),
    "The available evidence does not answer that question in this case.",
  );
});

// ---------------------------------------------------------------------------
// answerSources
// ---------------------------------------------------------------------------

test("answerSources returns only backend-attached presentation sources, deduped", () => {
  const finding = {
    presentation: {
      sources: [
        { doc_id: "CDR-001", filename: "cdr.csv", document_type: "CDR" },
        { doc_id: "CDR-001" },
        { doc_id: " " },
        { filename: "no-id.pdf" },
        { doc_id: "FIR-001", document_type: "FIR" },
      ],
    },
  };
  const sources = LIB.answerSources(finding);
  assert.deepEqual(
    sources.map((s) => s.doc_id),
    ["CDR-001", "FIR-001"],
  );
});

test("general-knowledge answers (no presentation) expose no sources", () => {
  assert.deepEqual(LIB.answerSources({ finding_type: "GENERAL_KNOWLEDGE", summary: "New Delhi." }), []);
  assert.deepEqual(LIB.answerSources(null), []);
  assert.deepEqual(LIB.answerSources({ presentation: { sources: [] } }), []);
});

// ---------------------------------------------------------------------------
// followupsFor
// ---------------------------------------------------------------------------

test("followupsFor dedupes, trims, caps at three and drops the asked question", () => {
  const finding = {
    followup_questions: [
      "Tell me everything about Dinesh Malhotra.",
      "tell me everything about dinesh malhotra.",
      "  ",
      "What files are attached?",
      "What is the FIR number?",
      "A fourth suggestion?",
    ],
  };
  assert.deepEqual(LIB.followupsFor(finding), [
    "Tell me everything about Dinesh Malhotra.",
    "What files are attached?",
    "What is the FIR number?",
  ]);
  assert.deepEqual(
    LIB.followupsFor({ followup_questions: ["What is the FIR number?"] }, "What is the FIR number?"),
    [],
  );
});

// ---------------------------------------------------------------------------
// buildHistory
// ---------------------------------------------------------------------------

test("buildHistory serialises completed turns only and caps the window", () => {
  const messages = [];
  for (let i = 0; i < 10; i += 1) {
    messages.push({ role: "user", text: `Question ${i}`, status: "done" });
    messages.push({ role: "assistant", text: `Answer ${i}`, status: "done" });
  }
  messages.push({ role: "assistant", text: "partial…", status: "streaming" });
  const history = LIB.buildHistory(messages, 3);
  assert.equal(history.length, 6, "three exchanges");
  assert.equal(history[history.length - 1].content, "Answer 9");
  assert.equal(history[0].content, "Question 7");
  assert.ok(history.every((t) => t.role === "user" || t.role === "assistant"));
});

test("buildHistory skips empty and streaming content", () => {
  const history = LIB.buildHistory([
    { role: "user", text: "", status: "done" },
    { role: "assistant", text: "   ", status: "done" },
    { role: "user", text: "Real question?", status: "done" },
    { role: "assistant", text: "Real answer.", status: "done" },
  ]);
  assert.equal(history.length, 2);
  assert.equal(history[0].role, "user");
});

// ---------------------------------------------------------------------------
// isProviderErrorText
// ---------------------------------------------------------------------------

test("provider error blobs are recognised so they never render as answers", () => {
  assert.equal(LIB.isProviderErrorText("openai.APIStatusError: 429 rate limit"), true);
  assert.equal(LIB.isProviderErrorText("configured provider call failed"), true);
  assert.equal(LIB.isProviderErrorText("The answer is grounded in the records."), false);
  assert.equal(LIB.isProviderErrorText(null), true);
});
