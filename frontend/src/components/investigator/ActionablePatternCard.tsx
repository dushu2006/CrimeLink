/**
 * Actionable Patterns — Sprint 2 upgrade from PatternList/Card
 * Entry point with counts: 5 cases / 12 entities / 23 records
 * Actions: Investigate / Compare Cases / Show Connections / View Evidence / Add to Investigation / Dismiss
 * Investigate identifies entities opens graph shows evidence related cases preserves provenance optional AI via retrieval
 * Keep FACT/INFERENCE/HYPOTHESIS/UNKNOWN
 */

import { useState } from "react";
import { investigatePattern, type PatternInvestigation } from "../../api/client";

interface PatternItem {
  id: string;
  case_id: string;
  pattern_type: string;
  confidence: number;
  status: string;
  explanation: string;
  entity_keys: string[];
  entities?: { provenance_key: string; name: string; label: string }[];
  evidence_doc_ids?: string[];
}

interface Props {
  pattern: PatternItem;
  onInvestigate?: (data: PatternInvestigation) => void;
  onCompareCases?: (patternId: string) => void;
  onShowConnections?: (entityKeys: string[]) => void;
  onViewEvidence?: (docId: string) => void;
  onAddToInvestigation?: (patternId: string) => void;
  onDismiss?: (patternId: string) => void;
  onFocusGraph?: (entityKeys: string[]) => void;
}

function evidenceLevel(conf: number, hasEvidence: boolean): "FACT" | "INFERENCE" | "HYPOTHESIS" | "UNKNOWN" {
  if (conf >= 0.9 && hasEvidence) return "FACT";
  if (conf >= 0.7 && hasEvidence) return "INFERENCE";
  if (conf >= 0.5) return "HYPOTHESIS";
  return "UNKNOWN";
}

