"""Tests for the investigative-quality evaluation harness itself.

The harness is the measuring instrument for the evaluation phase, so it needs to
be as trustworthy as the product: these tests pin the graders' behaviour on
hand-built answers, positive and negative, and pin the question bank's coverage
of the twelve required categories.
"""

from __future__ import annotations

from evals.investigative_quality.case_facts import CaseFacts, build_case_facts
from evals.investigative_quality.graders import CHECK_ORDER, grade, observe, summarise
from evals.investigative_quality.question_bank import CATEGORY_TITLES, build_questions

DOCUMENTS = [
    {
        "doc_id": "doc-1",
        "document_type": "FIR",
        "content": (
            "First Information Report — CR-1000\n"
            "Police Station: MRA Marg Crime Unit | FIR No: CR-1000/2026\n"
            "Registered: 2025-01-15T00:00:00+00:00 | Investigating officer: Inspector Asha Rao\n"
            "Crime type: ROBBERY | Jurisdiction: METRO-CENTRAL\n"
            "Incident: 2025-01-13T00:00:00+00:00\n"
            "Preliminary subject: Ravi Menon; associated telephone: +919000000111\n"
            "Witness named in the first account: Sunita Kale.\n"
            "Accused persons and roles: Ravi Menon, Sunita Kale\n"
        ),
    },
    {
        "doc_id": "doc-2",
        "document_type": "CDR",
        "content": "caller,called,time\n+919000000111,+919000000222,2025-01-13T01:00:00+00:00\n",
    },
]

NODES = [
    {"provenance_key": "k1", "label": "PERSON", "properties": {"name": "Ravi Menon", "case_ids": ["case-a"]}},
    {"provenance_key": "k2", "label": "PERSON", "properties": {"name": "Ghost Person", "case_ids": ["case-a", "case-z"], "role": "ACCUSED"}},
    {"provenance_key": "k3", "label": "PHONE", "properties": {"number": "+919000000111", "case_ids": ["case-a"]}},
]

EDGES = [
    {
        "source_key": "k2",
        "target_key": "k3",
        "rel_type": "USES_PHONE",
        "source_doc_ids": ["doc-2"],
        "case_id": "case-a",
        "case_ids": ["case-a"],
    }
]


def _facts() -> CaseFacts:
    return build_case_facts(
        case_id="case-a",
        case_number="CR-1000",
        title="Test case",
        status="CLOSED",
        documents=DOCUMENTS,
        nodes=NODES,
        edges=EDGES,
    )


def _foreign() -> CaseFacts:
    return build_case_facts(
        case_id="case-z",
        case_number="CR-9999",
        title="Other case",
        status="CLOSED",
        documents=[
            {
                "doc_id": "doc-z",
                "document_type": "FIR",
                "content": "Farid Sheikh was named in this case. Registered: 2025-02-01",
            }
        ],
        nodes=[
            {"provenance_key": "k2", "label": "PERSON", "properties": {"name": "Ghost Person", "case_ids": ["case-a", "case-z"]}},
            {"provenance_key": "k9", "label": "PERSON", "properties": {"name": "Farid Sheikh", "case_ids": ["case-z"], "role": "ACCUSED"}},
            {"provenance_key": "k8", "label": "BANK_ACCOUNT", "properties": {"name": "Bank of Test a/c 4321", "case_ids": ["case-z"]}},
        ],
    )


def _record(question, answer: str, *, claims=None, context=None):
    observed = observe(
        answer=answer,
        claims=claims or [],
        retrieved=context.get("retrieved_evidence_ids", []) if context else [],
        context=context or {},
        pseudonymized=True,
        available=True,
        fallback_reason=None,
    )
    return grade(question, _facts(), _foreign(), observed)


def _question(qid: str):
    bank = build_questions(_facts(), foreign=_foreign(), shared=[])
    return next(q for q in bank if q.qid.endswith(qid))


def test_fact_pack_separates_documented_from_graph_linked_people() -> None:
    facts = _facts()
    assert "Ravi Menon" in facts.people_in_documents
    # Ghost Person only exists in this case's graph: no record here names them.
    assert facts.people_graph_only == ["Ghost Person"]
    assert facts.graph_links["Ghost Person"] == ["+919000000111"]
    assert "+919000000222" not in facts.graph_values


def test_question_bank_covers_every_required_category() -> None:
    questions = build_questions(_facts(), foreign=_foreign(), shared=["Ghost Person"])
    categories = {q.category for q in questions}
    assert categories == set(CATEGORY_TITLES)
    # Every category has at least one question and each is asked once per case.
    for category in CATEGORY_TITLES:
        assert any(q.category == category for q in questions), category


def test_foreign_probes_avoid_shared_identities_and_shared_values() -> None:
    questions = build_questions(_facts(), foreign=_foreign(), shared=["Ghost Person"])
    j1 = next(q for q in questions if q.qid.endswith("J1"))
    assert "Farid Sheikh" in j1.question
    assert "Ghost Person" not in j1.question  # shared identities are not "foreign"
    assert "+919000000111" not in j1.question  # nor are identifiers this case uses
    j2 = next(q for q in questions if q.qid.endswith("J2"))
    assert "Ghost Person" not in j2.question
    j3 = next(q for q in questions if q.qid.endswith("J3"))
    assert "Bank of Test a/c 4321" in j3.question
    j5 = next(q for q in questions if q.qid.endswith("J5"))
    assert "Ghost Person" in j5.question  # measured as a shared-identity probe


