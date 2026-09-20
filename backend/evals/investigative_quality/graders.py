"""Mechanical graders for one answer.

Every check is computed from observable output — the answer text, its
citations, the retrieved document ids and the case's own records — so a verdict
is reproducible and can be argued with.  Where a check cannot be decided
mechanically it is reported as ``None`` (not applicable) rather than guessed.
"""

from __future__ import annotations

import re
from typing import Any

from evals.investigative_quality.case_facts import CaseFacts
from evals.investigative_quality.question_bank import EvalQuestion

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: Words that assert culpability or intent rather than documenting evidence.
_CULPABILITY = re.compile(
    r"\b(committed|guilty|conspired|conspiracy to|proves?|proven|established that .{0,24}(committed|guilty)|"
    r"is responsible for|culpable|mastermind|masterminded|carried out the (crime|offence|offense))\b",
    re.IGNORECASE,
)

#: Phrases that keep an inference inside its documentary boundary.
_BOUNDARY = re.compile(
    r"(not proof|no proof|does not establish|do not establish|does not show|does not by itself|"
    r"cannot be (concluded|inferred)|is not a conclusion|not a conclusion|documentary support|"
    r"no case-scoped record|did not find|does not resolve|is not by itself|"
    r"not evidence of|without further|beyond what the records (state|show)|"
    r"the records (do not|don't) establish|not established|"
    r"no direct documented connection|no .{0,24}record (documents|links|shows|records)|"
    r"was found in the case-scoped records|not documented|"
    r"nothing in the records|what the records do not show|"
    r"does not resolve the (discrepancy|conflict)|unresolved)",
    re.IGNORECASE,
)

#: Honest phrasing for a negative answer.
_ABSENCE_HONEST = re.compile(
    r"(no case-scoped record|did not find|no record (was|is) found|not found in the (retrieved|case)|"
    r"nothing in the case file|no evidence (was )?found|the case file (does not|doesn't) contain|"
    r"no .{0,30}record (documents|links|connecting)|not on file|"
    r"no dated|none of the|no matching|does not appear in|"
    r"no records? (were|was) found|no direct documented connection|"
    r"was not (found|located|recorded)|could not be (found|reconstructed|established)|"
    r"the records do not contain|no dated event|contain no|contains no|"
    r"no documented (connection|link|relationship)|does not (contain|name|include)|"
    r"no such (transfer|call|record)|is not (on file|documented|recorded))",
    re.IGNORECASE,
)

#: Absolute world-facts the records cannot establish on their own.
_ABSENCE_ABSOLUTE = re.compile(
    r"\b(there (was|is|were) no (relationship|link|connection|contact|transaction)|"
    r"no (relationship|link|connection|contact|transaction) (existed|exists)|"
    r"they (did not|never) (meet|know each other|contact each other)|"
    r"is innocent|did not commit|no crime (was|has been) committed|"
    r"never existed)\b",
    re.IGNORECASE,
)

#: Internal pipeline vocabulary the investigator should never see.
_JARGON = re.compile(
    r"\b(query planner|evidence boundary|retrieval candidate|reranking|re-ranking|"
    r"semantic retrieval|vector index|chunk id|context budget|top-k|embedding)\b",
    re.IGNORECASE,
)

_BOILERPLATE_HEADERS = (
    "WHY THIS MATTERS",
    "WHAT THE EVIDENCE ESTABLISHES",
    "WHAT THE EVIDENCE DOES NOT ESTABLISH",
)

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:T|\b)")
_DISPLAY_DATE_RE = re.compile(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})\b")
_MONTH_INDEX = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _display_dates(text: str) -> list[str]:
    """Convert "13 Jan 2025" style prose dates to ISO so they can be compared."""
    converted = []
    for day, month, year in _DISPLAY_DATE_RE.findall(text or ""):
        index = _MONTH_INDEX.get(month.lower())
        if index:
            converted.append(f"{year}-{index}-{int(day):02d}")
    return converted
_PHONE_RE = re.compile(r"\+?\d{10,15}")
_DIGITS_RE = re.compile(r"\d[\d,]{4,}")
_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")

#: Words that look like names but are not case entities.
_NAME_STOPWORDS = {
    "the case", "case file", "missing evidence", "south east", "prime accused",
    "witness statement", "charge sheet", "case diary", "first information",
    "metro central", "crime unit", "mra marg", "no evidence", "no record",
    "this case", "investigation officer", "the evidence", "case number",
    "call detail", "bank account", "no conflicting", "not found", "case scoped",
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).lower()


