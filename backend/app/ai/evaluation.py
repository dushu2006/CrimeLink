"""Reproducible, provider-independent AI evaluation metrics.

The evaluator consumes a checked-in benchmark manifest or a caller-supplied
fixture.  It does not call a provider and does not treat a model's confidence
as ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    expected_entities: frozenset[str] = frozenset()
    expected_relationships: frozenset[str] = frozenset()
    expected_evidence_ids: frozenset[str] = frozenset()
    expected_contradiction: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    entity_precision: float
    entity_recall: float
    relationship_precision: float
    relationship_recall: float
    citation_accuracy: float
    contradiction_accuracy: float
    hallucination_rate: float
    cases: int

    def as_dict(self) -> dict:
        return asdict(self)


def _precision_recall(predicted: set[str], expected: set[str]) -> tuple[float, float]:
    true_positive = len(predicted & expected)
    precision = true_positive / len(predicted) if predicted else (1.0 if not expected else 0.0)
    recall = true_positive / len(expected) if expected else (1.0 if not predicted else 0.0)
    return precision, recall


def evaluate(benchmark: Iterable[BenchmarkCase], predictions: dict[str, dict]) -> EvaluationResult:
    cases = list(benchmark)
    entity_p, entity_r, rel_p, rel_r, citations, contradiction_hits, hallucinations = [], [], [], [], [], [], []
    for item in cases:
        prediction = predictions.get(item.case_id, {})
        ep, er = _precision_recall(set(prediction.get("entities", [])), set(item.expected_entities))
        rp, rr = _precision_recall(set(prediction.get("relationships", [])), set(item.expected_relationships))
        refs = set(prediction.get("evidence_ids", []))
        allowed = set(item.expected_evidence_ids)
        citations.append(len(refs & allowed) / len(refs) if refs else (1.0 if not allowed else 0.0))
        contradiction_hits.append(bool(prediction.get("contradiction_detected", False)) == item.expected_contradiction)
        # A hallucination is a cited evidence identifier outside the benchmark package.
        hallucinations.append(bool(refs - allowed))
        entity_p.append(ep); entity_r.append(er); rel_p.append(rp); rel_r.append(rr)
    count = len(cases) or 1
    return EvaluationResult(
        entity_precision=sum(entity_p) / count,
        entity_recall=sum(entity_r) / count,
        relationship_precision=sum(rel_p) / count,
        relationship_recall=sum(rel_r) / count,
        citation_accuracy=sum(citations) / count,
        contradiction_accuracy=sum(contradiction_hits) / count,
        hallucination_rate=sum(hallucinations) / count,
        cases=len(cases),
    )
