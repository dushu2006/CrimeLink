/**
 * One hypothesis under investigation, with both sides visible.
 *
 * The hypothesis is presented as a *reading of the evidence* — never as a
 * conclusion — and its supporting and contradictory evidence are given equal
 * visual weight so the reader cannot see only the case for it.
 */

import type { Hypothesis } from "../../api/client";
import { Badge } from "../Status";
import { describeStrengthFactors, strengthSentence, strengthTone } from "../../lib/investigator";
import { EvidenceDebate, NoteList } from "./InvestigatorEvidence";

export function HypothesisCard({
  hypothesis,
  selected,
  onSelect,
}: {
  hypothesis: Hypothesis;
  selected?: boolean;
  onSelect?: (hypothesis: Hypothesis) => void;
}) {
  const supporting = hypothesis.supporting ?? [];
  const contradicting = hypothesis.contradicting ?? [];

  return (
    <article className={`inv-hypothesis-card ${selected ? "inv-selected" : ""}`}>
      <header>
        <div>
          <span className="inv-hypothesis-id">{hypothesis.id}</span>
          <h3>{hypothesis.statement}</h3>
        </div>
        <div className="inv-pattern-badges">
          <Badge value={hypothesis.inference_label} />
          <span className={`badge badge-${strengthTone(hypothesis.strength)}`}>
            {hypothesis.strength} support
          </span>
        </div>
      </header>

      <p className="inv-strength-line">{strengthSentence(hypothesis.strength)}</p>

      {hypothesis.analysis && (
        <dl className="inv-observation">
          <div>
            <dt>Observation</dt>
            <dd>{hypothesis.analysis.observation}</dd>
          </div>
          <div>
            <dt>Interpretation</dt>
            <dd>{hypothesis.analysis.interpretation}</dd>
          </div>
          <div>
            <dt>Assessment</dt>
            <dd>{hypothesis.analysis.assessment}</dd>
          </div>
        </dl>
      )}

      <EvidenceDebate supporting={supporting} contradicting={contradicting} />

      <details className="technical-details">
        <summary className="technical-details-toggle">
          Why this rating ({describeStrengthFactors(hypothesis.strength_factors).length} factors)
        </summary>
        <div className="technical-details-content">
          <ul className="inv-factor-list">
            {describeStrengthFactors(hypothesis.strength_factors).map((line, index) => (
              <li key={`${index}-${line.slice(0, 20)}`}>{line}</li>
            ))}
          </ul>
          <h4>Alternative explanations considered</h4>
          <NoteList
            items={hypothesis.innocent_alternatives}
            empty="No alternative reading was recorded for this hypothesis."
          />
        </div>
      </details>

      {onSelect && (
        <div className="row-actions">
          <button
            type="button"
            className="btn btn-secondary btn-small"
            onClick={() => onSelect(hypothesis)}
          >
            Show these entities in the focused graph
          </button>
        </div>
      )}
    </article>
  );
}
