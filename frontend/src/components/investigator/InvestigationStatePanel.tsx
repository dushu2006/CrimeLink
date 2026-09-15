/**
 * Investigation State Panel — Priority 5 in UX redesign
 * 
 * CrimeLink shouldn't forget what investigator is investigating.
 * 
 * My Investigation
 * ────────────────────
 * Subject: Person A
 * Focus: Warehouse Incident
 * Pinned entities: Person A, Person B, Vehicle V12
 * Important evidence: CCTV-014, CDR-021
 * Hypotheses: H-001
 * Notes: ...
 * Saved queries: ...
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../Status";

export interface PinnedEntity {
  id: string;
  name: string;
  type: string;
  reason?: string;
}

export interface ImportantEvidence {
  id: string;
  type: string;
  title?: string;
}

export interface Hypothesis {
  id: string;
  statement: string;
  status: "testing" | "supported" | "contradicted" | "unknown";
}

export interface InvestigationState {
  subject?: string;
  focus?: string;
  pinnedEntities: PinnedEntity[];
  importantEvidence: ImportantEvidence[];
  hypotheses: Hypothesis[];
  notes: string;
  savedQueries: string[];
}

interface InvestigationStatePanelProps {
  state: InvestigationState;
  onUpdateNotes: (notes: string) => void;
  onUnpinEntity: (id: string) => void;
  onRemoveEvidence: (id: string) => void;
  onAddHypothesis: (statement: string) => void;
  onPinEntity?: (entity: PinnedEntity) => void;
}

export function InvestigationStatePanel({ state, onUpdateNotes, onUnpinEntity, onRemoveEvidence, onAddHypothesis }: InvestigationStatePanelProps) {
  const [newHypothesis, setNewHypothesis] = useState("");
  const [notesDraft, setNotesDraft] = useState(state.notes);
  const [isEditingNotes, setIsEditingNotes] = useState(false);

  const handleSaveNotes = () => {
    onUpdateNotes(notesDraft);
    setIsEditingNotes(false);
  };

  const handleAddHypothesis = (e: React.FormEvent) => {
    e.preventDefault();
    if (newHypothesis.trim().length >= 3) {
      onAddHypothesis(newHypothesis.trim());
      setNewHypothesis("");
    }
  };

  return (
    <div className="investigation-state-panel">
      <header className="investigation-state-header">
        <h2 className="investigation-state-title">My Investigation</h2>
        <span className="badge badge-navy">Active</span>
      </header>

      <div className="investigation-state-content">
        {(state.subject || state.focus) && (
          <div className="investigation-state-section">
            <div className="investigation-state-field">
              <span className="investigation-state-label">Subject</span>
              <span className="investigation-state-value">{state.subject || "—"}</span>
            </div>
            <div className="investigation-state-field">
              <span className="investigation-state-label">Focus</span>
              <span className="investigation-state-value">{state.focus || "—"}</span>
            </div>
          </div>
        )}

        <div className="investigation-state-section">
          <h3 className="investigation-state-section-title">
            Pinned entities <span className="count-badge">{state.pinnedEntities.length}</span>
          </h3>
          {state.pinnedEntities.length === 0 ? (
            <p className="muted">No pinned entities. Click a node → Pin to investigation.</p>
          ) : (
            <div className="pinned-entities-list">
              {state.pinnedEntities.map((entity) => (
                <div key={entity.id} className="pinned-entity-item">
                  <Link to={`/entities/${encodeURIComponent(entity.id)}`} className="pinned-entity-link">
                    <span className="pinned-entity-type">{entity.type}</span>
                    <span className="pinned-entity-name">{entity.name}</span>
                  </Link>
                  <button className="btn-icon" onClick={() => onUnpinEntity(entity.id)} title="Unpin">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="investigation-state-section">
          <h3 className="investigation-state-section-title">
            Important evidence <span className="count-badge">{state.importantEvidence.length}</span>
          </h3>
          {state.importantEvidence.length === 0 ? (
            <p className="muted">No important evidence. Click evidence → Pin.</p>
          ) : (
            <div className="important-evidence-list">
              {state.importantEvidence.map((ev) => (
                <div key={ev.id} className="important-evidence-item">
                  <span className="evidence-type-badge">{ev.type}</span>
                  <span className="evidence-id">{ev.id.slice(0, 12)}…</span>
                  <button className="btn-icon" onClick={() => onRemoveEvidence(ev.id)} title="Remove">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="investigation-state-section">
          <h3 className="investigation-state-section-title">
            Hypotheses <span className="count-badge">{state.hypotheses.length}</span>
          </h3>
          <form onSubmit={handleAddHypothesis} className="hypothesis-form">
            <input
              className="inline-input"
              placeholder="Hypothesis: Person A coordinated with Person B..."
              value={newHypothesis}
              onChange={(e) => setNewHypothesis(e.target.value)}
              maxLength={200}
            />
            <button type="submit" className="btn btn-secondary btn-small" disabled={newHypothesis.trim().length < 3}>
              Add
            </button>
          </form>
          {state.hypotheses.length === 0 ? (
            <p className="muted">No hypotheses yet. Formulate what you think happened.</p>
          ) : (
            <div className="hypotheses-list">
              {state.hypotheses.map((h) => (
                <div key={h.id} className={`hypothesis-item hypothesis-${h.status}`}>
                  <div className="hypothesis-header">
                    <span className="hypothesis-id">{h.id}</span>
                    <Badge value={h.status} />
                  </div>
                  <p className="hypothesis-statement">{h.statement}</p>
                  <div className="hypothesis-evidence-analysis">
                    <div className="evidence-analysis-grid">
                      <div className="evidence-analysis-col supports">
                        <span className="evidence-analysis-label">Supports</span>
                        <span className="muted">—</span>
                      </div>
                      <div className="evidence-analysis-col contradicts">
                        <span className="evidence-analysis-label">Contradicts</span>
                        <span className="muted">—</span>
                      </div>
                      <div className="evidence-analysis-col unknown">
                        <span className="evidence-analysis-label">Unknown</span>
                        <span className="muted">—</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="investigation-state-section">
          <h3 className="investigation-state-section-title">Notes</h3>
          {isEditingNotes ? (
            <div className="notes-edit">
              <textarea
                className="notes-textarea"
                value={notesDraft}
                onChange={(e) => setNotesDraft(e.target.value)}
                placeholder="Investigator notes..."
                rows={4}
              />
              <div className="row-actions">
                <button className="btn btn-primary btn-small" onClick={handleSaveNotes}>
                  Save
                </button>
                <button className="btn btn-tertiary btn-small" onClick={() => setIsEditingNotes(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="notes-display">
              <p className="notes-text">{state.notes || "No notes yet."}</p>
              <button className="btn btn-tertiary btn-small" onClick={() => setIsEditingNotes(true)}>
                Edit notes
              </button>
            </div>
          )}
        </div>

        {state.savedQueries.length > 0 && (
          <div className="investigation-state-section">
            <h3 className="investigation-state-section-title">Saved queries</h3>
            <div className="saved-queries-list">
              {state.savedQueries.slice(-5).map((q, idx) => (
                <div key={idx} className="saved-query-item">
                  <span className="saved-query-text">{q.slice(0, 80)}…</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
