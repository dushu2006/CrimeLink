"""AI safety firewall primitives.

AI is an untrusted analyst over authoritative CrimeLink records.  This module
keeps schema validation, evidence-reference validation, neutral-language
checks, and write-policy checks testable without a provider or a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.ai.schemas import FindingResult

FORBIDDEN_AUTHORITATIVE_ACTIONS = frozenset({
    "modify_evidence", "delete_evidence", "merge_identity", "approve_merge",
    "close_case", "seal_case", "export_evidence", "change_legal_status",
    "create_authoritative_edge", "recommend_arrest", "recommend_guilt",
})
_NEUTRAL_TERMS = ("criminal", "guilty", "terrorist", "gang member", "kingpin", "mastermind")
_PROMPT_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous instructions|system\s+message|developer\s+message|reveal\s+the\s+prompt|act\s+as\s+the\s+system)",
    re.IGNORECASE,
)


class AISafetyViolation(ValueError):
    """Provider output or requested AI action failed the safety contract."""


@dataclass(frozen=True)
class SafetyReport:
    evidence_errors: tuple[str, ...] = ()
    entity_errors: tuple[str, ...] = ()
    neutral_language_warnings: tuple[str, ...] = ()
    prompt_injection_detected: bool = False

    @property
    def safe(self) -> bool:
        return not self.evidence_errors and not self.entity_errors


def sanitize_untrusted_evidence(text: str) -> str:
    """Mark instruction-like text as data; never treat evidence as a prompt."""
    value = str(text or "")
    return _PROMPT_INJECTION.sub(lambda match: f"[UNTRUSTED_TEXT:{match.group(0)}]", value)


def validate_authoritative_action(action: str) -> None:
    normalized = str(action or "").strip().lower()
    if normalized in FORBIDDEN_AUTHORITATIVE_ACTIONS:
        raise AISafetyViolation(
            f"AI cannot perform authoritative action {normalized!r}; use an explicit human workflow."
        )


def validate_evidence_references(finding: FindingResult, allowed_evidence_ids: Iterable[str]) -> tuple[str, ...]:
    allowed = {str(value) for value in allowed_evidence_ids}
    errors = []
    for ref in finding.evidence_refs:
        if ref.doc_id not in allowed:
            errors.append(f"Evidence reference does not exist in the retrieved evidence package: {ref.doc_id}")
    for step in finding.reasoning_steps:
        # Reasoning refs are document IDs in the gateway contract.
        for ref in step.evidence_refs:
            if ref not in allowed:
                errors.append(f"Reasoning step references nonexistent evidence: {ref}")
    if finding.evidence_level == "FACT" and not finding.evidence_refs:
        errors.append("FACT-level output must include evidence references.")
    return tuple(errors)


def validate_entities(finding: FindingResult, allowed_entity_ids: Iterable[str]) -> tuple[str, ...]:
    allowed = {str(value) for value in allowed_entity_ids}
    return tuple(
        f"Entity reference does not exist in the retrieved context: {entity.pseudo_id}"
        for entity in finding.entities
        if entity.pseudo_id not in allowed
    )


def neutral_language_warnings(finding: FindingResult) -> tuple[str, ...]:
    text = " ".join([finding.summary, *(step.statement for step in finding.reasoning_steps)])
    lower = text.lower()
    return tuple(f"Output contains non-neutral term: {term}" for term in _NEUTRAL_TERMS if term in lower)


def validate_finding(
    finding: FindingResult,
    *,
    allowed_evidence_ids: Iterable[str],
    allowed_entity_ids: Iterable[str] = (),
    reject_neutral_language: bool = False,
) -> SafetyReport:
    evidence_errors = validate_evidence_references(finding, allowed_evidence_ids)
    entity_errors = validate_entities(finding, allowed_entity_ids) if allowed_entity_ids else ()
    warnings = neutral_language_warnings(finding)
    report = SafetyReport(evidence_errors, entity_errors, warnings)
    if not report.safe or (reject_neutral_language and warnings):
        raise AISafetyViolation("; ".join((*evidence_errors, *entity_errors, *warnings)))
    return report