export function ActionablePatternCard({
  pattern,
  onInvestigate,
  onCompareCases,
  onShowConnections,
  onViewEvidence,
  onAddToInvestigation,
  onDismiss,
  onFocusGraph,
}: Props) {
  const [investigating, setInvestigating] = useState(false);
  const [investigationData, setInvestigationData] = useState<PatternInvestigation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  const hasEvidence = Boolean((pattern.evidence_doc_ids && pattern.evidence_doc_ids.length > 0) || (pattern.entities && pattern.entities.length > 0));
  const level = evidenceLevel(pattern.confidence, hasEvidence);

  const handleInvestigate = async () => {
    setInvestigating(true);
    setError(null);
    try {
      const data = await investigatePattern(pattern.id);
      setInvestigationData(data);
      setShowDetails(true);
      onInvestigate?.(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInvestigating(false);
    }
  };

  // Counts: cases / entities / records
  const entityCount = pattern.entity_keys?.length || pattern.entities?.length || 0;
  const evidenceCount = pattern.evidence_doc_ids?.length || 0;
  // For cases count, we use 1 for this pattern's case, but investigationData may have related cases
  const casesCount = investigationData ? 1 + investigationData.counts.related_cases : 1;

  return (
    <div className="actionable-pattern-card">
      <div className="actionable-pattern-card-header">
        <div className="actionable-pattern-card-type">
          <span className="cl-badge cl-badge-info">{pattern.pattern_type}</span>
          <span className={`cl-badge ${level === "FACT" ? "cl-badge-success" : level === "INFERENCE" ? "cl-badge-info" : level === "HYPOTHESIS" ? "cl-badge-warning" : "cl-badge"}`}>
            {level}
          </span>
          <span className="cl-badge">{pattern.status}</span>
          <span className="cl-badge" style={{ background: "var(--surface-secondary)" }}>{Math.round(pattern.confidence * 100)}% confidence</span>
        </div>
        <button className="cl-btn" style={{ fontSize: "11px", padding: "2px 8px" }} onClick={() => setShowDetails(!showDetails)}>
          {showDetails ? "Hide" : "Details"}
        </button>
      </div>

      <h4 className="actionable-pattern-card-title">{pattern.explanation.slice(0, 120)}{pattern.explanation.length > 120 ? "…" : ""}</h4>

      <div className="actionable-pattern-card-counts">
        <span className="actionable-pattern-count">
          <strong>{casesCount}</strong> {casesCount === 1 ? "case" : "cases"}
        </span>
        <span className="actionable-pattern-count">
          <strong>{entityCount}</strong> {entityCount === 1 ? "entity" : "entities"}
        </span>
        <span className="actionable-pattern-count">
          <strong>{evidenceCount || investigationData?.counts.evidence || 0}</strong> {evidenceCount === 1 ? "record" : "records"}
        </span>
        {investigationData && (
          <>
            <span className="actionable-pattern-count">
              <strong>{investigationData.counts.relationships}</strong> relationships
            </span>
            <span className="actionable-pattern-count">
              <strong>{investigationData.counts.related_cases}</strong> related cases
            </span>
          </>
        )}
      </div>

      <p className="actionable-pattern-card-explanation">{pattern.explanation}</p>

      <div className="actionable-pattern-card-basis">
        <span className="actionable-pattern-basis-item">Confidence {pattern.confidence.toFixed(2)}</span>
        <span className="actionable-pattern-basis-item">{entityCount} linked entities</span>
        {evidenceCount > 0 && <span className="actionable-pattern-basis-item">{evidenceCount} evidence sources</span>}
        <span className="actionable-pattern-basis-item">Status: {pattern.status}</span>
      </div>

      {pattern.entities && pattern.entities.length > 0 && (
        <div className="actionable-pattern-card-evidence">
          <span className="actionable-pattern-evidence-header">Linked entities</span>
          <div className="actionable-pattern-evidence-list">
            {pattern.entities.slice(0, 6).map((ent) => (
              <span key={ent.provenance_key} className="actionable-pattern-evidence-chip">
                {ent.name} ({ent.label})
              </span>
            ))}
          </div>
        </div>
      )}

      {investigationData && showDetails && (
        <div className="actionable-pattern-card-evidence">
          <span className="actionable-pattern-evidence-header">Investigation — entities, related cases, evidence, provenance</span>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "6px" }}>
            <div>
              <strong style={{ fontSize: "11px" }}>Entities ({investigationData.counts.entities}):</strong>
              <div className="actionable-pattern-evidence-list" style={{ marginTop: "4px" }}>
                {investigationData.entities.slice(0, 8).map((e: any) => (
                  <span key={e.provenance_key} className="actionable-pattern-evidence-chip" title={e.provenance_key}>
                    {e.name} — {e.label} — {Math.round(e.confidence * 100)}%
                  </span>
                ))}
              </div>
            </div>
            {investigationData.related_cases.length > 0 && (
              <div>
                <strong style={{ fontSize: "11px" }}>Related cases ({investigationData.counts.related_cases}):</strong>
                <div className="actionable-pattern-evidence-list" style={{ marginTop: "4px" }}>
                  {investigationData.related_cases.map((c: any) => (
                    <span key={c.id} className="actionable-pattern-evidence-chip">
                      {c.case_number} — {c.title.slice(0, 30)} — {c.shared_entities.length} shared
                    </span>
                  ))}
                </div>
              </div>
            )}
            {investigationData.evidence.doc_ids.length > 0 && (
              <div>
                <strong style={{ fontSize: "11px" }}>Evidence ({investigationData.counts.evidence}):</strong>
                <div className="actionable-pattern-evidence-list" style={{ marginTop: "4px" }}>
                  {investigationData.evidence.doc_ids.slice(0, 6).map((docId: string) => (
                    <button key={docId} className="actionable-pattern-evidence-chip" style={{ cursor: "pointer" }} onClick={() => onViewEvidence?.(docId)}>
                      📄 {docId.slice(0, 12)}…
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div style={{ fontSize: "10px", color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>
              Provenance: {investigationData.provenance.source} — Pattern {investigationData.provenance.pattern_id} — Case {investigationData.provenance.case_id.slice(0, 8)}
            </div>
          </div>
        </div>
      )}

      {error && <div className="cl-error" style={{ fontSize: "12px" }}>{error}</div>}

      <div className="actionable-pattern-card-actions">
        <button className="actionable-pattern-action-btn actionable-pattern-action-btn-primary" onClick={() => void handleInvestigate()} disabled={investigating}>
          {investigating ? "Investigating…" : "🔍 Investigate"}
        </button>
        <button className="actionable-pattern-action-btn" onClick={() => onCompareCases?.(pattern.id)}>
          📊 Compare Cases
        </button>
        <button className="actionable-pattern-action-btn" onClick={() => onShowConnections?.(pattern.entity_keys)}>
          🔗 Show Connections
        </button>
        <button className="actionable-pattern-action-btn" onClick={() => onFocusGraph?.(pattern.entity_keys)}>
          🕸️ View Graph
        </button>
        {pattern.evidence_doc_ids?.[0] && (
          <button className="actionable-pattern-action-btn" onClick={() => onViewEvidence?.(pattern.evidence_doc_ids![0])}>
            📄 View Evidence
          </button>
        )}
        <button className="actionable-pattern-action-btn" onClick={() => onAddToInvestigation?.(pattern.id)}>
          ➕ Add to Investigation
        </button>
        <button className="actionable-pattern-action-btn actionable-pattern-action-btn-danger" onClick={() => onDismiss?.(pattern.id)}>
          ✕ Dismiss
        </button>
      </div>

      <div className="actionable-pattern-disclaimer">
        <span>AI-assisted finding — requires verification</span>
        <span className="actionable-pattern-grounding">
          <span className="cl-badge cl-badge-info" style={{ fontSize: "9px" }}>Grounding: {level}</span>
          <span style={{ fontSize: "10px", color: "var(--text-tertiary)" }}>Provenance preserved: {pattern.id.slice(0, 8)}…</span>
        </span>
      </div>
    </div>
  );
}

interface ListProps {
  patterns: PatternItem[];
  onInvestigate?: (data: PatternInvestigation) => void;
  onCompareCases?: (patternId: string) => void;
  onShowConnections?: (entityKeys: string[]) => void;
  onViewEvidence?: (docId: string) => void;
  onAddToInvestigation?: (patternId: string) => void;
  onDismiss?: (patternId: string) => void;
  onFocusGraph?: (entityKeys: string[]) => void;
}

export function ActionablePatternList(props: ListProps) {
  if (props.patterns.length === 0) {
    return (
      <div className="cl-empty">
        <div className="cl-empty-title">No patterns</div>
        <div className="cl-empty-desc">No suspicious patterns detected. Deterministic detectors surface signals to prioritize, not conclusions. Real data only.</div>
      </div>
    );
  }

  return (
    <div className="actionable-patterns">
      <div className="actionable-patterns-header">
        <h3 className="actionable-patterns-title">Actionable Patterns — {props.patterns.length} signals</h3>
        <span className="cl-badge cl-badge-warning">{props.patterns.filter((p) => p.status === "NEW").length} new</span>
      </div>
      {props.patterns.map((p) => (
        <ActionablePatternCard
          key={p.id}
          pattern={p}
          onInvestigate={props.onInvestigate}
          onCompareCases={props.onCompareCases}
          onShowConnections={props.onShowConnections}
          onViewEvidence={props.onViewEvidence}
          onAddToInvestigation={props.onAddToInvestigation}
          onDismiss={props.onDismiss}
          onFocusGraph={props.onFocusGraph}
        />
      ))}
    </div>
  );
}

export default ActionablePatternCard;
