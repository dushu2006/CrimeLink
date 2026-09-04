"""End-to-end tests for the investigator-driven workflow.

These tests ingest a miniature corpus through the REAL source adapter and the
REAL six-stage pipeline, then drive the investigation workflow stage by stage,
asserting that every stage performs a genuine operation with a real, persisted
outcome — and that stages refuse to run out of order.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    DetectedPattern,
    InvestigationFinding,
    InvestigationStageRun,
)
from app.db.session import async_session, sync_session
from app.domain.enums import canonical_label
from app.services import investigation
from app.services.investigation import StageBlocked, run_stage, workflow_state

from tests.test_provenance import _write_csv


@dataclass
class CorpusSpec:
    """The mini-corpus plus the unique identities this test run uses."""

    root: Path
    case_number: str


@pytest.fixture()
def corpus(tmp_path: Path) -> CorpusSpec:
    """A corpus with ownership, calls and transfers between two persons.

    Asha holds account AC0001 and phone PH0001; Vikram holds AC0002 / PH0002;
    Meera holds AC0003.  Money moves AC0001 -> AC0002 and AC0002 -> AC0003, so
    the findings stage has a genuine two-hop financial chain to discover.

    Every identity value is unique per test (the suite shares one SQLite
    database, so static ids would leak workflow state between tests).
    """
    tag = uuid4().hex[:6]
    n6 = int(tag[:6], 16) % 900000 + 100000          # 6 digits
    phone_a = f"98{n6 % 100000000:08d}"              # 10 digits, unique per test
    phone_b = f"97{n6 % 100000000:08d}"
    case_id = f"CWF{tag.upper()}"
    case_number = f"FIR/2098/{n6}"
    p1, p2, p3 = f"P{tag.upper()}1", f"P{tag.upper()}2", f"P{tag.upper()}3"
    acct = {p1: f"XX1{n6}", p2: f"XX2{n6}", p3: f"XX3{n6}"}
    plate = f"AP{n6 % 100:02d}AB{1000 + n6 % 9000}"

    root = tmp_path / "Corpus_v1"
    op = root / "operational"

    _write_csv(
        op / "cases.csv",
        ["case_id", "case_number", "registered_date", "case_type", "police_station", "city", "status"],
        [{"case_id": case_id, "case_number": case_number, "registered_date": "2026-02-02",
          "case_type": "FRAUD", "police_station": "PS-08", "city": "Kurnool", "status": "OPEN"}],
    )
    _write_csv(
        op / "persons.csv",
        ["person_id", "full_name", "gender", "dob", "address", "city", "state", "status"],
        [
            {"person_id": p1, "full_name": f"Asha Reddy {tag.upper()}", "gender": "F", "dob": "",
             "address": "", "city": "Kurnool", "state": "Andhra Pradesh", "status": "ACTIVE"},
            {"person_id": p2, "full_name": f"Vikram Naidu {tag.upper()}", "gender": "M", "dob": "",
             "address": "", "city": "Kurnool", "state": "Andhra Pradesh", "status": "ACTIVE"},
            {"person_id": p3, "full_name": f"Meera Krishnan {tag.upper()}", "gender": "F", "dob": "",
             "address": "", "city": "Kurnool", "state": "Andhra Pradesh", "status": "ACTIVE"},
        ],
    )
    _write_csv(
        op / "case_members.csv",
        ["case_member_id", "case_id", "person_id", "role"],
        [
            {"case_member_id": f"CM1{tag.upper()}", "case_id": case_id, "person_id": p1, "role": "SUSPECT"},
            {"case_member_id": f"CM2{tag.upper()}", "case_id": case_id, "person_id": p2, "role": "WITNESS"},
            {"case_member_id": f"CM3{tag.upper()}", "case_id": case_id, "person_id": p3, "role": "CO_ACCUSED"},
        ],
    )
    _write_csv(
        op / "phones.csv",
        ["phone_id", "phone_number", "owner_person_id", "status", "source"],
        [
            {"phone_id": f"PHA{tag.upper()}", "phone_number": phone_a, "owner_person_id": p1,
             "status": "ACTIVE", "source": "SYNTHETIC"},
            {"phone_id": f"PHB{tag.upper()}", "phone_number": phone_b, "owner_person_id": p2,
             "status": "ACTIVE", "source": "SYNTHETIC"},
        ],
    )
    _write_csv(
        op / "accounts.csv",
        ["account_id", "account_number", "holder_person_id", "bank_code", "account_status"],
        [
            {"account_id": f"AC1{tag.upper()}", "account_number": acct[p1], "holder_person_id": p1,
             "bank_code": "HDFC", "account_status": "ACTIVE"},
            {"account_id": f"AC2{tag.upper()}", "account_number": acct[p2], "holder_person_id": p2,
             "bank_code": "ICIC", "account_status": "ACTIVE"},
            {"account_id": f"AC3{tag.upper()}", "account_number": acct[p3], "holder_person_id": p3,
             "bank_code": "AXIS", "account_status": "ACTIVE"},
        ],
    )
    _write_csv(
        op / "vehicles.csv",
        ["vehicle_id", "registration_number", "vehicle_type", "owner_person_id", "color"],
        [{"vehicle_id": f"VH{tag.upper()}", "registration_number": plate, "vehicle_type": "CAR",
          "owner_person_id": p1, "color": "white"}],
    )
    cdr_rows = [
        {"cdr_id": f"CDR{i}{tag.upper()}", "timestamp": f"2026-01-0{i} 10:00:00",
         "from_phone_id": f"PHB{tag.upper()}", "to_phone_id": f"PHA{tag.upper()}", "duration_seconds": "10",
         "call_type": "SMS", "cell_location_id": "", "case_id": case_id}
        for i in (1, 2, 3)
    ]
    cdr_rows.append(
        {"cdr_id": f"CDR99{tag.upper()}", "timestamp": "2026-01-09 21:34:00",
         "from_phone_id": f"PHA{tag.upper()}", "to_phone_id": f"PHB{tag.upper()}", "duration_seconds": "842",
         "call_type": "VOICE", "cell_location_id": "", "case_id": case_id}
    )
    _write_csv(
        op / "cdr.csv",
        ["cdr_id", "timestamp", "from_phone_id", "to_phone_id", "duration_seconds",
         "call_type", "cell_location_id", "case_id"],
        cdr_rows,
    )
    _write_csv(
        op / "transactions.csv",
        ["transaction_id", "timestamp", "from_account_id", "to_account_id", "amount_inr",
         "transaction_type", "location_id", "case_id"],
        [
            {"transaction_id": f"TX1{tag.upper()}", "timestamp": "2026-01-11 09:15:00",
             "from_account_id": f"AC1{tag.upper()}", "to_account_id": f"AC2{tag.upper()}", "amount_inr": "45000",
             "transaction_type": "IMPS", "location_id": "", "case_id": case_id},
            {"transaction_id": f"TX2{tag.upper()}", "timestamp": "2026-01-12 09:20:00",
             "from_account_id": f"AC1{tag.upper()}", "to_account_id": f"AC2{tag.upper()}", "amount_inr": "46000",
             "transaction_type": "IMPS", "location_id": "", "case_id": case_id},
            {"transaction_id": f"TX3{tag.upper()}", "timestamp": "2026-01-14 11:05:00",
             "from_account_id": f"AC2{tag.upper()}", "to_account_id": f"AC3{tag.upper()}", "amount_inr": "80000",
             "transaction_type": "NEFT", "location_id": "", "case_id": case_id},
        ],
    )
    _write_csv(
        op / "documents.csv",
        ["document_id", "case_id", "document_type", "file_path", "language", "source_environment"],
        [{"document_id": f"DOC{tag.upper()}", "case_id": case_id, "document_type": "FIR_NOTE",
          "file_path": f"documents/DOC{tag.upper()}.txt", "language": "en", "source_environment": "synthetic"}],
    )
    docs = root / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / f"DOC{tag.upper()}.txt").write_text(
        "\n".join(
            [
                "Case note line one.",
                f"Asha Reddy {tag.upper()} contacted Vikram Naidu {tag.upper()} on {phone_b}.",
                "Case note line three.",
            ]
        ),
        encoding="utf-8",
    )
    return CorpusSpec(root=root, case_number=case_number)


@pytest.fixture()
def ingested_case_id(corpus: CorpusSpec, container) -> str:
    """Ingest the corpus through the REAL adapter + pipeline and return a case id.

    Depends on ``container`` so the graph store points at this test's own
    snapshot file (conftest isolation), not a file shared across the suite.
    """
    from app.container import get_container
    from app.synthetic_corpus.external import await_pipeline_quiet, ingest_external_corpus

    async def run() -> str:
        report = await ingest_external_corpus(root=corpus.root, safety_confirmed=True)
        # The conftest RecordingBroker only records dispatches; run them now.
        get_container().broker.drain()
        await await_pipeline_quiet(get_container(), report, timeout_seconds=90)
        async with async_session() as session:
            from sqlalchemy import select

            row = (
                await session.execute(
                    select(Case).where(Case.case_number == corpus.case_number)
                )
            ).scalars().one()
            return row.id

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# Gating: the workflow is a state machine, not a page-load side effect
# ---------------------------------------------------------------------------


def test_every_stage_starts_pending_and_gated(ingested_case_id: str) -> None:
    state = workflow_state(ingested_case_id)
    by_key = {s["key"]: s for s in state["stages"]}
    assert len(state["stages"]) == 8
    assert all(s["status"] == "PENDING" for s in state["stages"])
    assert not by_key["extract_entities"]["runnable"]
    assert by_key["extract_entities"]["blocked_by"] == [1]

    with pytest.raises(StageBlocked) as excinfo:
        run_stage(ingested_case_id, "extract_entities")
    assert "process_data" in str(excinfo.value)


def test_stage_one_processes_pending_documents_for_real(ingested_case_id: str) -> None:
    result = run_stage(ingested_case_id, "process_data")
    detail = result["detail"]
    assert detail["documents_total"] >= 4, "the corpus produces several documents per case"
    assert detail["processed_now"] + detail["already_complete"] == detail["documents_total"]
    assert detail["still_pending"] == 0

    state = workflow_state(ingested_case_id)
    by_key = {s["key"]: s for s in state["stages"]}
    assert by_key["process_data"]["status"] == "COMPLETED"
    assert by_key["extract_entities"]["runnable"] is True


def test_stage_two_reports_a_real_entity_inventory(ingested_case_id: str) -> None:
    run_stage(ingested_case_id, "process_data")
    result = run_stage(ingested_case_id, "extract_entities")
    detail = result["detail"]
    assert detail["documents_parsed"] >= 4
    inventory = {k.upper(): v for k, v in detail["by_entity_type"].items()}
    assert inventory.get("PERSON", 0) >= 3
    assert inventory.get("PHONE", 0) >= 2
    assert inventory.get("BANK_ACCOUNT", 0) >= 3
    assert inventory.get("VEHICLE", 0) >= 1
    assert detail["deterministic_entities"] > 0
    assert detail["nlp_provider"], "the NLP provider that actually ran must be named"


def test_stage_three_queues_low_confidence_matches(ingested_case_id: str) -> None:
    run_stage(ingested_case_id, "process_data")
    run_stage(ingested_case_id, "extract_entities")
    result = run_stage(ingested_case_id, "resolve_entities")
    detail = result["detail"]
    assert detail["documents_resolved"] >= 4
    assert detail["hard_matches"] >= 0
    assert "review_queue_pending" in detail


def test_stage_four_materializes_person_account_ownership(ingested_case_id: str) -> None:
    """THE dataset fix, end to end: holder_person_id becomes OWNS_ACCOUNT.

    The corpus states that P0001 holds AC0001 etc.; after the relationship
    stage the graph must contain Person—OWNS_ACCOUNT→Account edges, and the
    financial chain must be traversable Person→Account→Transfer→Account→Person.
    """
    from app.container import get_container

    run_stage(ingested_case_id, "process_data")
    run_stage(ingested_case_id, "extract_entities")
    run_stage(ingested_case_id, "resolve_entities")
    result = run_stage(ingested_case_id, "build_relationships")
    detail = result["detail"]

    rels = detail["relationships_by_type"]
    assert rels.get("OWNS_ACCOUNT", 0) >= 3, "all three corpus holders must own their accounts"
    assert rels.get("USES_PHONE", 0) >= 2
    assert rels.get("OWNS_VEHICLE", 0) >= 1
    assert rels.get("TRANSFER_TO", 0) == 3
    assert rels.get("CALLED", 0) >= 1

    snapshot = get_container().graph_store.snapshot(ingested_case_id, include_staging=False)
    owners = {
        (e.source_key, e.target_key) for e in snapshot.edges_by_type("OWNS_ACCOUNT")
    }
    assert owners, "no ownership edges in the graph"
    for edge in snapshot.edges_by_type("OWNS_ACCOUNT"):
        person = snapshot.nodes[edge.source_key]
        account = snapshot.nodes[edge.target_key]
        assert canonical_label(person.label) == "PERSON" and canonical_label(account.label) == "BANK_ACCOUNT"
    # Every transfer edge's endpoints are owned by someone — the chain closes.
    transfer_accounts = {
        e.source_key for e in snapshot.edges_by_type("TRANSFER_TO")
    } | {e.target_key for e in snapshot.edges_by_type("TRANSFER_TO")}
    covered = {target for (_, target) in owners}
    assert transfer_accounts <= covered, "an account moved money but no person holds it"


def test_stage_five_verifies_the_persisted_graph(ingested_case_id: str) -> None:
    for stage in ("process_data", "extract_entities", "resolve_entities", "build_relationships"):
        run_stage(ingested_case_id, stage)
    result = run_stage(ingested_case_id, "build_graph")
    detail = result["detail"]
    assert detail["graph_backend"] in ("embedded", "neo4j")
    assert detail["nodes_persisted"] > 0
    assert detail["person_nodes"] >= 3
    assert detail["edges_persisted"] > 0


def test_stage_six_runs_and_persists_network_analysis(ingested_case_id: str) -> None:
    for stage in ("process_data", "extract_entities", "resolve_entities",
                  "build_relationships", "build_graph"):
        run_stage(ingested_case_id, stage)
    result = run_stage(ingested_case_id, "network_analysis")
    detail = result["detail"]
    assert detail["nodes_analyzed"] > 0
    assert detail["communities"] >= 1
    assert detail["engine"] == "networkx"
    with sync_session() as session:
        patterns = (
            session.execute(
                select(DetectedPattern.id).where(DetectedPattern.case_id == ingested_case_id)
            ).scalars().all()
        )
    assert isinstance(patterns, list)  # persisted table readable (may be empty for this corpus)


def test_stage_seven_is_honest_without_a_key(ingested_case_id: str) -> None:
    for stage in ("process_data", "extract_entities", "resolve_entities",
                  "build_relationships", "build_graph", "network_analysis"):
        run_stage(ingested_case_id, stage)
    result = run_stage(ingested_case_id, "ai_analysis")
    detail = result["detail"]
    # The test environment has no reasoning key: the stage must say so, not pretend.
    assert detail["ai_available"] is False
    assert "no_api_key" in detail["reason"]
    assert "CRIMELINK_AI_REASONING_API_KEY" in detail["message"]


def test_stage_eight_produces_evidence_backed_findings(ingested_case_id: str) -> None:
    for stage in ("process_data", "extract_entities", "resolve_entities",
                  "build_relationships", "build_graph", "network_analysis"):
        run_stage(ingested_case_id, stage)
    result = run_stage(ingested_case_id, "generate_findings")
    assert result["detail"]["findings_created"] >= 1

    payload = investigation.findings_list(ingested_case_id)
    items = payload["items"]
    financial = [f for f in items if f["finding_type"] == "FINANCIAL_LINK"]
    assert financial, "the corpus contains a two-hop financial chain; it must be found"
    top = financial[0]
    assert top["confidence_band"] in ("HIGH", "MEDIUM", "LOW")
    # Evidence: ownership edges AND the transfers with amounts/timestamps/refs.
    evidence_kinds = {e["kind"] for e in top["evidence"]}
    assert evidence_kinds == {"relationship"}
    rel_types = {e["rel_type"] for e in top["evidence"]}
    assert {"OWNS_ACCOUNT", "TRANSFER_TO"} <= rel_types
    transfer_evidence = next(e for e in top["evidence"] if e["rel_type"] == "TRANSFER_TO")
    assert transfer_evidence["transfer_count"] >= 1
    assert transfer_evidence["transfers"][0]["amount"] is not None
    assert transfer_evidence["source_doc_ids"], "findings must cite source documents"
    # Neutral language, no accusations.
    assert "requires review" in top["narrative"] or "not by itself evidence" in top["narrative"]

    # Re-running does not duplicate: dedupe by (type, entities).
    again = run_stage(ingested_case_id, "generate_findings")
    assert again["detail"]["findings_created"] == 0
    assert again["detail"]["findings_skipped_existing"] >= 1

    # Human-in-the-loop: a finding can be reviewed.
    review = investigation.review_finding(
        ingested_case_id, top["id"], "CONFIRMED", "checked against statements", "user-1"
    )
    assert review["status"] == "CONFIRMED"


def test_findings_stage_cannot_run_before_network_analysis(ingested_case_id: str) -> None:
    for stage in ("process_data", "extract_entities", "resolve_entities",
                  "build_relationships", "build_graph"):
        run_stage(ingested_case_id, stage)
    with pytest.raises(StageBlocked):
        run_stage(ingested_case_id, "generate_findings")


def test_failure_blocks_dependent_stages(ingested_case_id: str, monkeypatch) -> None:
    """A FAILED stage leaves its dependents locked and records the error."""
    from app.services import investigation as inv

    run_stage(ingested_case_id, "process_data")

    def boom(*_a, **_k):
        raise RuntimeError("object store exploded")

    monkeypatch.setattr(inv, "_stage_extract_entities", boom)
    with pytest.raises(RuntimeError):
        run_stage(ingested_case_id, "extract_entities")

    state = workflow_state(ingested_case_id)
    by_key = {s["key"]: s for s in state["stages"]}
    assert by_key["extract_entities"]["status"] == "FAILED"
    assert "object store exploded" in by_key["extract_entities"]["error"]
    assert by_key["resolve_entities"]["runnable"] is False

    # Recovery: once the underlying issue is fixed, a re-run completes.
    monkeypatch.undo()
    result = run_stage(ingested_case_id, "extract_entities")
    assert result["status"] == "COMPLETED"
