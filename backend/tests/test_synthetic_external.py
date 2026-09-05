"""Tests for the external synthetic corpus source adapter and ingestion flow.

The fixture corpus built here is a *test fixture* created in a pytest tmp
directory — it exercises the discovery/validation/classification rules
without touching (or assuming the contents of) any real external dataset.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.adapters.sources import available_adapters, get_source_adapter
from app.adapters.sources.synthetic_external import (
    ExternalCorpusError,
    ExternalSyntheticCorpusAdapter,
)
from app.domain.enums import DocumentType, IngestionStatus, SourceConfidence

# Unique marker planted only inside ground_truth/: if any pipeline stage ever
# ingests evaluation material, this exact string would surface as a Person.
GROUND_TRUTH_MARKER = "Zqx Neverperson ZXQ-9001"


def _salted_mobile(salt: str, offset: int = 0) -> str:
    seed = int(hashlib.sha256(f"{salt}:{offset}".encode()).hexdigest()[:8], 16)
    return f"+91{9_000_000_000 + seed % 999_999_999}"


def _make_corpus(root: Path, *, salt: str) -> Path:
    """Build a minimal but realistic external-corpus layout under ``root``."""
    caller = _salted_mobile(salt, 1)
    callee = _salted_mobile(salt, 2)
    acct_a = str(5_000_000_000_000 + int(hashlib.sha256(salt.encode()).hexdigest()[:4], 16))
    acct_b = str(5_100_000_000_000 + int(hashlib.sha256(salt.encode()).hexdigest()[:4], 16))

    (root / "operational" / "CELL-ALPHA").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    (root / "metadata").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)

    (root / "README.md").write_text("# Fixture corpus\n", encoding="utf-8")
    (root / "SCHEMA.md").write_text("# Schema\n", encoding="utf-8")

    (root / "operational" / "CELL-ALPHA" / "cdr.csv").write_text(
        "\n".join(
            ["calling_number,called_number,timestamp,duration_seconds,direction,imei"]
            + [
                f"{caller},{callee},2024-08-0{i}T10:00:00Z,{60 + i},OUTGOING,3557100000{i}"
                for i in range(1, 8)
            ]
        ),
        encoding="utf-8",
    )
    (root / "operational" / "CELL-ALPHA" / "sightings.csv").write_text(
        "\n".join(
            [
                "subject,observed_at,location,vehicle,remarks",
                f"Suresh Kumar,2024-08-02 11:00,Jaipur Junction,RJ14AB1234,watch-{salt}",
                f"Meena Rathore,2024-08-03 16:30,Sanganer Gate,,tail-{salt}",
            ]
        ),
        encoding="utf-8",
    )
    # Valid CDR headers, 100% unusable rows: the source adapter accepts the
    # file, the pipeline must quarantine it with the 5% bad-row rule.
    (root / "operational" / "CELL-ALPHA" / "cdr_dirty.csv").write_text(
        "\n".join(
            ["calling_number,called_number,timestamp,duration_seconds,direction,imei"]
            + [f"not-a-number,{salt},banana,oops,?,?" for _ in range(4)]
        ),
        encoding="utf-8",
    )
    (root / "operational" / "txns.csv").write_text(
        "\n".join(
            ["txn_id,date,from_account,to_account,amount,ifsc,remarks"]
            + [
                f"TXN{i:03d},2024-08-{i:02d},{acct_a},{acct_b},48000,SBIN0001234,cash"
                for i in range(1, 6)
            ]
        ),
        encoding="utf-8",
    )
    (root / "operational" / "mystery.csv").write_text(
        "rec_id,category,score\n1,alpha,0.4\n2,beta,0.7\n", encoding="utf-8"
    )
    (root / "operational" / "broken.json").write_text("{not valid json", encoding="utf-8")
    (root / "operational" / "notes.xlsx").write_bytes(b"not a real spreadsheet")

    (root / "documents" / "witness_statement.txt").write_text(
        "Witness statement. Suresh Kumar was seen with Vikram Singh Rathore "
        f"near Bapu Bazaar using mobile number {caller}.", encoding="utf-8"
    )
    (root / "documents" / "intel_note.txt").write_text(
        "Intelligence note: an associate of Suresh Kumar frequents Sanganer. "
        f"(source batch {salt})",
        encoding="utf-8",
    )
    (root / "documents" / "history.csv").write_text(
        "\n".join(
            [
                "name,aliases,ipc_sections,case_ref,case_date",
                f"Suresh Kumar,Suresh K,384;120B,FIR/2019/{salt},2019-04-02",
                f"Vikram Singh Rathore,Vikram Rathore,386,FIR/2021/{salt},2021-11-19",
            ]
        ),
        encoding="utf-8",
    )

    # Deliberately *valid-looking* data in the forbidden directories: exclusion
    # must come from the directory rule, not from classification failure.
    (root / "metadata" / "manifest.json").write_text(
        json.dumps({"friends": ["a", "b"], "followers": ["c"], "username": "fixture"}),
        encoding="utf-8",
    )
    (root / "ground_truth" / "answers.csv").write_text(
        "name,aliases\n" + GROUND_TRUTH_MARKER + ",The Answer\n", encoding="utf-8"
    )
    return root


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    return _make_corpus(tmp_path / "corpus_alpha", salt="unit")


# ---------------------------------------------------------------------------
# Discovery / validation (source adapter boundary)
# ---------------------------------------------------------------------------


def test_scan_finds_operational_files(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    assert scan.ok, scan.issues
    accepted = {f.relative_path: f for f in scan.accepted}
    assert "operational/CELL-ALPHA/cdr.csv" in accepted
    assert "operational/CELL-ALPHA/sightings.csv" in accepted
    assert "operational/txns.csv" in accepted


def test_scan_finds_documents(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    accepted = {f.relative_path: f for f in scan.accepted}
    assert "documents/witness_statement.txt" in accepted
    assert "documents/intel_note.txt" in accepted
    assert "documents/history.csv" in accepted


def test_classification_matches_pipeline_schemas(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    by_path = {f.relative_path: f for f in scan.files}
    assert by_path["operational/CELL-ALPHA/cdr.csv"].document_type is DocumentType.CDR
    assert by_path["operational/CELL-ALPHA/sightings.csv"].document_type is DocumentType.SURVEILLANCE
    assert by_path["operational/CELL-ALPHA/cdr_dirty.csv"].document_type is DocumentType.CDR
    assert by_path["operational/txns.csv"].document_type is DocumentType.FINANCIAL
    assert by_path["documents/history.csv"].document_type is DocumentType.CRIMINAL_HISTORY
    assert by_path["documents/witness_statement.txt"].document_type is DocumentType.FIR
    assert by_path["documents/intel_note.txt"].document_type is DocumentType.INTEL


def test_malformed_and_unsupported_files_are_reported_not_silent(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    unsupported = {f.relative_path: f for f in scan.by_status("unsupported")}
    assert "operational/mystery.csv" in unsupported
    # Header is quoted so the operator sees the exact gap.
    assert "rec_id" in (unsupported["operational/mystery.csv"].reason or "")
    assert unsupported["operational/broken.json"].reason
    assert "operational/notes.xlsx" in unsupported


def test_ground_truth_is_excluded(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    gt = scan.by_status("excluded")
    assert any(f.relative_path == "ground_truth/answers.csv" for f in gt)
    assert not any(f.relative_path.startswith("ground_truth") for f in scan.accepted)


def test_metadata_is_not_treated_as_operational(corpus: Path):
    scan = ExternalSyntheticCorpusAdapter(root=corpus).scan()
    assert any(f.relative_path == "metadata/manifest.json" for f in scan.by_status("excluded"))
    assert not any(f.relative_path.startswith("metadata") for f in scan.accepted)


def test_missing_root_is_a_clear_error(tmp_path: Path):
    missing = tmp_path / "no_such_corpus"
    adapter = ExternalSyntheticCorpusAdapter(root=missing)
    scan = adapter.scan()
    assert not scan.ok
    assert str(missing) in scan.issues[0]
    with pytest.raises(ExternalCorpusError) as excinfo:
        list(adapter.iter_records())
    assert "no_such_corpus" in str(excinfo.value)


def test_missing_sections_are_a_clear_error(tmp_path: Path):
    (tmp_path / "corpus").mkdir()
    adapter = ExternalSyntheticCorpusAdapter(root=tmp_path / "corpus")
    scan = adapter.scan()
    assert not scan.ok
    joined = " ".join(scan.issues)
    assert "operational" in joined and "documents" in joined
    with pytest.raises(ExternalCorpusError):
        list(adapter.iter_records())


def test_records_carry_synthetic_provenance(corpus: Path):
    records = list(ExternalSyntheticCorpusAdapter(root=corpus).iter_records())
    assert records
    for record in records:
        assert record.is_synthetic()
        assert record.source_environment == "synthetic"
        assert record.source_confidence is SourceConfidence.SYNTHETIC
        assert record.metadata["synthetic_data_mode"] == "external"
        assert record.metadata["relative_path"]
        assert record.case_number.startswith("SYN-EXT/")


def test_case_grouping_is_deterministic(corpus: Path):
    records = {r.metadata["relative_path"]: r.case_number for r in ExternalSyntheticCorpusAdapter(root=corpus).iter_records()}
    assert records["operational/CELL-ALPHA/cdr.csv"] == "SYN-EXT/CELL-ALPHA"
    assert records["operational/txns.csv"] == "SYN-EXT/CORPUS-ALPHA"
    assert records["documents/witness_statement.txt"] == "SYN-EXT/CORPUS-ALPHA"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_relative_root_resolves_against_repo_root():
    from app.config import REPO_ROOT, Settings

    settings = Settings(synthetic_data_root=Path("backend/CrimeLink_Synthetic_Corpus_v1"))
    assert settings.resolved_synthetic_data_root == (
        REPO_ROOT / "backend/CrimeLink_Synthetic_Corpus_v1"
    ).resolve()
    sibling = Settings(synthetic_data_root=Path("../CrimeLink_Synthetic_Corpus_v1"))
    assert sibling.resolved_synthetic_data_root == (
        REPO_ROOT / "../CrimeLink_Synthetic_Corpus_v1"
    ).resolve()


def test_absolute_root_is_honoured(tmp_path: Path):
    from app.config import Settings

    settings = Settings(synthetic_data_root=tmp_path)
    assert settings.resolved_synthetic_data_root == tmp_path.resolve()


def test_mode_defaults_to_external():
    from app.config import BACKEND_ROOT, Settings

    settings = Settings()
    assert settings.synthetic_data_mode == "external"
    assert Settings(synthetic_data_mode="generate").synthetic_data_mode == "generate"
    assert settings.resolved_synthetic_data_root == (
        BACKEND_ROOT / "CrimeLink_Synthetic_Corpus_v1"
    ).resolve()


# ---------------------------------------------------------------------------
# Mode independence (both adapters co-exist behind the same boundary)
# ---------------------------------------------------------------------------


def test_external_and_generate_adapters_coexist(corpus: Path):
    assert "synthetic" in available_adapters()
    assert "synthetic_external" in available_adapters()

    external = get_source_adapter("synthetic_external", root=corpus)
    external_records = list(external.iter_records())
    assert external_records
    assert all(r.metadata.get("synthetic_data_mode") == "external" for r in external_records)
    assert all("corpus_seed" not in r.metadata for r in external_records)
    assert external.source_environment == "synthetic"

    generate = get_source_adapter("synthetic")
    generate_records = list(generate.iter_records())
    assert generate_records
    assert all(r.metadata.get("corpus_seed") is not None for r in generate_records)
    assert generate.source_environment == "synthetic"
    # The two modes produce distinct record id namespaces.
    ext_ids = {r.external_id for r in external_records}
    assert not any(r.external_id in ext_ids for r in generate_records)


# ---------------------------------------------------------------------------
# End-to-end ingestion through the real pipeline
# ---------------------------------------------------------------------------


async def _doc_rows(db, doc_ids):
    from sqlalchemy import select

    from app.db.models import CaseDocument

    rows = (
        (await db.execute(select(CaseDocument).where(CaseDocument.id.in_(doc_ids))))
        .scalars()
        .all()
    )
    return list(rows)


async def test_external_ingest_end_to_end(db, container, tmp_path: Path):
    from app.synthetic_corpus.external import await_pipeline_quiet, ingest_external_corpus

    root = _make_corpus(tmp_path / "corpus_alpha", salt="e2e")
    report = await ingest_external_corpus(root=root)

    assert report.uploaded == 7, report.to_dict()
    assert report.failed == 0
    assert {f.relative_path for f in report.files if f.status == "unsupported"} == set()
    assert len(report.unsupported) == 3
    assert len(report.excluded) == 2
    assert set(report.cases) == {"SYN-EXT/CELL-ALPHA", "SYN-EXT/CORPUS-ALPHA"}

    container.broker.drain()

    ids = [f.document_id for f in report.files if f.document_id]
    assert len(ids) == 7
    documents = await _doc_rows(db, ids)
    by_name = {d.filename: d for d in documents}
    # Provenance: every imported row is explicitly SYNTHETIC.
    for doc in documents:
        assert doc.source_confidence is SourceConfidence.SYNTHETIC
    # Good files complete the six-stage pipeline; the dirty CDR is quarantined
    # with a reason (the 5% bad-row rule), not silently half-ingested.
    quarantined = by_name["cdr_dirty.csv"]
    assert quarantined.ingestion_status is IngestionStatus.QUARANTINED
    assert quarantined.quarantined is True
    assert quarantined.failure_reason
    for name, doc in by_name.items():
        if name != "cdr_dirty.csv":
            assert doc.ingestion_status is IngestionStatus.COMPLETE, name

    # Cases carry the synthetic title marker.
    from sqlalchemy import select

    from app.db.models import Case

    cases = (
        (await db.execute(select(Case).where(Case.id.in_(report.cases.values()))))
        .scalars()
        .all()
    )
    assert all(c.title.startswith("[SYNTHETIC]") for c in cases)

    # Graph population happened through the pipeline (nodes + typed edges).
    graph = container.graph_store
    alpha_snapshot = graph.snapshot(report.cases["SYN-EXT/CELL-ALPHA"])
    corpus_snapshot = graph.snapshot(report.cases["SYN-EXT/CORPUS-ALPHA"])
    assert alpha_snapshot.nodes and corpus_snapshot.nodes
    assert alpha_snapshot.edges_by_type("CALLED")
    assert corpus_snapshot.edges_by_type("TRANSFER_TO")

    # Ground truth never reached the graph.
    for snapshot in (alpha_snapshot, corpus_snapshot):
        for node in snapshot.nodes.values():
            assert "ZXQ-9001" not in json.dumps(node.properties, default=str)

    stats = await await_pipeline_quiet(container, report, timeout_seconds=1)
    assert stats["documents_by_status"]["COMPLETE"] == 6
    assert stats["documents_by_status"]["QUARANTINED"] == 1
    assert stats["quarantined"][0]["filename"] == "cdr_dirty.csv"


async def test_reingestion_does_not_duplicate(db, container, tmp_path: Path):
    from app.synthetic_corpus.external import ingest_external_corpus

    root = _make_corpus(tmp_path / "corpus_alpha", salt="idem")
    first = await ingest_external_corpus(root=root)
    container.broker.drain()
    ids = [f.document_id for f in first.files if f.document_id]
    graph_before = container.graph_store.stats()

    second = await ingest_external_corpus(root=root)
    container.broker.drain()

    assert second.uploaded == 0, second.to_dict()
    assert second.duplicates == first.uploaded == 7
    documents = await _doc_rows(db, ids)
    assert len(documents) == 7  # no new rows for the same corpus
    assert container.graph_store.stats() == graph_before


async def test_nothing_is_ingested_from_forbidden_directories(db, container, tmp_path: Path):
    from sqlalchemy import select

    from app.db.models import CaseDocument
    from app.synthetic_corpus.external import ingest_external_corpus

    root = _make_corpus(tmp_path / "corpus_alpha", salt="iso")
    report = await ingest_external_corpus(root=root)
    container.broker.drain()

    filenames = {
        row[0]
        for row in (
            await db.execute(
                select(CaseDocument.filename).where(
                    CaseDocument.case_id.in_(report.cases.values())
                )
            )
        ).all()
    }
    assert "answers.csv" not in filenames
    assert "manifest.json" not in filenames


async def test_empty_corpus_reports_nothing_to_ingest(db, container, tmp_path: Path):
    from app.synthetic_corpus.external import ingest_external_corpus

    root = tmp_path / "corpus_empty"
    (root / "operational").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    report = await ingest_external_corpus(root=root)
    assert report.uploaded == 0
    assert any("nothing to ingest" in w.lower() or "no supported files" in w.lower() for w in report.warnings)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_dry_run_writes_nothing(tmp_path: Path, capsys):
    from app.synthetic_corpus.external import main

    root = _make_corpus(tmp_path / "corpus_alpha", salt="dry")
    assert main(["--root", str(root), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "operational/CELL-ALPHA/cdr.csv" in out
    assert "ground_truth/answers.csv" in out  # visible as excluded


def test_cli_dry_run_missing_root(tmp_path: Path, capsys):
    from app.synthetic_corpus.external import main

    missing = tmp_path / "gone"
    assert main(["--root", str(missing), "--dry-run"]) == 2
    out = capsys.readouterr().out
    assert str(missing) in out


def test_cli_ingest_missing_root_returns_2(tmp_path: Path, capsys):
    from app.synthetic_corpus.external import main

    assert main(["--root", str(tmp_path / "gone")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_umbrella_cli_routes_by_mode(tmp_path: Path, capsys):
    from app.cli import main as cli_main

    root = _make_corpus(tmp_path / "corpus_alpha", salt="cli")
    assert cli_main(["ingest-synthetic", "--mode", "external", "--root", str(root), "--dry-run"]) == 0
    # --root without external mode is an operator error, not silently ignored.
    assert cli_main(["ingest-synthetic", "--mode", "generate", "--root", str(root)]) == 2


# ---------------------------------------------------------------------------
# Admin API surface
# ---------------------------------------------------------------------------


async def test_admin_external_preview_endpoint(client, admin_headers, container, tmp_path: Path):
    root = _make_corpus(tmp_path / "corpus_alpha", salt="api-preview")
    response = client.get(
        "/api/v1/admin/synthetic/external/preview",
        params={"root": str(root)},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    statuses = {f["relative_path"]: f["status"] for f in data["files"]}
    assert statuses["operational/CELL-ALPHA/cdr.csv"] == "accepted"
    assert statuses["ground_truth/answers.csv"] == "excluded"
    assert statuses["operational/mystery.csv"] == "unsupported"


async def test_admin_preview_missing_root_is_an_error_report(client, admin_headers, tmp_path: Path):
    missing = tmp_path / "nope"
    response = client.get(
        "/api/v1/admin/synthetic/external/preview",
        params={"root": str(missing)},
        headers=admin_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert str(missing) in data["issues"][0]


async def test_admin_ingest_external_endpoint(client, admin_headers, container, tmp_path: Path):
    root = _make_corpus(tmp_path / "corpus_alpha", salt="api-ingest")
    response = client.post(
        "/api/v1/admin/synthetic/ingest",
        json={"adapter": "external", "root": str(root)},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "external"
    assert body["records_ingested"] == 7
    assert body["records_rejected"] == 3
    assert body["excluded_evaluation_files"] == 2
    # The operator action is audited.
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import async_session

    async with async_session() as session:
        rows = (
            (await session.execute(select(AuditLog.target_resource)))
            .scalars()
            .all()
        )
    assert any(target and "synthetic" in target for target in rows)


async def test_admin_ingest_preview_requires_admin(client, viewer_headers, tmp_path: Path):
    response = client.post(
        "/api/v1/admin/synthetic/ingest",
        json={"adapter": "external", "root": str(tmp_path)},
        headers=viewer_headers,
    )
    assert response.status_code == 403


async def test_admin_ingest_missing_corpus_returns_validation_error(
    client, admin_headers, tmp_path: Path
):
    missing = tmp_path / "nope"
    response = client.post(
        "/api/v1/admin/synthetic/ingest",
        json={"adapter": "external", "root": str(missing)},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert str(missing) in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# CrimeLink relational corpus mapping (cases.csv → Case records)
# ---------------------------------------------------------------------------


def _make_crimelink_corpus(root: Path) -> Path:
    """Minimal corpus using the documented CrimeLink_Synthetic_Corpus_v1 headers."""
    (root / "operational").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    (root / "metadata").mkdir(parents=True)
    (root / "ground_truth").mkdir(parents=True)

    (root / "operational" / "cases.csv").write_text(
        "case_id,case_number,registered_date,case_type,police_station,city,status\n"
        "C0001,FIR/2026/00001,2026-08-30,VEHICLE_THEFT,PS-10,Warangal,UNDER_REVIEW\n",
        encoding="utf-8",
    )
    (root / "operational" / "persons.csv").write_text(
        "person_id,full_name,gender,dob,address,city,state,status\n"
        "P0001,Ada Rao,F,,,Warangal,Telangana,ACTIVE\n",
        encoding="utf-8",
    )
    (root / "operational" / "phones.csv").write_text(
        "phone_id,phone_number,owner_person_id,status,source\n"
        "PH0001,9982796927,P0001,ACTIVE,SYNTHETIC\n"
        "PH0002,9876543210,P0001,ACTIVE,SYNTHETIC\n",
        encoding="utf-8",
    )
    (root / "operational" / "vehicles.csv").write_text(
        "vehicle_id,registration_number,vehicle_type,owner_person_id,color\n"
        "V0001,AP49IY3171,TRUCK,P0001,Black\n",
        encoding="utf-8",
    )
    (root / "operational" / "accounts.csv").write_text(
        "account_id,account_number,holder_person_id,bank_code,account_status\n"
        "AC0001,XX96152165,P0001,PUNB,ACTIVE\n"
        "AC0002,XX96152166,P0001,PUNB,ACTIVE\n",
        encoding="utf-8",
    )
    (root / "operational" / "locations.csv").write_text(
        "location_id,name,city,state,latitude,longitude\n"
        "L0001,Warehouse 9,Warangal,Telangana,17.9,79.6\n",
        encoding="utf-8",
    )
    (root / "operational" / "organizations.csv").write_text(
        "organization_id,name,organization_type,city,state\n"
        "O0001,Bharat Traders 884,TRANSPORT,Warangal,Telangana\n",
        encoding="utf-8",
    )
    (root / "operational" / "case_members.csv").write_text(
        "case_member_id,case_id,person_id,role\n"
        "CM00001,C0001,P0001,SUBJECT\n",
        encoding="utf-8",
    )
    (root / "operational" / "person_organizations.csv").write_text(
        "person_org_id,person_id,organization_id,role,start_date,end_date\n"
        "PO00001,P0001,O0001,CONTRACTOR,2025-01-01,\n",
        encoding="utf-8",
    )
    (root / "operational" / "cdr.csv").write_text(
        "cdr_id,timestamp,from_phone_id,to_phone_id,duration_seconds,call_type,cell_location_id,case_id\n"
        "CDR000001,2025-05-26 16:48:00,PH0001,PH0002,841,VOICE,L0001,C0001\n"
        "CDR000002,2025-05-26 17:00:00,PH0001,PH0002,60,VOICE,L0001,\n",
        encoding="utf-8",
    )
    (root / "operational" / "transactions.csv").write_text(
        "transaction_id,timestamp,from_account_id,to_account_id,amount_inr,transaction_type,location_id,case_id\n"
        "TX000001,2025-09-15 21:44:00,AC0001,AC0002,69462.27,CASH_DEPOSIT,L0001,C0001\n",
        encoding="utf-8",
    )
    (root / "operational" / "vehicle_sightings.csv").write_text(
        "sighting_id,vehicle_id,location_id,timestamp,case_id,source\n"
        "VS000001,V0001,L0001,2025-10-25 19:17:00,C0001,CCTV\n",
        encoding="utf-8",
    )
    (root / "operational" / "intelligence_reports.csv").write_text(
        "report_id,report_date,subject_person_id,location_id,case_id,source_type,summary\n"
        "IR00001,2024-06-18,P0001,L0001,C0001,SOURCE_REPORT,Field note near Warehouse 9.\n",
        encoding="utf-8",
    )
    (root / "operational" / "documents.csv").write_text(
        "document_id,case_id,document_type,file_path,language,source_environment\n"
        "DOC00001,C0001,FIR_NOTE,documents/DOC00001.txt,en,synthetic\n",
        encoding="utf-8",
    )
    (root / "documents" / "DOC00001.txt").write_text(
        "Case Reference: FIR/2026/00001\n"
        "Investigation note: Ada Rao was seen near Warehouse 9 with vehicle AP49IY3171.\n",
        encoding="utf-8",
    )
    (root / "ground_truth" / "answers.csv").write_text(
        "name,aliases\n" + GROUND_TRUTH_MARKER + ",The Answer\n",
        encoding="utf-8",
    )
    (root / "metadata" / "schema.json").write_text("{\"structured_sources\":\"CSV\"}\n", encoding="utf-8")
    return root


def test_crimelink_reference_tables_are_not_uploaded_as_documents(tmp_path: Path):
    root = _make_crimelink_corpus(tmp_path / "CrimeLink_Synthetic_Corpus_v1")
    scan = ExternalSyntheticCorpusAdapter(root=root).scan()
    assert scan.ok, scan.issues
    by_path = {f.relative_path: f for f in scan.files}
    assert by_path["operational/cases.csv"].status == "reference"
    assert by_path["operational/persons.csv"].status == "reference"
    assert by_path["operational/documents.csv"].status == "reference"
    assert by_path["operational/case_members.csv"].status == "reference"
    assert by_path["ground_truth/answers.csv"].status == "excluded"
    assert by_path["metadata/schema.json"].status == "excluded"
    assert by_path["documents/DOC00001.txt"].status == "accepted"


def test_crimelink_mapped_transactions_parse_through_financial_adapter(tmp_path: Path):
    from app.pipeline.adapters.financial import FinancialAdapter
    from app.pipeline.adapters.protocol import DocumentMeta
    from app.domain.enums import SourceConfidence

    root = _make_crimelink_corpus(tmp_path / "CrimeLink_Synthetic_Corpus_v1")
    record = next(
        r
        for r in ExternalSyntheticCorpusAdapter(root=root).iter_records()
        if r.filename.endswith("-transactions.csv")
    )
    parsed = FinancialAdapter().parse(
        record.content if isinstance(record.content, bytes) else record.content.encode(),
        DocumentMeta(
            doc_id="doc-tx",
            case_id="case-tx",
            filename=record.filename,
            document_type=DocumentType.FINANCIAL,
            source_confidence=SourceConfidence.SYNTHETIC,
        ),
    )
    assert parsed.blocks
    assert parsed.blocks[0].data["kind"] == "transfer"


def test_crimelink_records_use_dataset_case_numbers(tmp_path: Path):
    root = _make_crimelink_corpus(tmp_path / "CrimeLink_Synthetic_Corpus_v1")
    records = list(ExternalSyntheticCorpusAdapter(root=root).iter_records())
    assert records
    assert {r.case_number for r in records} == {"FIR/2026/00001"}
    assert all(r.is_synthetic() for r in records)
    kinds = {r.filename: r.document_type for r in records}
    assert kinds["C0001-case-record.txt"] is DocumentType.FIR
    assert kinds["C0001-persons.csv"] is DocumentType.CRIMINAL_HISTORY
    assert kinds["C0001-cdr.csv"] is DocumentType.CDR
    assert kinds["DOC00001.txt"] is DocumentType.FIR
    # Empty case_id CDR row is attached via phone owner → case_members.
    cdr = next(r for r in records if r.filename == "C0001-cdr.csv")
    assert cdr.metadata.get("row_count") == 2
    blob = cdr.content.decode() if isinstance(cdr.content, bytes) else cdr.content
    assert "9982796927" in blob
    assert GROUND_TRUTH_MARKER not in "".join(
        (r.content.decode() if isinstance(r.content, bytes) else r.content) for r in records
    )


async def test_crimelink_ingest_creates_dataset_cases(db, container, tmp_path: Path):
    from sqlalchemy import select

    from app.db.models import Case, CaseDocument
    from app.synthetic_corpus.external import ingest_external_corpus

    root = _make_crimelink_corpus(tmp_path / "CrimeLink_Synthetic_Corpus_v1")
    report = await ingest_external_corpus(root=root)
    assert report.failed == 0, report.to_dict()
    assert "FIR/2026/00001" in report.cases
    assert report.uploaded >= 4

    container.broker.drain()

    case = (
        await db.execute(select(Case).where(Case.case_number == "FIR/2026/00001"))
    ).scalar_one()
    assert case.jurisdiction_id == "SYN-DEV"
    assert case.title.startswith("[SYNTHETIC]")
    assert "VEHICLE_THEFT" in case.title

    filenames = {
        row[0]
        for row in (
            await db.execute(select(CaseDocument.filename).where(CaseDocument.case_id == case.id))
        ).all()
    }
    assert "answers.csv" not in filenames
    assert "DOC00001.txt" in filenames
    assert "C0001-cdr.csv" in filenames

    graph = container.graph_store.snapshot(case.id)
    assert graph.nodes
    dumped = json.dumps({k: n.properties for k, n in graph.nodes.items()}, default=str)
    assert GROUND_TRUTH_MARKER not in dumped
    assert graph.edges_by_type("CALLED")

    second = await ingest_external_corpus(root=root)
    assert second.uploaded == 0, second.to_dict()
    assert second.duplicates == report.uploaded


def test_local_dataset_is_detected_when_present():
    from app.config import BACKEND_ROOT

    root = BACKEND_ROOT / "CrimeLink_Synthetic_Corpus_v1"
    if not (root / "operational").is_dir() or not (root / "documents").is_dir():
        pytest.skip("local dataset is not present in this environment")
    scan = ExternalSyntheticCorpusAdapter(root=root).scan()
    assert scan.ok, scan.issues
    summary = scan.summary()
    assert summary["operational_files"] >= 14
    assert summary["document_files"] >= 1
    assert summary["ground_truth_excluded"] is True
    assert "cases.csv" in summary["schema_tables"]
    assert not any(f.relative_path.startswith("ground_truth") for f in scan.accepted)
    records = list(ExternalSyntheticCorpusAdapter(root=root).records_from_scan(scan))
    assert records
    assert all(r.case_number.startswith("FIR/") for r in records)
    assert all(r.is_synthetic() for r in records)
    assert {r.metadata.get("external_case_id") for r in records if r.metadata.get("external_case_id")}


async def test_admin_synthetic_status_endpoint(client, admin_headers, container):
    response = client.get("/api/v1/admin/synthetic/status", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "root" in data
    assert data["jurisdiction_id"] == "SYN-DEV"
    assert "scan" in data
    assert "busy" in data


async def test_admin_status_requires_admin(client, viewer_headers):
    response = client.get("/api/v1/admin/synthetic/status", headers=viewer_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Shared corpus validation (validation.validate_external)
# ---------------------------------------------------------------------------


def _write_table(root: Path, name: str, header: str, rows: list[str]) -> None:
    (root / "operational" / name).write_text(
        "\n".join([header] + rows) + "\n", encoding="utf-8"
    )


def _make_validated_corpus(root: Path) -> Path:
    """A minimal CrimeLink relational corpus that must pass validate_external."""
    (root / "operational").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    _write_table(root, "cases.csv", "case_id,case_number,registered_date,case_type,police_station,city,status",
                 ["C0001,FIR/2026/00001,2026-08-30,VEHICLE_THEFT,PS-10,Warangal,UNDER_REVIEW"])
    _write_table(root, "persons.csv", "person_id,full_name,gender,dob,address,city,state,status",
                 ["P0001,Ada Rao,F,,,Warangal,Telangana,ACTIVE",
                  "P0002,Ravi Kumar,M,,,Warangal,Telangana,ACTIVE"])
    _write_table(root, "phones.csv", "phone_id,phone_number,owner_person_id,status,source",
                 ["PH0001,9982796927,P0001,ACTIVE,SYNTHETIC",
                  "PH0002,9876543210,P0002,ACTIVE,SYNTHETIC"])
    _write_table(root, "vehicles.csv", "vehicle_id,registration_number,vehicle_type,owner_person_id,color",
                 ["V0001,AP49IY3171,TRUCK,P0001,Black"])
    _write_table(root, "accounts.csv", "account_id,account_number,holder_person_id,bank_code,account_status",
                 ["AC0001,111111111111,P0001,PUNB,ACTIVE",
                  "AC0002,222222222222,P0002,PUNB,ACTIVE"])
    _write_table(root, "locations.csv", "location_id,name,city,state,latitude,longitude",
                 ["L0001,Warehouse 9,Warangal,Telangana,17.9,79.6"])
    _write_table(root, "organizations.csv", "organization_id,name,organization_type,city,state",
                 ["O0001,Bharat Traders 884,TRANSPORT,Warangal,Telangana"])
    _write_table(root, "case_members.csv", "case_member_id,case_id,person_id,role",
                 ["CM00001,C0001,P0001,SUBJECT"])
    _write_table(root, "person_organizations.csv", "person_org_id,person_id,organization_id,role,start_date,end_date",
                 ["PO00001,P0001,O0001,CONTRACTOR,2025-01-01,"])
    _write_table(root, "cdr.csv",
                 "cdr_id,timestamp,from_phone_id,to_phone_id,duration_seconds,call_type,cell_location_id,case_id",
                 ["CDR000001,2025-05-26 16:48:00,PH0001,PH0001,841,VOICE,L0001,C0001"])
    _write_table(root, "transactions.csv",
                 "transaction_id,timestamp,from_account_id,to_account_id,amount_inr,transaction_type,location_id,case_id",
                 ["TX000001,2025-09-15 21:44:00,AC0001,AC0001,69462.27,CASH_DEPOSIT,L0001,C0001"])
    _write_table(root, "vehicle_sightings.csv",
                 "sighting_id,vehicle_id,location_id,timestamp,case_id,source",
                 ["VS000001,V0001,L0001,2025-10-25 19:17:00,C0001,CCTV"])
    _write_table(root, "intelligence_reports.csv",
                 "report_id,report_date,subject_person_id,location_id,case_id,source_type,summary",
                 ["IR00001,2024-06-18,P0001,L0001,C0001,SOURCE_REPORT,Field note near Warehouse 9."])
    return root


def test_validate_external_accepts_crimelink_corpus(tmp_path: Path):
    from app.synthetic_corpus.validation import validate_external

    root = _make_crimelink_corpus(tmp_path / "CrimeLink_Synthetic_Corpus_v1")
    assert validate_external(root) == []


def test_validate_external_accepts_build_external_output(tmp_path: Path):
    """The reference generator's own output passes the shared validator."""
    from app.synthetic_corpus.build_external import Builder
    from app.synthetic_corpus.validation import validate_external

    root = tmp_path / "built_corpus"
    builder = Builder(seed=20260902, root=root)
    for index in range(1, 6):
        builder.build_case(index)
    builder.build_background()
    builder.write()
    assert validate_external(root) == []


