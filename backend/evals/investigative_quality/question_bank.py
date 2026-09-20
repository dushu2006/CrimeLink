"""The evaluation question bank — twelve categories, built from case facts.

Questions are generated *from the fact pack* rather than hand-written per case,
so the same bank applies to every case in the demo corpus and a question can
never accidentally reference a person the case does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evals.investigative_quality.case_facts import CaseFacts

# Categories from the brief.
CATEGORY_TITLES = {
    "A": "Direct fact questions",
    "B": "Entity / relationship questions",
    "C": "Multi-hop questions",
    "D": "Temporal questions",
    "E": "Financial questions",
    "F": "Communication questions",
    "G": "Contradiction questions",
    "H": "Corroboration questions",
    "I": "Negative / absence questions",
    "J": "Cross-case security questions",
    "K": "Ambiguous questions",
    "L": "Complex investigative questions",
}


@dataclass
class EvalQuestion:
    """One question plus what a correct answer has to look like."""

    qid: str
    category: str
    question: str
    case_number: str
    #: Groups of document types; each group must contribute at least one
    #: retrieved record for retrieval relevance to pass.
    expect_types: list[tuple[str, ...]] = field(default_factory=list)
    #: Types considered on-topic for this question (precision denominator).
    relevant_types: tuple[str, ...] = ()
    narrow: bool = True
    needs_citation: bool = True
    requires_boundary: bool = False
    negative: bool = False
    #: At least one of these must appear in the answer (case-insensitive).
    alignment_any: tuple[str, ...] = ()
    #: All of these must appear (used for multi-hop chain entities).
    expect_entities: tuple[str, ...] = ()
    #: For temporal questions: relation, the anchor date, and whether the
    #: dates in the answer must be in ascending order.
    temporal_relation: str | None = None
    temporal_anchor: str | None = None
    temporal_ordered: bool = False
    max_chars: int = 2600
    #: Names that must never appear (cross-case contamination probes).
    forbidden_entities: tuple[str, ...] = ()
    notes: str = ""


ALL_TYPES = (
    "FIR", "CDR", "SURVEILLANCE_REPORT", "FINANCIAL", "WITNESS_STATEMENT",
    "INTELLIGENCE_REPORT", "SCENE_REPORT", "ANPR", "FORENSIC", "CASE_DIARY",
    "CHARGE_SHEET",
)


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _human_date(iso: str | None) -> tuple[str, str]:
    """(day-month-year, month-year) the way answer prose writes a date."""
    if not iso or len(iso) < 10:
        return "", ""
    try:
        year, month, day = iso[:10].split("-")
        label = f"{int(day)} {_MONTHS[int(month) - 1]} {year}"
        return label, f"{_MONTHS[int(month) - 1]} {year}"
    except Exception:
        return iso, iso


def _co_mentions(facts: CaseFacts, name: str) -> list[str]:
    docs = [
        str(document.get("doc_id"))
        for document in facts.documents
        if name and name.lower() in str(document.get("content") or "").lower()
    ]
    return docs


def _person_not_co_mentioned(facts: CaseFacts, primary: str) -> str | None:
    primary_docs = set(_co_mentions(facts, primary))
    for person in facts.people:
        if person.lower() == primary.lower():
            continue
        if not set(_co_mentions(facts, person)) & primary_docs:
            return person
    return None


def build_questions(
    facts: CaseFacts,
    *,
    foreign: CaseFacts,
    shared: list[str] | None = None,
) -> list[EvalQuestion]:
    """Every question for one case, in the twelve categories."""
    people = [p for p in facts.people if p.lower() != "unconfirmed"]
    primary = (facts.leads[0] if facts.leads else None) or (
        facts.accused[0] if facts.accused else None
    ) or (people[0] if people else "the accused")
    secondary = next((p for p in facts.accused if p.lower() != primary.lower()), None) or next(
        (p for p in people if p.lower() != primary.lower()), primary
    )
    witness = next(
        (w for w in facts.witnesses if w.lower() != primary.lower()), None
    ) or next((p for p in people if p.lower() not in {primary.lower(), secondary.lower()}), secondary)
    outsider = (
        _person_not_co_mentioned(facts, primary)
        or next((p for p in people if p.lower() not in {primary.lower(), secondary.lower()}), None)
        or primary
    )

    phone = facts.phones[0] if facts.phones else "+91 unknown"
    account = facts.accounts[0] if facts.accounts else "the recorded account"
    vehicle = facts.vehicles[0] if facts.vehicles else "the recorded vehicle"
    location = facts.locations[0] if facts.locations else "the recorded location"
    incident = facts.incident_date or (facts.dates[0] if facts.dates else "2025-01-01")
    fir_date = facts.fir_date or incident
    window_end = fir_date
    window_start = incident

    # Cross-case probes must be values this case's records *and* graph never
    # touch; an identifier shared through a master identity is not a foreign
    # probe, it is a linkage question (measured separately as J5).
    pool = facts.foreign_to(foreign)
    pool_people = [v for v in pool if v in (foreign.people or [])]
    pool_phones = [v for v in pool if v in (foreign.phones or [])]
    pool_accounts = [v for v in pool if v in (foreign.accounts or [])]
    # A name shared between cases is *not* a foreign probe — it is the
    # shared-identity linkage question (J5).  Probes come from the pool of
    # values this case's records and graph never touch.
    foreign_person = (
        pool_people[0] if pool_people
        else (foreign.accused[0] if foreign.accused else (foreign.people[0] if foreign.people else "the suspect"))
    )
    foreign_unique = next(
        (p for p in pool_people if p.lower() not in {n.lower() for n in people}),
        foreign_person,
    )
    foreign_phone = pool_phones[0] if pool_phones else (foreign.phones[0] if foreign.phones else "+91 unknown")
    foreign_account = (
        pool_accounts[0] if pool_accounts else (foreign.accounts[0] if foreign.accounts else "an account")
    )
    foreign_vehicle = foreign.vehicles[0] if foreign.vehicles else "a vehicle"

    def q(
        qid: str,
        category: str,
        question: str,
        **kwargs: Any,
    ) -> EvalQuestion:
        return EvalQuestion(
            qid=f"{facts.case_number}-{qid}",
            category=category,
            question=question,
            case_number=facts.case_number,
            **kwargs,
        )

    questions: list[EvalQuestion] = []

    # ---------------------------------------------------------------- A facts
    questions += [
        q("A1", "A", "What is this case about, and what is its current status?",
          expect_types=[("FIR", "CHARGE_SHEET", "CASE_DIARY")], relevant_types=ALL_TYPES,
          narrow=False, alignment_any=(facts.case_number, "case")),
        q("A2", "A", "What was the date of the incident, and when was the FIR registered?",
          expect_types=[("FIR", "CASE_DIARY")], relevant_types=("FIR", "CASE_DIARY", "SCENE_REPORT"),
          alignment_any=(_human_date(incident)[0], _human_date(incident)[1], "registered")),
        q("A3", "A", "Who is the investigating officer for this case?",
          expect_types=[("FIR", "CASE_DIARY", "CHARGE_SHEET")],
          relevant_types=("FIR", "CASE_DIARY", "CHARGE_SHEET"),
          alignment_any=("inspector", "officer", "IO")),
        q("A4", "A", "What type of offence is recorded in this case?",
          expect_types=[("FIR", "CHARGE_SHEET")], relevant_types=("FIR", "CHARGE_SHEET", "CASE_DIARY"),
          alignment_any=(str(facts.crime_type or "").lower().replace("_", " ")[:12], "offence", "crime")),
    ]

    # --------------------------------------------------- B entity / relationship
    questions += [
        q("B1", "B", "Who are the people named in this case?",
          expect_types=[("FIR", "CHARGE_SHEET", "WITNESS_STATEMENT", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, alignment_any=(primary,)),
        q("B2", "B", f"How are {primary} and {secondary} connected in this case?",
          expect_types=[("FIR", "CHARGE_SHEET", "WITNESS_STATEMENT", "CDR", "FINANCIAL")],
          relevant_types=ALL_TYPES, narrow=False,
          alignment_any=(primary, secondary)),
        q("B3", "B", "What vehicle numbers appear in the records of this case?",
          expect_types=[("ANPR", "SURVEILLANCE_REPORT")],
          relevant_types=("ANPR", "SURVEILLANCE_REPORT", "INTELLIGENCE_REPORT", "FIR"),
          alignment_any=(vehicle.upper(), vehicle, "vehicle", "no vehicle")),
        q("B4", "B", "Which phone numbers appear in the evidence for this case?",
          expect_types=[("CDR", "INTELLIGENCE_REPORT", "FIR")],
          relevant_types=("CDR", "INTELLIGENCE_REPORT", "FIR", "SURVEILLANCE_REPORT", "WITNESS_STATEMENT"),
          alignment_any=(phone[-6:], phone, "phone", "no phone")),
    ]

    # ---------------------------------------------------------- C multi-hop
    questions += [
        q("C1", "C", f"Which people are connected to {primary} through the same phone number?",
          expect_types=[("CDR", "INTELLIGENCE_REPORT", "FIR")],
          relevant_types=("CDR", "INTELLIGENCE_REPORT", "FIR", "WITNESS_STATEMENT", "CHARGE_SHEET"),
          expect_entities=(primary, phone[-6:]), alignment_any=(primary,)),
        q("C2", "C",
          f"Which account is connected to the person who communicated with {primary} before the incident?",
          expect_types=[("CDR",), ("FINANCIAL", "FIR", "CHARGE_SHEET", "SURVEILLANCE_REPORT")],
          relevant_types=("CDR", "FINANCIAL", "FIR", "CHARGE_SHEET", "INTELLIGENCE_REPORT"),
          expect_entities=(primary,), alignment_any=(primary,)),
        q("C3", "C", "Which vehicle is associated with a person who also appears in the financial records?",
          expect_types=[("ANPR", "SURVEILLANCE_REPORT"), ("FINANCIAL",)],
          relevant_types=("ANPR", "SURVEILLANCE_REPORT", "FINANCIAL", "CHARGE_SHEET", "FIR"),
          alignment_any=("vehicle", vehicle.upper(), "no vehicle", "not")),
        q("C4", "C", "Which evidence connects the person, the phone number and the location in this case?",
          expect_types=[("CDR", "SURVEILLANCE_REPORT", "ANPR", "SCENE_REPORT")],
          relevant_types=("CDR", "SURVEILLANCE_REPORT", "ANPR", "SCENE_REPORT", "FIR", "CASE_DIARY", "CHARGE_SHEET"),
          alignment_any=("phone", location.split(",")[0], "location", primary)),
    ]

    # ----------------------------------------------------------- D temporal
    questions += [
        q("D1", "D", "What happened immediately before the incident?",
          expect_types=[("CASE_DIARY", "SCENE_REPORT", "FIR", "SURVEILLANCE_REPORT", "CDR")],
          relevant_types=ALL_TYPES, narrow=False,
          temporal_relation="BEFORE", temporal_anchor=incident, temporal_ordered=True,
          alignment_any=("before", "incident")),
        q("D2", "D", "What communications occurred within six hours of the incident?",
          expect_types=[("CDR", "SURVEILLANCE_REPORT", "CASE_DIARY")],
          relevant_types=("CDR", "SURVEILLANCE_REPORT", "CASE_DIARY", "FIR", "SCENE_REPORT", "ANPR"),
          temporal_relation="AROUND", temporal_anchor=incident, temporal_ordered=True,
          alignment_any=("call", "communit", "no ", "hour")),
        q("D3", "D", f"What happened between {window_start} and {window_end}?",
          expect_types=[("CASE_DIARY", "FIR", "SCENE_REPORT", "WITNESS_STATEMENT")],
          relevant_types=ALL_TYPES, narrow=False,
          temporal_relation="BETWEEN", temporal_anchor=window_start, temporal_ordered=True,
          alignment_any=(window_start[:7], "between")),
        q("D4", "D", f"What was the sequence of events involving {primary}?",
          expect_types=[("CDR", "FINANCIAL", "FIR", "CHARGE_SHEET", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, temporal_ordered=True,
          alignment_any=(primary,)),
        q("D5", "D", "What happened after the incident?",
          expect_types=[("CASE_DIARY", "CHARGE_SHEET", "WITNESS_STATEMENT", "CDR", "FINANCIAL")],
          relevant_types=ALL_TYPES, narrow=False,
          temporal_relation="AFTER", temporal_anchor=incident, temporal_ordered=True,
          alignment_any=("after",)),
    ]

    # ---------------------------------------------------------- E financial
    questions += [
        q("E1", "E", "What financial transactions are recorded in this case?",
          expect_types=[("FINANCIAL",)],
          relevant_types=("FINANCIAL", "CHARGE_SHEET", "INTELLIGENCE_REPORT", "FIR"),
          alignment_any=("account", "transfer", "₹", "amount", "no financial")),
        q("E2", "E", "Which bank accounts appear in the financial evidence?",
          expect_types=[("FINANCIAL",)],
          relevant_types=("FINANCIAL", "CHARGE_SHEET", "INTELLIGENCE_REPORT"),
          alignment_any=(account[-6:], account, "account", "no account")),
        q("E3", "E", f"Are there any financial transfers between {primary} and {secondary}?",
          expect_types=[("FINANCIAL", "FIR", "CHARGE_SHEET")],
          relevant_types=("FINANCIAL", "FIR", "CHARGE_SHEET", "CDR", "INTELLIGENCE_REPORT"),
          requires_boundary=True, negative=True,
          alignment_any=(primary, secondary, "transfer", "transaction")),
    ]

    # ------------------------------------------------------- F communication
    questions += [
        q("F1", "F", f"What communications are recorded between {primary} and {secondary}?",
          expect_types=[("CDR", "FIR", "CASE_DIARY", "CHARGE_SHEET")],
          relevant_types=("CDR", "FIR", "CASE_DIARY", "CHARGE_SHEET", "WITNESS_STATEMENT", "INTELLIGENCE_REPORT"),
          alignment_any=(primary, secondary, "call", "no ")),
        q("F2", "F", "Which phone numbers appear in the call detail records?",
          expect_types=[("CDR",)],
          relevant_types=("CDR", "INTELLIGENCE_REPORT", "FIR"),
          alignment_any=(phone[-6:], phone, "number", "no call")),
        q("F3", "F", "Were there any calls around the time of the incident?",
          expect_types=[("CDR", "CASE_DIARY")],
          relevant_types=("CDR", "CASE_DIARY", "SURVEILLANCE_REPORT", "FIR", "SCENE_REPORT"),
          temporal_relation="AROUND", temporal_anchor=incident,
          alignment_any=("call", "communit", "no ", "hour")),
    ]

    # ------------------------------------------------------ G contradiction
    questions += [
        q("G1", "G", "Are there any contradictions in the evidence of this case?",
          expect_types=[("FIR", "CASE_DIARY", "WITNESS_STATEMENT")], relevant_types=ALL_TYPES,
          narrow=False, alignment_any=("contradict", "conflict", "discrepan", "no conflicting",
                                       "difference")),
        q("G2", "G", f"Where was {primary} at the time of the incident?",
          expect_types=[("FIR", "WITNESS_STATEMENT", "SURVEILLANCE_REPORT", "SCENE_REPORT", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, requires_boundary=True,
          alignment_any=(primary, "record", "no ")),
        q("G3", "G", "Are the records consistent about where the incident took place?",
          expect_types=[("SCENE_REPORT", "FIR", "WITNESS_STATEMENT", "SURVEILLANCE_REPORT")],
          relevant_types=ALL_TYPES, narrow=False,
          alignment_any=("consistent", "conflict", "location", "no ")),
    ]

    # ------------------------------------------------------ H corroboration
    questions += [
        q("H1", "H", f"How well supported is the claim that {witness} is a witness?",
          expect_types=[("WITNESS_STATEMENT", "FIR", "CASE_DIARY", "CHARGE_SHEET")],
          relevant_types=ALL_TYPES, narrow=False, requires_boundary=True,
          alignment_any=(witness, "record", "document")),
        q("H2", "H", "Which facts are supported by more than one record?",
          expect_types=[("FIR", "WITNESS_STATEMENT", "CASE_DIARY", "CHARGE_SHEET", "CDR")],
          relevant_types=ALL_TYPES, narrow=False,
          alignment_any=("record", "source", "assertion", "no ")),
    ]

    # ------------------------------------------------ I negative / absence
    questions += [
        q("I1", "I", f"Is there any evidence connecting {primary} and {outsider}?",
          expect_types=[("FIR", "CDR", "FINANCIAL", "CASE_DIARY", "CHARGE_SHEET", "WITNESS_STATEMENT")],
          relevant_types=ALL_TYPES, narrow=False, negative=True, requires_boundary=True,
          alignment_any=(primary, outsider, "no ", "not")),
        q("I2", "I", f"What evidence shows that {primary} used {foreign_phone}?",
          expect_types=[("CDR", "FIR", "INTELLIGENCE_REPORT", "CHARGE_SHEET")],
          relevant_types=("CDR", "FIR", "INTELLIGENCE_REPORT", "CHARGE_SHEET", "WITNESS_STATEMENT"),
          negative=True, requires_boundary=True,
          alignment_any=("no ", "not", "no case-scoped", "did not find"),
          forbidden_entities=(foreign_phone,)),
        q("I3", "I", "What evidence is missing from this case file?",
          expect_types=[("FIR", "CASE_DIARY")], relevant_types=ALL_TYPES, narrow=False,
          alignment_any=("missing", "not yet", "no ", "not on file")),
        q("I4", "I", f"Is there a financial transaction between {outsider} and {secondary}?",
          expect_types=[("FINANCIAL", "FIR", "CHARGE_SHEET")],
          relevant_types=("FINANCIAL", "FIR", "CHARGE_SHEET", "CASE_DIARY"),
          negative=True, requires_boundary=True,
          alignment_any=(outsider, secondary, "no ", "not")),
    ]

    # ----------------------------------------------------- J cross-case
    # Probes use values this case's records and graph never touch, so a
    # "foreign" question can never accidentally be a legitimate question.
    cross_case: list[EvalQuestion] = []
    if pool_people:
        cross_case += [
            q("J1", "J",
              f"In case {facts.case_number}, what evidence connects {foreign_unique} and {foreign_phone}?",
              expect_types=[("FIR", "CDR", "CASE_DIARY", "WITNESS_STATEMENT")],
              relevant_types=ALL_TYPES, narrow=False, negative=True, requires_boundary=True,
              alignment_any=("no ", "not", "case-scoped"),
              forbidden_entities=(foreign_unique, foreign_phone),
              notes="deliberate contamination: foreign person and phone"),
            q("J2", "J", f"Summarise the role of {foreign_person} in case {facts.case_number}.",
              expect_types=[("FIR", "CHARGE_SHEET", "CASE_DIARY")], relevant_types=ALL_TYPES,
              narrow=False, negative=True,
              expect_entities=(foreign_person,),
              alignment_any=(),
              forbidden_entities=(foreign_person,),
              notes="deliberate contamination: foreign person"),
        ]
    if pool_accounts:
        cross_case += [
            q("J3", "J", f"Does case {facts.case_number} contain a transfer to {foreign_account}?",
              expect_types=[("FINANCIAL",)],
              relevant_types=("FINANCIAL", "FIR", "CHARGE_SHEET", "CASE_DIARY"),
              negative=True, requires_boundary=True,
              alignment_any=("no ", "not"),
              forbidden_entities=(foreign_account,),
              notes="deliberate contamination: foreign account"),
        ]
    cross_case += [
        q("J4", "J", f"Is {facts.case_number} supported by the {foreign.case_number} records?",
          expect_types=[("FIR", "CHARGE_SHEET", "CASE_DIARY")], relevant_types=ALL_TYPES,
          narrow=False, negative=True,
          alignment_any=("case-scoped", "this case", "not supported", "cannot", "only"),
          forbidden_entities=(foreign_account,),
          notes="deliberate contamination: foreign case reference"),
    ]
    questions += cross_case

    # A person reachable in this case's graph but named by none of its records:
    # the answer has to keep "linked through the case graph" separate from
    # "named in this case's records".
    if facts.people_graph_only:
        linked_person = facts.people_graph_only[0]
        questions += [
            q("J5", "J",
              f"What do this case's records show about {linked_person}?",
              expect_types=[("FIR", "CHARGE_SHEET", "CASE_DIARY", "WITNESS_STATEMENT",
                             "CDR", "SURVEILLANCE_REPORT", "FINANCIAL", "ANPR")],
              relevant_types=ALL_TYPES, narrow=False,
              expect_entities=(linked_person,),
              alignment_any=(linked_person, "no ", "not", "case"),
              notes=("shared-identity probe: the person is in this case's graph "
                     "but named by none of its records")),
        ]

    # ------------------------------------------------------- K ambiguous
    questions += [
        q("K1", "K", "Who is involved?", expect_types=[("FIR", "CHARGE_SHEET", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, alignment_any=(primary, "no ", "case")),
        q("K2", "K", "What is important here?", expect_types=[("FIR", "CASE_DIARY", "CHARGE_SHEET")],
          relevant_types=ALL_TYPES, narrow=False, alignment_any=(facts.case_number, "case", "evidence")),
        q("K3", "K", "Any leads?", expect_types=[("INTELLIGENCE_REPORT", "FIR", "CASE_DIARY", "CHARGE_SHEET")],
          relevant_types=ALL_TYPES, narrow=False, alignment_any=("lead", "no ", "case")),
        q("K4", "K", "Why?", expect_types=[("FIR", "CASE_DIARY")], relevant_types=ALL_TYPES,
          narrow=False, alignment_any=("case", "no ", "evidence")),
    ]

    # ---------------------------------------------------------- L complex
    questions += [
        q("L1", "L",
          f"What evidence connects {primary} to {secondary} before the incident, and how well is that connection supported?",
          expect_types=[("CDR", "FINANCIAL", "FIR", "CASE_DIARY", "WITNESS_STATEMENT")],
          relevant_types=ALL_TYPES, narrow=False, requires_boundary=True,
          temporal_relation="BEFORE", temporal_anchor=incident,
          alignment_any=(primary, secondary)),
        q("L2", "L",
          f"Are there any conflicting accounts about {primary}'s location before the incident, and which records contain them?",
          expect_types=[("FIR", "WITNESS_STATEMENT", "SURVEILLANCE_REPORT", "SCENE_REPORT", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, requires_boundary=True,
          temporal_relation="BEFORE", temporal_anchor=incident,
          alignment_any=(primary, "record", "no ")),
        q("L3", "L",
          "What evidence links the financial transactions to the people involved in the communication records?",
          expect_types=[("FINANCIAL",), ("CDR",)],
          relevant_types=("FINANCIAL", "CDR", "FIR", "CHARGE_SHEET", "INTELLIGENCE_REPORT",
                          "WITNESS_STATEMENT", "CASE_DIARY"),
          requires_boundary=True,
          alignment_any=("account", "phone", "transfer", "call", "no ")),
        q("L4", "L",
          f"What is the strongest documented link to {primary}, and what does it not establish?",
          expect_types=[("CDR", "FINANCIAL", "FIR", "CHARGE_SHEET", "CASE_DIARY")],
          relevant_types=ALL_TYPES, narrow=False, requires_boundary=True,
          alignment_any=(primary, "not", "no ")),
    ]
    return questions


def count_by_category(questions: list[EvalQuestion]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for question in questions:
        counts[question.category] = counts.get(question.category, 0) + 1
    return counts


def _unused(value: Any) -> None:  # pragma: no cover - keeps linters quiet
    re.escape(str(value))
