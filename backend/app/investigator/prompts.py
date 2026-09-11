"""The investigator prompt contract and the language guard.

The model is an explainer, never a finder. The orchestrator computes every
structural claim deterministically and hands the model a minimized,
pseudonymized brief; the model returns :class:`InvestigatorNarrative` JSON
only. Anything outside that contract — extra fields, invented entities,
accusatory language — is ignored or neutralized, never rendered.

``neutralize_language`` is the last line of defence before model prose
reaches the investigator: it replaces accusatory terms with neutral
equivalents and reports every edit, so the substitution itself is auditable.
Data vocabulary (``criminal_status``, ``criminal history`` as a record
category) is explicitly *not* treated as accusation.
"""

from __future__ import annotations

import re

CONTRACT_VERSION = "investigator-narrative-v1"

INVESTIGATOR_SYSTEM = """You explain an already-computed investigation to a police investigator.
Hard rules:
1. Explain ONLY the findings given below. Never invent entities, relationships, dates, or evidence.
2. Every paragraph must be traceable to the brief; if the brief is thin, say so instead of padding.
3. Keep observation (what the data shows), interpretation (what it could mean), and assessment (your judgement with confidence) separate.
4. Always offer innocent alternatives for suspicious readings.
5. Never call anyone a criminal, offender, culprit, mastermind, or any synonym. Describe records, not people: "the dataset records one conviction" is allowed; "he is a criminal" is not.
6. Respond with a single JSON object and nothing else, using exactly these keys: summary, observation, interpretation, assessment, convergence_note, caveats (list), suggested_next_actions (list).
"""

#: Accusatory terms and their neutral replacements. Word-boundaried so
#: "offenders" (plural records) still matches, while data vocabulary such
#: as criminal_status / criminal history is allow-listed below and skipped.
GUILT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("criminal network", "network under investigation"),
    ("crime syndicate", "group under investigation"),
    ("mastermind", "key figure"),
    ("kingpin", "key figure"),
    ("ringleader", "organizer"),
    ("culprit", "person involved"),
    ("perpetrator", "person involved"),
    ("offender", "person involved"),
    ("guilty", "responsible"),
    ("confession", "statement"),
    ("confessed", "stated"),
    ("confess", "stated"),
)

#: Data vocabulary that must never be treated as accusation.
_ALLOWED_CONTEXT = re.compile(
    r"criminal[_\s-]?history|criminal[_\s-]?records?|criminal_status",
    re.IGNORECASE,
)


def _allowed_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _ALLOWED_CONTEXT.finditer(text)]


def find_guilt_terms(text: str | None) -> list[str]:
    """Return the accusatory terms present in ``text`` (audit helper)."""
    if not text:
        return []
    spans = _allowed_spans(text)
    found: list[str] = []
    for term, _replacement in GUILT_REPLACEMENTS:
        for match in re.finditer(r"\b" + re.escape(term) + r"s?\b", text, re.IGNORECASE):
            if any(start <= match.start() < end for start, end in spans):
                continue
            found.append(term)
            break
    return found


def neutralize_language(text: str | None) -> tuple[str, list[str]]:
    """Replace accusatory wording with neutral equivalents.

    Returns the cleaned text plus a human-readable edit list (``[]`` when
    nothing needed changing). The rewrite is deliberately mechanical: it
    can only make prose *less* accusatory, never more.
    """
    if not text:
        return "", []
    spans = _allowed_spans(text)
    edits: list[str] = []

    def _replace(match: re.Match[str], term: str, replacement: str) -> str:
        if any(start <= match.start() < end for start, end in spans):
            return match.group(0)
        original = match.group(0)
        edits.append(f"Reworded {original!r} as {replacement!r}.")
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    cleaned = text
    for term, replacement in GUILT_REPLACEMENTS:
        cleaned = re.sub(
            r"\b" + re.escape(term) + r"s?\b",
            lambda match, _t=term, _r=replacement: _replace(match, _t, _r),
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned, edits


def build_investigation_prompt(
    *,
    question: str,
    scope_summary: str,
    entity_lines: list[str],
    relationship_lines: list[str],
    pattern_lines: list[str],
    hypothesis_lines: list[str],
    convergence_line: str,
    gap_lines: list[str],
    per_section_cap: int = 8,
    line_cap: int = 300,
) -> str:
    """Assemble the minimized narrative brief (deterministic, capped)."""
    def _block(title: str, lines: list[str]) -> str:
        trimmed = [line[:line_cap] for line in lines[:per_section_cap]]
        body = "\n".join(f"- {line}" for line in trimmed) if trimmed else "- (none)"
        return f"{title}:\n{body}"

    sections = [
        f"Question: {question[:500]}",
        f"Scope: {scope_summary[:300]}",
        _block("Resolved entities", entity_lines),
        _block("Relationships", relationship_lines),
        _block("Patterns", pattern_lines),
        _block("Hypotheses", hypothesis_lines),
        f"Convergence: {convergence_line[:300]}",
        _block("Data gaps", gap_lines),
    ]
    return "\n\n".join(sections)
