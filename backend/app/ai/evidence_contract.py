"""Deterministic answer-contract helpers for evidence-grounded AI output.

The model may explain a finding, but it does not decide whether a statement is
supported.  This module keeps the small amount of answer-quality policy that
can be evaluated without an LLM in one place:

* every claim gets document references;
* support strength is derived from the existing FACT/INFERENCE/HYPOTHESIS/
  UNKNOWN label and independent source types;
* legacy model responses are upgraded to the structured three-layer contract;
* wording is conservative when the evidence does not establish intent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.ai.schemas import ClaimCitation, FindingResult

SUPPORT_LEVELS = (
    "DIRECTLY_SUPPORTED",
    "STRONGLY_SUPPORTED",
    "INFERRED",
    "UNSUPPORTED",
)


def classify_evidence_support(
    evidence_level: str,
    evidence_refs: Iterable[str],
    source_types: Iterable[str] = (),
) -> str:
    """Classify support without allowing the model to inflate confidence.

    ``FACT`` means a cited record states the claim directly.  It becomes
    ``STRONGLY_SUPPORTED`` only when at least two distinct evidence types are
    available.  ``INFERENCE`` and cited ``HYPOTHESIS`` remain analytical
    readings, never facts.  Missing citations are always unsupported.
    """

    refs = {str(ref) for ref in evidence_refs if ref}
    types = {str(value).strip().upper() for value in source_types if value}
    level = str(evidence_level or "UNKNOWN").upper()
    if not refs:
        return "UNSUPPORTED"
    if level == "FACT":
        return "STRONGLY_SUPPORTED" if len(types) >= 2 and len(refs) >= 2 else "DIRECTLY_SUPPORTED"
    if level in {"INFERENCE", "HYPOTHESIS"}:
        return "INFERRED"
    return "UNSUPPORTED"


def _normalise_claims(finding: FindingResult) -> list[ClaimCitation]:
    """Make a claim-to-citation list from new or legacy structured output."""

    if finding.claims:
        return list(finding.claims)

    claims: list[ClaimCitation] = []
    for relationship in finding.relationships:
        if not isinstance(relationship, dict):
            continue
        refs = relationship.get("evidence_refs") or relationship.get("evidence") or []
        if isinstance(refs, str):
            refs = [refs]
        if isinstance(refs, list):
            source = relationship.get("source_person") or relationship.get("source") or "The records"
            target = relationship.get("target_person") or relationship.get("target") or "a second entity"
            relationship_type = relationship.get("relationship_type") or relationship.get("rel_type") or "relationship"
            relationship_level = str(relationship.get("classification") or "UNKNOWN").upper()
            if relationship_level not in {"FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"}:
                relationship_level = "UNKNOWN"
            claims.append(
                ClaimCitation(
                    claim=str(
                        relationship.get("explanation")
                        or f"{source} and {target} have a documented {relationship_type} relationship."
                    ),
                    evidence_refs=[str(ref) for ref in refs if ref],
                    evidence_level=relationship_level,
                    support_level=classify_evidence_support(
                        relationship_level, refs
                    ),
                )
            )
    for step in finding.reasoning_steps:
        if step.statement.strip():
            claims.append(
                ClaimCitation(
                    claim=step.statement.strip(),
                    evidence_refs=list(step.evidence_refs),
                    evidence_level=step.evidence_level,
                    support_level=classify_evidence_support(
                        step.evidence_level, step.evidence_refs
                    ),
                )
            )
    if not claims and finding.summary.strip():
        refs = [ref.doc_id for ref in finding.evidence_refs]
        claims.append(
            ClaimCitation(
                claim=finding.summary.strip(),
                evidence_refs=refs,
                evidence_level=finding.evidence_level,
                support_level=classify_evidence_support(
                    finding.evidence_level, refs
                ),
            )
        )
    return claims


def enrich_finding_contract(
    finding: FindingResult,
    *,
    evidence_type_by_id: Mapping[str, str] | None = None,
    answer_mode: str = "GENERAL",
) -> FindingResult:
    """Return a backwards-compatible finding with explicit answer layers.

    Existing clients continue to use ``summary`` and ``evidence_refs``.  New
    consumers can use ``direct_answer``, ``evidence_explanation``,
    ``investigator_interpretation`` and ``claims`` without requiring a UI
    migration in the same release.
    """

    type_map = {str(key): str(value) for key, value in (evidence_type_by_id or {}).items()}
    claims = _normalise_claims(finding)
    all_refs = [ref.doc_id for ref in finding.evidence_refs]
    for claim in claims:
        all_refs.extend(claim.evidence_refs)
    support = classify_evidence_support(
        finding.evidence_level,
        all_refs,
        (type_map.get(ref) for ref in all_refs),
    )

    # Keep model-provided claim labels only when they are no stronger than the
    # deterministic boundary.  A model may be conservative, never expansive.
    safe_claims: list[ClaimCitation] = []
    for claim in claims:
        claim_refs = [str(ref) for ref in claim.evidence_refs if ref]
        claim_types = (type_map.get(ref) for ref in claim_refs)
        derived = classify_evidence_support(
            claim.evidence_level, claim_refs, claim_types
        )
        safe_claims.append(claim.model_copy(update={"support_level": derived}))

    safe_relationships: list[dict[str, Any]] = []
    for relationship in finding.relationships:
        if not isinstance(relationship, dict):
            continue
        item = dict(relationship)
        refs = item.get("evidence_refs") or item.get("evidence") or []
        if isinstance(refs, str):
            refs = [refs]
        level = str(item.get("classification") or "UNKNOWN").upper()
        if level not in {"FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"}:
            level = "UNKNOWN"
        item["support_level"] = classify_evidence_support(
            level, refs, (type_map.get(str(ref)) for ref in refs)
        )
        safe_relationships.append(item)

    evidence_explanation = finding.evidence_explanation
    if not evidence_explanation:
        statements = [claim.claim for claim in safe_claims if claim.evidence_refs]
        evidence_explanation = " ".join(statements) if statements else (
            "No cited evidence-backed explanation was produced."
        )

    interpretation = finding.investigator_interpretation
    if not interpretation:
        if support in {"INFERRED", "UNSUPPORTED"}:
            interpretation = (
                "The available records support an analytical reading only; they do "
                "not by themselves establish intent, responsibility, or guilt."
            )
        else:
            interpretation = (
                "The cited records describe documented activity. Their significance "
                "still requires investigator review."
            )

    establishes = list(finding.establishes)
    if not establishes:
        establishes = [
            claim.claim
            for claim in safe_claims
            if claim.evidence_level == "FACT" and claim.evidence_refs
        ][:8]

    does_not_establish = list(finding.does_not_establish)
    if not does_not_establish:
        does_not_establish = [
            "The available records do not by themselves establish intent or legal responsibility."
        ]

    return finding.model_copy(
        update={
            "answer_mode": answer_mode,
            "direct_answer": finding.direct_answer or finding.summary,
            "evidence_explanation": evidence_explanation,
            "investigator_interpretation": interpretation,
            "establishes": establishes,
            "does_not_establish": does_not_establish,
            "claims": safe_claims,
            "relationships": safe_relationships,
            "evidence_support": support,
        }
    )


def citation_coverage(
    finding: FindingResult,
    allowed_evidence_ids: Iterable[str],
) -> float:
    """Return the fraction of claims whose citations are in the case package."""

    claims = list(finding.claims)
    if not claims:
        return 0.0
    allowed = {str(value) for value in allowed_evidence_ids}
    supported = sum(
        1
        for claim in claims
        if claim.evidence_refs and set(claim.evidence_refs).issubset(allowed)
    )
    return round(supported / len(claims), 3)


def evidence_inventory(
    documents: Iterable[Mapping[str, Any]],
    *,
    known_types: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a safe missing-data inventory from case-scoped metadata."""

    available: set[str] = set()
    document_ids: list[str] = []
    for document in documents:
        doc_id = document.get("doc_id")
        if doc_id:
            document_ids.append(str(doc_id))
        raw_type = str(document.get("document_type") or "").strip()
        if raw_type:
            available.add(raw_type.rsplit(".", 1)[-1].upper())
    known = {str(value).rsplit(".", 1)[-1].upper() for value in known_types if value}
    return {
        "available_types": sorted(available),
        "missing_types": sorted(known - available),
        "document_ids": sorted(set(document_ids)),
    }
