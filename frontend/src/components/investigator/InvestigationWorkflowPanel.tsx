import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

interface Task {
  id: string;
  title: string;
  status: string;
  priority: string;
  due_at?: string | null;
}
interface Hypothesis {
  id: string;
  statement: string;
  status: string;
  confidence?: number | null;
  unknown_information?: string[];
}
interface Contradiction {
  id: string;
  subject_key: string;
  predicate: string;
  explanation: string;
  verification_steps: string[];
}

/**
 * Small, case-scoped workflow surface for the durable industry foundation.
 * It intentionally shows unknowns and verification steps next to work items;
 * it never renders a legal recommendation or an inferred criminal label.
 */
export default function InvestigationWorkflowPanel({ caseId }: { caseId: string }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [contradictions, setContradictions] = useState<Contradiction[]>([]);
  const [unknowns, setUnknowns] = useState<any[]>([]);
  const [title, setTitle] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [taskResult, hypothesisResult, contradictionResult, unknownResult] = await Promise.all([
        api<{ items: Task[] }>(`/cases/${caseId}/tasks`),
        api<{ items: Hypothesis[] }>(`/cases/${caseId}/hypotheses`),
        api<{ items: Contradiction[] }>(`/cases/${caseId}/contradictions`),
        api<{ items: any[] }>(`/cases/${caseId}/unknowns`),
      ]);
      setTasks(taskResult.items ?? []);
      setHypotheses(hypothesisResult.items ?? []);
      setContradictions(contradictionResult.items ?? []);
      setUnknowns(unknownResult.items ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Workflow data could not be loaded.");
    }
  }, [caseId]);

  useEffect(() => { void load(); }, [load]);

  const createTask = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api(`/cases/${caseId}/tasks`, { method: "POST", body: JSON.stringify({ title, priority: "MEDIUM" }) });
      setTitle("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Task could not be created.");
    } finally { setBusy(false); }
  };

  const createHypothesis = async () => {
    if (!hypothesis.trim()) return;
    setBusy(true);
    try {
      await api(`/cases/${caseId}/hypotheses`, { method: "POST", body: JSON.stringify({ statement: hypothesis, status: "UNVERIFIED" }) });
      setHypothesis("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Hypothesis could not be saved.");
    } finally { setBusy(false); }
  };

  const createNote = async () => {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await api(`/cases/${caseId}/notes`, { method: "POST", body: JSON.stringify({ text: note, classification: "CONFIDENTIAL" }) });
      setNote("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Note could not be saved.");
    } finally { setBusy(false); }
  };

  return (
    <section className="panel" aria-labelledby="workflow-title">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "baseline" }}>
        <div>
          <h2 id="workflow-title">Investigation workflow</h2>
          <p className="hint">Human-reviewable tasks, hypotheses, contradictions, and unknown information. These are leads for verification, not legal conclusions.</p>
        </div>
        <button className="btn btn-secondary" type="button" onClick={() => void load()} disabled={busy}>Refresh</button>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}

      <div className="workflow-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
        <div>
          <h3>Tasks <span className="muted">({tasks.length})</span></h3>
          <div className="form-row">
            <input aria-label="New investigation task" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Verify identity" />
            <button className="btn btn-primary" type="button" onClick={() => void createTask()} disabled={busy || !title.trim()}>Add</button>
          </div>
          {tasks.length === 0 ? <p className="muted">No tasks recorded.</p> : <ul className="compact-list">{tasks.slice(0, 5).map((task) => <li key={task.id}><strong>{task.title}</strong><span className="muted"> · {task.status} · {task.priority}</span></li>)}</ul>}
        </div>
        <div>
          <h3>Hypotheses <span className="muted">({hypotheses.length})</span></h3>
          <div className="form-row">
            <input aria-label="New investigation hypothesis" value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} placeholder="A testable explanation" />
            <button className="btn btn-primary" type="button" onClick={() => void createHypothesis()} disabled={busy || !hypothesis.trim()}>Record</button>
          </div>
          {hypotheses.length === 0 ? <p className="muted">No hypotheses recorded.</p> : <ul className="compact-list">{hypotheses.slice(0, 5).map((item) => <li key={item.id}><strong>{item.statement}</strong><span className="muted"> · {item.status}</span></li>)}</ul>}
        </div>
        <div>
          <h3>Contradictions <span className="muted">({contradictions.length})</span></h3>
          {contradictions.length === 0 ? <p className="muted">No recorded contradictions.</p> : <ul className="compact-list">{contradictions.slice(0, 5).map((item) => <li key={item.id}><strong>{item.subject_key} — {item.predicate}</strong><br /><span className="muted">{item.explanation}</span></li>)}</ul>}
        </div>
      </div>

      <div className="workflow-unknowns" style={{ marginTop: 16, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
        <h3>What CrimeLink does not know <span className="muted">({unknowns.length})</span></h3>
        {unknowns.length === 0 ? <p className="muted">No unknowns have been recorded; this is not proof that information is complete.</p> : <ul className="compact-list">{unknowns.slice(0, 8).map((item, index) => <li key={`${item.question}-${index}`}><strong>{item.state}: {item.question}</strong><br /><span className="muted">Next verification: {(item.verification_steps ?? []).join("; ")}</span></li>)}</ul>}
      </div>

      <div className="form-row" style={{ marginTop: 16 }}>
        <input aria-label="Investigator note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Add a structured investigator note" />
        <button className="btn btn-secondary" type="button" onClick={() => void createNote()} disabled={busy || !note.trim()}>Save note</button>
      </div>
    </section>
  );
}