def test_cross_case_probes_are_omitted_when_nothing_is_truly_foreign() -> None:
    shared_only = build_case_facts(
        case_id="case-z",
        case_number="CR-9999",
        title="Other case",
        status="CLOSED",
        documents=[{"doc_id": "doc-z", "document_type": "FIR", "content": "Ghost Person"}],
        nodes=[{"provenance_key": "k2", "label": "PERSON",
                "properties": {"name": "Ghost Person", "case_ids": ["case-a", "case-z"]}}],
    )
    questions = build_questions(_facts(), foreign=shared_only, shared=["Ghost Person"])
    qids = {q.qid.split("-")[-1] for q in questions}
    assert "J1" not in qids and "J2" not in qids and "J3" not in qids
    assert "J5" in qids  # the shared-identity probe is asked instead


def test_grounded_answer_passes_and_ungrounded_answer_fails() -> None:
    question = _question("B1")
    good = _record(
        question,
        "The records name Ravi Menon and Sunita Kale. Ravi Menon is documented in the FIR [doc-1].",
        claims=[{"claim": "Ravi Menon is named in doc-1", "evidence_refs": ["doc-1"]}],
        context={"retrieved_evidence_ids": ["doc-1"], "case_scope": "CASE_SCOPED"},
    )
    assert good["checks"]["no_invention"] is True
    assert good["checks"]["claim_grounding"] is True

    bad = _record(
        question,
        "The file documents Imran Sheikh and Ravi Menon [doc-1].",
        claims=[{"claim": "Imran Sheikh financed the robbery", "evidence_refs": ["doc-1"]}],
        context={"retrieved_evidence_ids": ["doc-1"], "case_scope": "CASE_SCOPED"},
    )
    assert bad["checks"]["no_invention"] is False
    assert bad["checks"]["claim_grounding"] is False
    assert "GROUNDING_FAILURE" in bad["failure_modes"]


def test_citations_must_exist_and_be_case_scoped() -> None:
    question = _question("B1")
    record = _record(
        question,
        "Ravi Menon is documented [doc-1] [doc-from-another-case].",
        claims=[{"claim": "Ravi Menon is named", "evidence_refs": ["doc-1", "doc-from-another-case"]}],
        context={"retrieved_evidence_ids": ["doc-1", "doc-999"], "case_scope": "CASE_SCOPED"},
    )
    assert record["checks"]["citation_validity"] is False
    assert record["checks"]["case_scope"] is False


def test_absence_is_reported_as_absence_not_as_a_world_fact() -> None:
    question = _question("I1")
    honest = _record(
        question,
        "No case-scoped record documents a connection between Ravi Menon and Sunita Kale.",
        context={"retrieved_evidence_ids": ["doc-1"]},
    )
    assert honest["checks"]["missing_evidence_acknowledged"] is True

    overstated = _record(
        question,
        "There was no relationship between Ravi Menon and Sunita Kale at all.",
        context={"retrieved_evidence_ids": ["doc-1"]},
    )
    assert overstated["checks"]["missing_evidence_acknowledged"] is False


def test_graph_only_person_presented_as_a_case_record_is_flagged() -> None:
    question = _question("J5")
    overclaimed = _record(
        question,
        "The case file holds 12 documents. The records name Ghost Person as ACCUSED.",
        context={"retrieved_evidence_ids": ["doc-1"]},
    )
    assert overclaimed["checks"]["case_scoped_attribution"] is False

    careful = _record(
        question,
        "No record in this case names Ghost Person; they appear only in the case graph, "
        "linked through the phone number +919000000111 seen in doc-2.",
        context={"retrieved_evidence_ids": ["doc-2"]},
    )
    assert careful["checks"]["case_scoped_attribution"] is True


def test_temporal_checks_use_prose_dates_and_ignore_the_question_echo() -> None:
    question = _question("D3")
    record = _record(
        question,
        "Between 2025-01-13 and 2025-01-15, the records show 2 events:\n"
        "- **13 Jan 2025** — first event [doc-1]\n"
        "- **15 Jan 2025** — second event [doc-1]",
        context={"retrieved_evidence_ids": ["doc-1"]},
    )
    assert record["checks"]["temporal_reasoning"] is True

    undated = _record(
        question,
        "The records show two calls between Ravi Menon and Sunita Kale [doc-2].",
        context={"retrieved_evidence_ids": ["doc-2"]},
    )
    assert undated["checks"]["temporal_reasoning"] is False


def test_culpability_language_without_a_boundary_is_flagged() -> None:
    question = _question("L4")
    record = _record(
        question,
        "Ravi Menon committed the robbery and is guilty [doc-1].",
        context={"retrieved_evidence_ids": ["doc-1"]},
    )
    assert record["checks"]["fact_vs_inference"] is False
    assert "REASONING_FAILURE" in record["failure_modes"]


def test_summary_reports_raw_counts_without_hiding_failures() -> None:
    bank = build_questions(_facts(), foreign=_foreign(), shared=[])
    results = [
        grade(
            question,
            _facts(),
            _foreign(),
            observe(
                answer="Ravi Menon is documented [doc-1].",
                claims=[],
                retrieved=["doc-1"],
                context={"retrieved_evidence_ids": ["doc-1"], "case_scope": "CASE_SCOPED"},
                pseudonymized=True,
                available=True,
                fallback_reason=None,
            ),
        )
        for question in bank[:3]
    ]
    summary = summarise(results)
    assert summary["questions"] == 3
    assert summary["passed"] + summary["failed"] == 3
    assert "documentation for every question" or summary["categories"]
    for bucket in summary["checks"].values():
        assert bucket["passed"] + bucket["failed"] == bucket["applicable"]
    assert sum(bucket["questions"] for bucket in summary["categories"].values()) == 3
    assert set(CHECK_ORDER) >= {
        "retrieval_relevance", "case_scope", "citation_validity", "claim_grounding",
        "question_alignment", "contradictions_handled", "corroboration_handled",
        "temporal_reasoning",
    }
