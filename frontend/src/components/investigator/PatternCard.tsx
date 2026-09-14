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
  analyticalBasisLines,
  describeStrengthFactors,
  interpretationBoundaries,
  labelText,
  patternHasChallenge,
  patternScopeSentence,
  patternSourceCount,
  patternTypeLabel,
  strengthSentence,
  strengthTone,
} from "../../lib/investigator";
import { patternIdentity } from "../../lib/pattern-identity";
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

      {pattern.why && (
        <p className="inv-pattern-why muted">
          <strong>WHY surfaced:</strong> {pattern.why}
        </p>
      )}

      {patternScopeSentence(pattern) && (
        <p className="inv-pattern-scope muted">{patternScopeSentence(pattern)}</p>
      )}

      <p className="inv-signal-note">{PATTERN_SIGNAL_NOTE}</p>

      {!pattern.excluded && pattern.analytical_basis && (
        <details className="technical-details">
          <summary className="technical-details-toggle">View analytical basis — method used</summary>
          <div className="technical-details-content">
            {(() => {
              const lines = analyticalBasisLines(pattern.analytical_basis);
              if (lines.length === 0) {
                return (
                  <p className="muted">
                    No structural metric was computed for this finding — it was surfaced by its
                    detector rule alone.
                  </p>
                );
              }
              return (
                <>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Method used</th>
                        <th>Value</th>
                        <th>Why it mattered</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((line) => (
                        <tr key={line.metric}>
                          <td>{line.metric}</td>
                          <td>{line.value}</td>
                          <td className="muted">{line.why}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {pattern.analytical_basis?.metrics &&
                    (pattern.analytical_basis.metrics as Record<string, unknown>).entity_metrics && (
                      <p className="muted inv-entity-keys">
                        Per-entity values are in the metric detail above; only the metrics actually
                        computed for this finding's entities are shown.
                      </p>
                    )}
                </>
              );
            })()}
            {(pattern.investigative_relevance || pattern.evidence_strength) && (
              <ul className="kv" style={{ marginTop: "var(--space-2)" }}>
                {pattern.investigative_relevance && (
                  <li>
                    <dt>Investigative relevance</dt>
                    <dd>
                      {pattern.investigative_relevance.relevance} —{" "}
                      {pattern.investigative_relevance.explanation}
                    </dd>
                  </li>
                )}
                {pattern.evidence_strength && (
                  <li>
                    <dt>Evidence strength</dt>
                    <dd>
                      {pattern.evidence_strength.strength} — {pattern.evidence_strength.explanation}
                    </dd>
                  </li>
                )}
                {pattern.evidence_convergence && (
                  <li>
                    <dt>Evidence convergence</dt>
                    <dd>
                      {pattern.evidence_convergence.convergence_type} ·{" "}
                      {pattern.evidence_convergence.independent_source_count} independent source
                      categor(ies)
                    </dd>
                  </li>
                )}
              </ul>
            )}
            {interpretationBoundaries(pattern).length > 0 && (
              <p className="inv-boundary-note">
                {interpretationBoundaries(pattern).join(" ")}
              </p>
            )}
          </div>
        </details>
      )}

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
      {live.map((pattern) => {
        const key = patternIdentity(pattern);
        return (
          <PatternCard
            key={key ?? undefined}
            pattern={pattern}
            selected={selectedKind === `${pattern.kind}-${pattern.title}`}
            onSelect={onSelect}
          />
        );
      })}
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
            {setAside.map((pattern) => {
              const key = patternIdentity(pattern);
              return <PatternCard key={key ?? undefined} pattern={pattern} />;
            })}
          </div>
        </details>
      )}
    </>
  );
}

export { labelText };
