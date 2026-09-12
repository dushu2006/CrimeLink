/**
 * A detected suspicious/unusual pattern, rendered as an investigative signal.
 *
 * The card is required to say three things the backend computed, and never to
 * imply a fourth it did not:
 *
 *   what was detected   → pattern type + explanation + affected entities
 *   how well supported  → strength + strength factors + evidence pointers
 *   what challenges it  → contradictions considered + innocent alternatives
 *
 * It must never read as a verdict, hence the standing signal note and the
 * absence of any guilt vocabulary.
 */

import type { SuspiciousPattern } from "../../api/client";
import { Badge } from "../Status";
import {
  PATTERN_SIGNAL_NOTE,
  describeStrengthFactors,
  labelText,
  patternHasChallenge,
  patternScopeSentence,
  patternSourceCount,
  patternTypeLabel,
  strengthSentence,
  strengthTone,
} from "../../lib/investigator";
import { EvidenceList, NoteList } from "./InvestigatorEvidence";

export function PatternCard({
  pattern,
  selected,
  onSelect,
}: {
  pattern: SuspiciousPattern;
  selected?: boolean;
  onSelect?: (pattern: SuspiciousPattern) => void;
}) {
  const sourceCount = patternSourceCount(pattern);
  const challenged = patternHasChallenge(pattern);

  return (
    <article
      className={`inv-pattern-card ${pattern.excluded ? "inv-pattern-excluded" : ""} ${
        selected ? "inv-selected" : ""
      }`}
      data-kind={pattern.kind}
    >
      <header>
        <div className="inv-pattern-title">
          <span className="inv-pattern-kind">{patternTypeLabel(pattern.kind)}</span>
          <h3>{pattern.title}</h3>
        </div>
        <div className="inv-pattern-badges">
          <Badge value={pattern.inference_label} />
          <span className={`badge badge-${strengthTone(pattern.strength)}`}>
            {pattern.strength} support
          </span>
          {sourceCount > 0 ? (
            <span className="badge badge-muted">
              {sourceCount} source{sourceCount === 1 ? "" : "s"}
            </span>
          ) : (
            <span className="badge badge-muted">no openable source</span>
          )}
          {pattern.excluded && <span className="badge badge-muted">set aside</span>}
        </div>
      </header>

      <p className="inv-pattern-explanation">{pattern.explanation}</p>

      {pattern.excluded && pattern.exclusion_reason && (
        <p className="inv-exclusion">
          <strong>Set aside:</strong> {pattern.exclusion_reason}
        </p>
      )}

      {patternScopeSentence(pattern) && (
        <p className="inv-pattern-scope muted">{patternScopeSentence(pattern)}</p>
      )}

      <p className="inv-signal-note">{PATTERN_SIGNAL_NOTE}</p>

      <div className="inv-strength">
        <p>{strengthSentence(pattern.strength)}</p>
        <ul className="inv-factor-list">
          {describeStrengthFactors(pattern.strength_factors).map((line, index) => (
            <li key={`${index}-${line.slice(0, 20)}`}>{line}</li>
          ))}
        </ul>
      </div>

      {challenged && (
        <div className="inv-challenge">
          <div>
            <h4>Contradictions considered</h4>
            <NoteList
              items={pattern.contradictions_considered}
              empty="No contradiction was recorded for this pattern."
            />
          </div>
          <div>
            <h4>Alternative explanations</h4>
            <NoteList
              items={pattern.innocent_alternatives}
              empty="No non-criminal reading was recorded — treat the signal as unexplained."
            />
          </div>
        </div>
      )}

      <details className="technical-details" open={false}>
        <summary className="technical-details-toggle">
          Supporting evidence ({(pattern.evidence ?? []).length})
        </summary>
        <div className="technical-details-content">
          <EvidenceList
            items={pattern.evidence}
            empty="No evidence item is attached to this pattern."
          />
          {(pattern.entity_keys ?? []).length > 0 && (
            <p className="muted inv-entity-keys">
              Entity keys: {pattern.entity_keys.map((key) => key.split(":").pop()).join(", ")}
            </p>
          )}
        </div>
      </details>

      {onSelect && (
        <div className="row-actions">
          <button type="button" className="btn btn-secondary btn-small" onClick={() => onSelect(pattern)}>
            Inspect in focused graph
          </button>
        </div>
      )}
    </article>
  );
}

export function PatternList({
  patterns,
  selectedKind,
  onSelect,
}: {
  patterns: SuspiciousPattern[];
  selectedKind?: string | null;
  onSelect?: (pattern: SuspiciousPattern) => void;
}) {
  const live = patterns.filter((pattern) => !pattern.excluded);
  const setAside = patterns.filter((pattern) => pattern.excluded);

  if (patterns.length === 0) {
    return (
      <p className="muted">
        No unusual pattern was detected in this scope. That is a statement about the records
        present, not about activity that was never recorded.
      </p>
    );
  }

  return (
    <>
      {live.map((pattern) => (
        <PatternCard
          key={`${pattern.kind}-${pattern.title}`}
          pattern={pattern}
          selected={selectedKind === `${pattern.kind}-${pattern.title}`}
          onSelect={onSelect}
        />
      ))}
      {setAside.length > 0 && (
        <details className="technical-details">
          <summary className="technical-details-toggle">
            Set aside openly ({setAside.length}) — kept for transparency
          </summary>
          <div className="technical-details-content">
            <p className="muted">
              These combinations were examined and deliberately not treated as signals: a single
              shared location, benign-only repetition, or social-media adjacency on its own. They
              remain here so the decision to set them aside is reviewable.
            </p>
            {setAside.map((pattern) => (
              <PatternCard key={`${pattern.kind}-${pattern.title}`} pattern={pattern} />
            ))}
          </div>
        </details>
      )}
    </>
  );
}

export { labelText };