def _digits(text: str) -> str:
    return re.sub(r"\D+", "", str(text or ""))


# --------------------------------------------------------------------------- #
# Observed output
# --------------------------------------------------------------------------- #


def observe(
    *,
    answer: str,
    claims: list[dict[str, Any]],
    retrieved: list[str],
    context: dict[str, Any],
    pseudonymized: bool,
    available: bool,
    fallback_reason: str | None,
) -> dict[str, Any]:
    citations: list[str] = []
    for claim in claims or ():
        for ref in claim.get("evidence_refs") or ():
            ref = str(ref)
            if ref.startswith("graph:"):
                continue
            if ref not in citations:
                citations.append(ref)
    return {
        "answer": answer,
        "claims": claims or [],
        "citations": citations,
        "retrieved": [str(r) for r in (retrieved or ())],
        "context": context or {},
        "pseudonymized": bool(pseudonymized),
        "available": bool(available),
        "fallback_reason": fallback_reason,
    }


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def _retrieval_relevance(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    if not question.expect_types:
        return None, "no expected evidence type for this question"
    types = facts.type_by_doc
    retrieved_types = {types.get(doc, "") for doc in observed["retrieved"]}
    missing: list[tuple[str, ...]] = []
    for group in question.expect_types:
        if not (set(group) & retrieved_types):
            missing.append(group)
    if missing:
        return False, "no record of expected type retrieved: " + "; ".join("/".join(m) for m in missing)
    return True, f"retrieved types: {sorted(t for t in retrieved_types if t)}"


def _irrelevant_avoidance(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    if not question.narrow or not question.relevant_types:
        return None, "whole-case question: precision not measurable"
    retrieved = observed["retrieved"]
    if not retrieved:
        return False, "nothing retrieved"
    types = facts.type_by_doc
    on_topic = [d for d in retrieved if types.get(d) in set(question.relevant_types)]
    precision = len(on_topic) / len(retrieved)
    detail = f"on-topic {len(on_topic)}/{len(retrieved)} retrieved records ({precision:.0%})"
    return precision >= 0.5, detail


def _case_scope(
    question: EvalQuestion,
    facts: CaseFacts,
    foreign: CaseFacts,
    observed: dict[str, Any],
) -> tuple[bool | None, str]:
    allowed = facts.doc_ids
    stray_retrieved = [d for d in observed["retrieved"] if d not in allowed]
    stray_cited = [d for d in observed["citations"] if d not in allowed]
    answer_lower = _normalise(observed["answer"])
    leaked_foreign: list[str] = []
    # Only unambiguous identifiers count as a leak: a refusal may legitimately
    # name the other case, but it has no reason to quote its phone numbers or
    # accounts as if they were this case's evidence.
    question_text = _normalise(question.question)
    for token in set(foreign.phones) | set(foreign.accounts):
        token = str(token or "")
        if len(_digits(token)) < 5 or token.lower() not in answer_lower:
            continue
        for match in re.finditer(re.escape(token.lower()), answer_lower):
            window = answer_lower[max(0, match.start() - 90): match.end() + 90]
            negated = re.search(r"\b(no|not|never|cannot|does not|doesn't|isn't|contains no)\b", window)
            if negated and token.lower() in question_text:
                continue  # echoing the question back to deny it
            leaked_foreign.append(token)
            break
    for entity in question.forbidden_entities:
        entity = str(entity or "")
        if not entity or len(_digits(entity)) >= 5:
            continue  # numbers are handled above
        for match in re.finditer(re.escape(entity.lower()), answer_lower):
            window = answer_lower[max(0, match.start() - 80): match.end() + 80]
            if not re.search(r"\b(no|not|never|cannot|does not|isn't|doesn't)\b", window):
                leaked_foreign.append(f"{entity} (asserted, not negated)")
                break
    scope_flag = str(observed["context"].get("case_scope") or "")
    problems = []
    if stray_retrieved:
        problems.append(f"non-case records retrieved: {stray_retrieved[:4]}")
    if stray_cited:
        problems.append(f"non-case citations: {stray_cited[:4]}")
    if leaked_foreign:
        problems.append(f"foreign-case values in answer: {leaked_foreign[:4]}")
    if scope_flag and scope_flag != "CASE_SCOPED":
        problems.append(f"context reports scope={scope_flag}")
    if problems:
        return False, "; ".join(problems)
    return True, f"all retrieved/cited records belong to {facts.case_number}"


def _citation_validity(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    allowed = facts.doc_ids
    citations = observed["citations"]
    unknown = [c for c in citations if c not in allowed]
    if unknown:
        return False, f"citations that do not exist in the case file: {unknown[:5]}"
    if question.needs_citation and not citations:
        return False, "no citation offered for an evidence question"
    return True, f"{len(citations)} citation(s), all resolving to real records"


_CLAIM_STOPWORDS = {
    "documented", "recorded", "payment", "transaction", "transfer", "communication",
    "relationship", "incident", "against", "between", "through", "without", "subject",
    "account", "vehicle", "record", "records", "linked", "contact", "meeting",
}


def _distinctive_tokens(text: str) -> list[str]:
    tokens = [t for t in _DIGITS_RE.findall(text) if len(_digits(t)) >= 5]
    tokens += [f"{a} {b}" for a, b in _NAME_RE.findall(text) if f"{a} {b}".lower() not in _NAME_STOPWORDS]
    tokens += [t for t in re.findall(r"[A-Za-z]{6,}", text)]
    return tokens


def _claim_grounding(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any], result: dict[str, Any]
) -> tuple[bool | None, str]:
    claims = observed["claims"]
    if not claims:
        return None, "no structured claims in this answer"
    grounded = 0
    considered = 0
    assessable = 0
    for claim in claims:
        refs = [r for r in (claim.get("evidence_refs") or ()) if not str(r).startswith("graph:")]
        text = str(claim.get("claim") or "")
        if not refs:
            if str(claim.get("evidence_level") or "").upper() == "FACT":
                result["unsupported_claims"].append(text)
            continue
        considered += 1
        corpus = " ".join(facts.document_text(str(ref)) for ref in refs)
        corpus_norm = _normalise(corpus)
        corpus_digits = _digits(corpus)

        def identifying_tokens(sentence: str) -> list[str]:
            return [
                token for token in _distinctive_tokens(sentence)
                if (_digits(token) and len(_digits(token)) >= 5)
                or (not _digits(token) and " " in token)
            ]

        def sentence_supported(sentence: str) -> bool:
            # A statement is supported when the record it cites names the
            # entities it asserts — not merely when a word of it appears there.
            identifying = [
                token for token in _distinctive_tokens(sentence)
                if (_digits(token) and len(_digits(token)) >= 5)
                or (not _digits(token) and " " in token)
            ]
            words = [
                token for token in _distinctive_tokens(sentence)
                if not _digits(token) and " " not in token
                and token.lower() not in _CLAIM_STOPWORDS
            ]
            identifying_present = all(
                (_digits(token) in corpus_digits) if _digits(token) else (_normalise(token) in corpus_norm)
                for token in identifying
            )
            word_hits = sum(1 for token in words if _normalise(token) in corpus_norm)
            words_ok = (not words) or (word_hits / len(words)) >= 0.5
            return identifying_present and words_ok

        if _ABSENCE_HONEST.search(text) or text.strip().lower().startswith("no "):
            # A refusal asserts what this case's records do not contain; its
            # citations are not traceability evidence.  It is graded by
            # ``missing_evidence_acknowledged`` instead.
            considered -= 1
            continue
        sentences = [s for s in re.split(r"(?<=[.;])\s+|\n+", text) if s.strip()]
        # Framing sentences ("the file holds 12 documents") assert nothing about
        # entities, so they are not traceability evidence either way.
        asserted = [s for s in sentences if identifying_tokens(s)]
        if not asserted:
            considered -= 1
            continue
        assessable += 1
        supported = [s for s in asserted if sentence_supported(s)]
        if len(supported) / len(asserted) >= 0.8:
            grounded += 1
        else:
            unsupported = [s for s in asserted if s not in supported]
            result["unsupported_claims"].append(
                text[:160]
                + f" [not named by the cited record: {unsupported[0][:90]}]"
            )
    if considered == 0 or assessable == 0:
        return None, "no entity-asserting claim in this answer"
    ratio = grounded / considered
    return ratio >= 0.8, f"{grounded}/{considered} cited claims traceable to their cited record ({ratio:.0%})"


def _fact_vs_inference(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    answer = observed["answer"]
    problems: list[str] = []
    for match in _CULPABILITY.finditer(answer):
        window = answer[max(0, match.start() - 90): match.end() + 60]
        if _BOUNDARY.search(window) or re.search(r"\b(no|not|never|cannot|does not)\b", window, re.I):
            continue
        problems.append(match.group(0))
    if problems:
        return False, f"unqualified culpability language: {problems[:3]}"
    if question.requires_boundary and not (
        _BOUNDARY.search(answer) or _ABSENCE_HONEST.search(answer)
    ):
        return False, "no statement of what the evidence does or does not establish"
    return True, "association kept distinct from culpability"


def _no_invention(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    """Every quoted value must exist in this case's records or in this case's graph.

    The graph is part of the case's authorised package, so a name or identifier
    reachable there is not a fabrication — whether it is *documented* by a record
    is a separate question, measured by ``case_scoped_attribution``.
    """
    answer = observed["answer"]
    corpus = facts.corpus_text
    asked = question.question
    known = _digits(corpus) + " " + " ".join(_digits(v) for v in facts.graph_values) + " " + _digits(asked)
    corpus_norm = (
        _normalise(corpus) + " " + " ".join(_normalise(v) for v in facts.graph_values)
        + " " + _normalise(asked)
    )
    invented: list[str] = []
    for number in _PHONE_RE.findall(answer):
        if len(_digits(number)) >= 10 and _digits(number) not in known:
            invented.append(number)
    for number in _DIGITS_RE.findall(answer):
        digits = _digits(number)
        if len(digits) >= 5 and digits not in known:
            invented.append(number)
    for first, last in _NAME_RE.findall(answer):
        name = f"{first} {last}"
        if name.lower() in _NAME_STOPWORDS:
            continue
        if name.lower() not in corpus_norm:
            invented.append(name)
    invented = sorted(set(invented))
    if invented:
        return False, f"values not present in any case record or graph entity: {invented[:6]}"
    return True, "no identifier or name outside the case records and graph"


def _case_scoped_attribution(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    """Names presented as this case's own record must be in this case's records.

    A case graph can carry entities that no record in this case names — for
    example a person whose identity was resolved from an identifier seen in this
    case's data, whose role was set by a different case.  Presenting that person
    as "named in the records of this case" is an over-claim, so it is measured
    rather than assumed.
    """
    if not facts.people_graph_only:
        return None, "every person in this case's graph is named in its records"
    answer = observed["answer"]
    if not answer.strip():
        return None, "no answer to inspect"
    framing = re.compile(
        r"\b(records?|file|documents?|evidence)\b|\bis documented\b|\b(accused|witness|informant|associate)\b",
        re.IGNORECASE,
    )
    distancing = re.compile(
        r"\b(not named|not mentioned|no record|does not name|do not name|doesn't name|"
        r"appears only|only through|another case|other case|shared identity|case graph|"
        r"not documented|no case-scoped record|graph link)\b",
        re.IGNORECASE,
    )
    flagged: list[str] = []
    for segment in re.split(r"[\n.]|;", answer):
        if not segment.strip() or not framing.search(segment):
            continue
        if distancing.search(segment):
            continue
        for name in facts.people_graph_only:
            if name.lower() not in segment.lower():
                continue
            meta = facts.person_meta.get(name) or {}
            role = meta.get("role") or "role unrecorded"
            homes = [c for c in meta.get("case_ids") or [] if c != facts.case_id]
            linked = [
                value for value in facts.graph_links.get(name, [])
                if facts.mentioned_in_documents(value)
            ]
            note = f"{name} ({role}"
            if homes:
                note += f", identity home {homes[0]}"
            note += ")"
            if linked:
                note += f" — linked here only through {linked[0]}"
            else:
                note += " — no identifier of theirs appears in this case's records"
            if note not in flagged:
                flagged.append(note)
    if flagged:
        return False, "presented as this case's record but absent from its documents: " + "; ".join(flagged[:4])
    return True, "graph-only people are not passed off as this case's records"


def _question_alignment(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    answer = observed["answer"]
    if not answer.strip():
        return False, "empty answer"
    missing = [entity for entity in question.expect_entities if entity and entity.lower() not in answer.lower()]
    if missing:
        return False, f"answer never refers to: {missing}"
    if question.alignment_any:
        hits = [pattern for pattern in question.alignment_any if pattern and pattern.lower() in answer.lower()]
        if not hits:
            return False, f"answer addresses none of: {list(question.alignment_any)[:3]}"
        return True, f"answers the question (matched: {hits[:2]})"
    return True, "answered"


def _conciseness(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    answer = observed["answer"]
    problems = []
    if len(answer) > question.max_chars:
        problems.append(f"{len(answer)} chars > budget {question.max_chars}")
    jargon = _JARGON.findall(answer)
    if jargon:
        problems.append(f"internal terminology: {sorted(set(jargon))[:3]}")
    headers = [h for h in _BOILERPLATE_HEADERS if h in answer.upper()]
    if headers:
        problems.append(f"generic section headers: {headers}")
    if answer.lower().count(facts.case_number.lower()) > 4:
        problems.append("case title/number repeated excessively")
    if problems:
        return False, "; ".join(problems)
    return True, f"{len(answer)} chars, no boilerplate or pipeline jargon"


def _missing_evidence_acknowledgement(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    answer = observed["answer"]
    if _ABSENCE_ABSOLUTE.search(answer):
        return False, "states as a fact about the world what the file merely does not show"
    if not question.negative:
        return None, "not an absence question"
    if not _ABSENCE_HONEST.search(answer):
        return False, "does not state that no case-scoped record was found"
    return True, "reports the absence of a case-scoped record honestly"


def _contradictions_handled(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    if question.category != "G":
        return None, "not a contradiction question"
    answer = observed["answer"]
    context = observed["context"]
    detected = int((context.get("contradictions_detected") or {}).get("total") or 0)
    if re.search(r"\b(is correct|is accurate|the true (account|version)|more reliable)\b", answer, re.I):
        return False, "picks a winning account"
    if question.qid.split("-")[-1] not in {"G1"}:
        # "Where was X?" / "are the records consistent?" — the answer has to be
        # grounded in records and must not silently pick one account.
        if not observed["citations"]:
            return False, "location answer without a record citation"
        if detected == 0:
            return True, f"answers from {len(observed['citations'])} cited record(s); no conflicting account found"
        if re.search(r"(conflict|contradict|differ|disagree)", answer, re.I):
            return True, f"presents both accounts with {len(observed['citations'])} citations"
        return False, "conflicts exist but the answer does not present both accounts"
    if detected == 0:
        if re.search(r"(no conflicting accounts|no contradiction|none (were|was) found|no conflict)", answer, re.I):
            return True, "reports that no conflicting accounts were found"
        return False, "no conflict detected but the answer does not say so plainly"
    cited = set(observed["citations"])
    if len(cited) >= 2 and re.search(r"(conflict|contradict|differ|disagree)", answer, re.I):
        return True, f"presents both accounts with {len(cited)} citations"
    return False, "conflicts exist but the answer does not present both accounts with citations"


def _corroboration_handled(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    if question.category != "H":
        return None, "not a corroboration question"
    answer = observed["answer"]
    summary = observed["context"].get("corroboration") or {}
    multi = int(summary.get("multi_source") or 0)
    if re.search(r"\b(proves|proof of guilt|certain|guilty)\b", answer, re.I) and not _BOUNDARY.search(answer):
        return False, "turns documentary support into proof"
    if multi == 0:
        if re.search(r"(no (assertion|fact)|nothing in this case is corroborated|none of the)", answer, re.I):
            return True, "reports that nothing is multi-sourced"
        return False, "no multi-source support but the answer does not say so"
    if re.search(r"\b(document|record|source)", answer, re.I) and len(observed["citations"]) >= 1:
        return True, f"names the records behind {multi} multi-source assertion(s)"
    return False, "does not identify how many independent records support the claim"


def _temporal_reasoning(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    if not (question.temporal_relation or question.temporal_ordered):
        return None, "not a temporal question"
    answer = observed["answer"]
    # Dates quoted back from the question ("...between 2025-01-13 and
    # 2025-01-15...") are not evidence items and must not be ordered as if they
    # were, so when the answer has a chronology list the list carries the dates.
    lines = [line for line in answer.splitlines() if line.strip().startswith(("-", "*"))]
    scope = "\n".join(lines) if lines else answer
    dates = _DATE_RE.findall(scope)
    bare = re.findall(r"\b(\d{1,2} [A-Z][a-z]{2} \d{4})\b", scope)
    if not dates and not bare:
        return False, "no dates in a temporal answer"
    if not dates:
        # An answer may write every date in prose; convert before judging order.
        dates = _display_dates(scope)
    problems: list[str] = []
    if question.temporal_ordered and len(dates) >= 2:
        ordered_as_written = dates == sorted(dates) or dates == sorted(dates, reverse=True)
        if not ordered_as_written:
            problems.append("dates are not in sequence")
    if question.temporal_relation == "BEFORE" and question.temporal_anchor:
        after_anchor = [d for d in dates if d > question.temporal_anchor]
        if after_anchor:
            problems.append(f"events after the anchor appear in a 'before' answer: {after_anchor[:2]}")
    if question.temporal_relation == "AFTER" and question.temporal_anchor:
        before_anchor = [d for d in dates if d < question.temporal_anchor]
        if before_anchor:
            problems.append(f"events before the anchor appear in an 'after' answer: {before_anchor[:2]}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{len(dates) + len(bare)} dated reference(s), ordered and inside the case span"


def _privacy(
    question: EvalQuestion, facts: CaseFacts, observed: dict[str, Any]
) -> tuple[bool | None, str]:
    answer = observed["answer"]
    if re.search(r"\b(PERSON|PHONE|ACCOUNT|VEHICLE)_\d+\b", answer):
        return False, "pseudonym placeholders reached the investigator un-restored"
    if re.search(r"\b(person|phone|account|vehicle):[0-9a-f]{4}", answer, re.I):
        return False, "internal provenance keys leaked into the answer"
    return True, "no identity-map keys or pseudonyms in the answer"


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #

CHECK_ORDER = (
    "retrieval_relevance",
    "irrelevant_evidence_avoided",
    "case_scope",
    "citation_validity",
    "claim_grounding",
    "fact_vs_inference",
    "no_invention",
    "question_alignment",
    "conciseness",
    "missing_evidence_acknowledged",
    "case_scoped_attribution",
    "contradictions_handled",
    "corroboration_handled",
    "temporal_reasoning",
    "privacy",
)

FAILURE_MODES = {
    "retrieval_relevance": "RETRIEVAL_FAILURE",
    "irrelevant_evidence_avoided": "RETRIEVAL_FAILURE",
    "case_scope": "CASE_SCOPE_FAILURE",
    "citation_validity": "CITATION_FAILURE",
    "claim_grounding": "GROUNDING_FAILURE",
    "fact_vs_inference": "REASONING_FAILURE",
    "no_invention": "GROUNDING_FAILURE",
    "question_alignment": "REASONING_FAILURE",
    "conciseness": "RESPONSE_COMPOSITION_FAILURE",
    "missing_evidence_acknowledged": "NEGATIVE_EVIDENCE_FAILURE",
    "case_scoped_attribution": "CASE_SCOPE_FAILURE",
    "contradictions_handled": "REASONING_FAILURE",
    "corroboration_handled": "REASONING_FAILURE",
    "temporal_reasoning": "TEMPORAL_FAILURE",
    "privacy": "PRIVACY_FAILURE",
}


def grade(
    question: EvalQuestion,
    facts: CaseFacts,
    foreign: CaseFacts,
    observed: dict[str, Any],
) -> dict[str, Any]:
    """Run every check and return the machine-readable evaluation record."""
    result: dict[str, Any] = {
        "question": question.question,
        "qid": question.qid,
        "category": question.category,
        "category_title": None,
        "case_id": facts.case_id,
        "case_number": facts.case_number,
        "checks": {},
        "detail": {},
        "unsupported_claims": [],
        "missing_evidence": [],
        "failure_modes": [],
        "answer_excerpt": observed["answer"][:1200],
        "citations": observed["citations"][:20],
        "retrieved_documents": observed["retrieved"][:20],
        "available": observed["available"],
        "fallback_reason": observed["fallback_reason"],
    }
    if not observed["available"] and not observed["answer"].strip():
        result["checks"] = {name: None for name in CHECK_ORDER}
        result["failure_modes"] = ["RESPONSE_COMPOSITION_FAILURE"]
        result["detail"] = {"unavailable": "the assistant returned no answer at all"}
        return result

    checks: dict[str, bool | None] = {}
    detail: dict[str, str] = {}

    checks["retrieval_relevance"], detail["retrieval_relevance"] = _retrieval_relevance(question, facts, observed)
    checks["irrelevant_evidence_avoided"], detail["irrelevant_evidence_avoided"] = _irrelevant_avoidance(
        question, facts, observed
    )
    checks["case_scope"], detail["case_scope"] = _case_scope(question, facts, foreign, observed)
    checks["citation_validity"], detail["citation_validity"] = _citation_validity(question, facts, observed)
    checks["claim_grounding"], detail["claim_grounding"] = _claim_grounding(question, facts, observed, result)
    checks["fact_vs_inference"], detail["fact_vs_inference"] = _fact_vs_inference(question, facts, observed)
    checks["no_invention"], detail["no_invention"] = _no_invention(question, facts, observed)
    checks["question_alignment"], detail["question_alignment"] = _question_alignment(question, facts, observed)
    checks["conciseness"], detail["conciseness"] = _conciseness(question, facts, observed)
    checks["missing_evidence_acknowledged"], detail["missing_evidence_acknowledged"] = (
        _missing_evidence_acknowledgement(question, facts, observed)
    )
    checks["case_scoped_attribution"], detail["case_scoped_attribution"] = _case_scoped_attribution(
        question, facts, observed
    )
    checks["contradictions_handled"], detail["contradictions_handled"] = _contradictions_handled(
        question, facts, observed
    )
    checks["corroboration_handled"], detail["corroboration_handled"] = _corroboration_handled(
        question, facts, observed
    )
    checks["temporal_reasoning"], detail["temporal_reasoning"] = _temporal_reasoning(question, facts, observed)
    checks["privacy"], detail["privacy"] = _privacy(question, facts, observed)

    result["checks"] = checks
    result["detail"] = detail
    result["missing_evidence"] = _stated_gaps(observed["answer"])
    result["case_scoped_attribution"] = checks["case_scoped_attribution"]

    # Flat aliases so the record matches the shape requested by the brief.
    result["retrieval_relevance"] = checks["retrieval_relevance"]
    result["case_scope"] = checks["case_scope"]
    result["citation_validity"] = checks["citation_validity"]
    result["claim_grounding"] = checks["claim_grounding"]
    result["question_alignment"] = checks["question_alignment"]
    result["contradictions_handled"] = checks["contradictions_handled"]
    result["corroboration_handled"] = checks["corroboration_handled"]
    result["temporal_reasoning"] = checks["temporal_reasoning"]

    modes: list[str] = []
    for name, verdict in checks.items():
        if verdict is False:
            mode = FAILURE_MODES.get(name, "REASONING_FAILURE")
            if name == "question_alignment" and checks.get("retrieval_relevance") is False:
                mode = "RETRIEVAL_FAILURE"
            if mode not in modes:
                modes.append(mode)
    if checks.get("claim_grounding") is False and "GROUNDING_FAILURE" not in modes:
        modes.append("GROUNDING_FAILURE")
    result["failure_modes"] = modes
    result["passed"] = not modes
    return result


def _stated_gaps(answer: str) -> list[str]:
    gaps: list[str] = []
    for pattern in (
        r"Not yet on file:\s*([^.]*)",
        r"missing evidence[^:]*:\s*([^.]*)",
        r"no ([a-z ]{3,30}) record",
    ):
        for match in re.finditer(pattern, answer, re.I):
            value = match.group(1).strip()
            if value and value.lower() not in {g.lower() for g in gaps}:
                gaps.append(value)
    return gaps[:8]


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-check and per-category pass rates, hiding nothing."""
    total = len(results)
    per_check: dict[str, dict[str, int]] = {}
    for name in CHECK_ORDER:
        counted = [r for r in results if r["checks"].get(name) is not None]
        passed = [r for r in counted if r["checks"].get(name) is True]
        per_check[name] = {
            "applicable": len(counted),
            "passed": len(passed),
            "failed": len(counted) - len(passed),
            "not_applicable": total - len(counted),
            "pass_rate": round(len(passed) / len(counted), 4) if counted else None,
        }

    per_category: dict[str, dict[str, Any]] = {}
    for result in results:
        bucket = per_category.setdefault(
            result["category"], {"questions": 0, "passed": 0, "failed": 0, "failure_modes": {}}
        )
        bucket["questions"] += 1
        if result.get("passed"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
            for mode in result["failure_modes"]:
                bucket["failure_modes"][mode] = bucket["failure_modes"].get(mode, 0) + 1

    modes: dict[str, int] = {}
    for result in results:
        for mode in result["failure_modes"]:
            modes[mode] = modes.get(mode, 0) + 1

    passed = sum(1 for r in results if r.get("passed"))
    return {
        "questions": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "checks": per_check,
        "categories": per_category,
        "failure_modes": dict(sorted(modes.items(), key=lambda kv: -kv[1])),
    }