def test_validate_external_rejects_background_person_in_case_event(tmp_path: Path):
    from app.synthetic_corpus.validation import validate_external

    root = _make_validated_corpus(tmp_path / "corpus")
    # P0002 is NOT a case member (background).  A case-scoped CDR referencing
    # their phone must be flagged.
    _write_table(root, "cdr.csv",
                 "cdr_id,timestamp,from_phone_id,to_phone_id,duration_seconds,call_type,cell_location_id,case_id",
                 ["CDR000001,2025-05-26 16:48:00,PH0002,PH0002,841,VOICE,L0001,C0001"])
    problems = validate_external(root)
    assert any("non-member" in p or "background" in p for p in problems)


def test_validate_external_rejects_missing_references_and_owners(tmp_path: Path):
    from app.synthetic_corpus.validation import validate_external

    root = _make_validated_corpus(tmp_path / "corpus")
    # Transaction references an account that does not exist.
    _write_table(root, "transactions.csv",
                 "transaction_id,timestamp,from_account_id,to_account_id,amount_inr,transaction_type,location_id,case_id",
                 ["TX000001,2025-09-15 21:44:00,AC0099,AC0001,100.00,NEFT,L0001,C0001"])
    # A phone with no owner.
    _write_table(root, "phones.csv", "phone_id,phone_number,owner_person_id,status,source",
                 ["PH0001,9982796927,P0001,ACTIVE,SYNTHETIC",
                  "PH0002,9876543210,,ACTIVE,SYNTHETIC"])
    problems = validate_external(root)
    assert any("missing source account" in p for p in problems)
    assert any("no owner" in p for p in problems)


def test_validate_external_rejects_duplicate_event_identities(tmp_path: Path):
    from app.synthetic_corpus.validation import validate_external

    root = _make_validated_corpus(tmp_path / "corpus")
    _write_table(root, "vehicle_sightings.csv",
                 "sighting_id,vehicle_id,location_id,timestamp,case_id,source",
                 ["VS000001,V0001,L0001,2025-10-25 19:17:00,C0001,CCTV",
                  "VS000001,V0001,L0001,2025-10-25 19:17:00,C0001,CCTV"])
    problems = validate_external(root)
    assert any("duplicate" in p for p in problems)


def test_validate_external_rejects_impossible_timestamps(tmp_path: Path):
    from app.synthetic_corpus.validation import validate_external

    root = _make_validated_corpus(tmp_path / "corpus")
    _write_table(root, "cdr.csv",
                 "cdr_id,timestamp,from_phone_id,to_phone_id,duration_seconds,call_type,cell_location_id,case_id",
                 ["CDR000001,1899-01-01 00:00:00,PH0001,PH0001,841,VOICE,L0001,C0001"])
    problems = validate_external(root)
    assert any("precedes" in p for p in problems)
