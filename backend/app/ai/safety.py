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

#: Instruction-like spans inside a case record.  Extension is deliberately in
#: the *removal* direction rather than the "label it" direction: a model reads
#: text, not intentions, so the imperative sentence itself must not survive.
#:
#: Note the identity clause: it matches "reveal PERSON_01's real identity" or
#: "disclose the true identity", but not a case record that discusses witness
#: identity protection ("do not reveal the identity of the informant"), which
#: is a legitimate investigative sentence rather than an instruction to a model.
_PROMPT_INJECTION = re.compile(
    r"(?:"
    r"ignore\s+(?:all\s+|any\s+|these\s+|those\s+|the\s+)?(?:previous|prior|above|earlier|foregoing|preceding)?\s*"
    r"(?:instructions?|prompts?|rules?|directions?|messages?)"
    r"|disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|foregoing)?\s*"
    r"(?:instructions?|prompts?|rules?|directions?)"
    r"|forget\s+(?:everything|all\s+(?:previous|prior|above)\s+(?:instructions?|rules?))"
    r"|(?:reveal|disclose|print|show|repeat|output|share|tell)\s+(?:me\s+)?(?:the\s+|your\s+)?"
    r"(?:system\s+|developer\s+|hidden\s+|initial\s+|full\s+)?(?:prompt|instructions?|system\s+message|developer\s+message)"
    r"|(?:reveal|disclose|expose|divulge|unmask)\s+[^.\n]{0,40}?(?:real|true|actual)\s+identit(?:y|ies)"
    r"|unrestricted\s+(?:information|mode|answer|access|response)"
    r"|(?:you\s+are\s+now|from\s+now\s+on\s+you\s+(?:are|will|must)|act\s+as\s+(?:the\s+)?(?:system|developer|admin|root|dAN))"
    r"|(?:new|updated)\s+instructions?\s*[:=]"
    r"|(?:system|developer)\s+message\s*[:=]"
    r"|override\s+(?:the\s+)?(?:system|safety|security|previous|all)"
    r"|jailbreak|do\s+anything\s+now"
    r"|do\s+not\s+follow\s+(?:the\s+)?(?:system|previous|above|those)"
    r"|without\s+(?:any\s+|all\s+)?restrictions?"
    r")",
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


#: What an instruction-like span inside a case record is replaced with.  The
#: imperative text itself is *removed*, not merely labelled: a marker that still
#: contains "ignore previous instructions" is still readable as an instruction
#: by a model that does not respect the marker.  The marker preserves the fact
#: that something was stripped, which is what an investigator needs to know —
#: the original text remains unmodified in the evidence store.
_UNTRUSTED_PLACEHOLDER = "[UNTRUSTED_TEXT: instruction-like content removed from evidence]"


def sanitize_untrusted_evidence(text: str) -> str:
    """Neutralise instruction-like text inside retrieved evidence.

    Retrieved documents are DATA.  Any span that reads like an instruction to
    the model is replaced before the evidence is placed in a prompt, so the
    content cannot be read as one — the marker alone is not relied upon.
    """
    value = str(text or "")
    return _PROMPT_INJECTION.sub(_UNTRUSTED_PLACEHOLDER, value)


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
    for claim in finding.claims:
        # UNKNOWN is the explicit insufficient-evidence path; requiring a
        # citation for that boundary statement would turn honest uncertainty
        # into a false validation failure. All stronger claim levels require
        # at least one case-scoped reference.
        if not claim.evidence_refs and claim.evidence_level != "UNKNOWN":
            errors.append(f"Claim has no supporting evidence references: {claim.claim[:120]}")
        for ref in claim.evidence_refs:
            if ref not in allowed:
                errors.append(f"Claim references nonexistent evidence: {ref}")
    for index, relationship in enumerate(finding.relationships):
        if not isinstance(relationship, dict):
            continue
        refs = relationship.get("evidence_refs") or relationship.get("evidence") or []
        if isinstance(refs, str):
            refs = [refs]
        if not refs:
            errors.append(f"Relationship[{index}] has no supporting evidence references.")
            continue
        for ref in refs:
            if isinstance(ref, dict):
                ref = ref.get("doc_id") or ref.get("ref")
            if not ref:
                errors.append(f"Relationship[{index}] contains an empty evidence reference.")
            elif str(ref) not in allowed:
                errors.append(f"Relationship[{index}] references nonexistent evidence: {ref}")
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
