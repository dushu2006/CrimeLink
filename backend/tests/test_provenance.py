"""Exact-source provenance: capture during ingestion, resolution afterwards.

These tests protect the guarantee that makes CrimeLink traceable rather than
merely evidenced: that a relationship can be followed back to the *original*
corpus row (file + row number + fields), not just to the derived document the
pipeline happened to ingest.

The corpus fixture mirrors the documented CrimeLink schema (cases.csv +
persons.csv + cdr.csv + ...), because the relational branch of the adapter is
exactly the branch that rewrites rows and therefore the one that can lose
provenance.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.adapters.sources.synthetic_external import ExternalSyntheticCorpusAdapter
from app.domain.models import ORIGIN_COLUMN, OriginRef
from app.services import source_viewer


# ---------------------------------------------------------------------------
# Fixture corpus
# ---------------------------------------------------------------------------


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """A miniature corpus in the documented relational shape."""
    root = tmp_path / "Corpus_v1"
    op = root / "operational"

    _write_csv(
        op / "cases.csv",
        ["case_id", "case_number", "registered_date", "case_type", "police_station", "city", "status"],
        [{"case_id": "CPV01", "case_number": "FIR/2099/09001", "registered_date": "2026-01-02",
          "case_type": "FRAUD", "police_station": "PS-01", "city": "Kurnool", "status": "OPEN"}],
    )
    _write_csv(
        op / "persons.csv",
        ["person_id", "full_name", "gender", "dob", "address", "city", "state", "status"],
        [
            {"person_id": "P0001", "full_name": "Asha Reddy", "gender": "F", "dob": "",
             "address": "", "city": "Kurnool", "state": "Andhra Pradesh", "status": "ACTIVE"},
            {"person_id": "P0002", "full_name": "Vikram Naidu", "gender": "M", "dob": "",
             "address": "", "city": "Kurnool", "state": "Andhra Pradesh", "status": "ACTIVE"},
        ],
    )
    _write_csv(
        op / "case_members.csv",
        ["case_member_id", "case_id", "person_id", "role"],
        [
            {"case_member_id": "CM00001", "case_id": "CPV01", "person_id": "P0001", "role": "SUSPECT"},
            {"case_member_id": "CM00002", "case_id": "CPV01", "person_id": "P0002", "role": "WITNESS"},
        ],
    )
    _write_csv(
        op / "phones.csv",
        ["phone_id", "phone_number", "owner_person_id", "status", "source"],
        [
            {"phone_id": "PH0001", "phone_number": "9812345670", "owner_person_id": "P0001",
             "status": "ACTIVE", "source": "SYNTHETIC"},
            {"phone_id": "PH0002", "phone_number": "9812345671", "owner_person_id": "P0002",
             "status": "ACTIVE", "source": "SYNTHETIC"},
        ],
    )
    # Three padding rows first, so the interesting call is NOT row 2 — a test
    # that passes only because everything is on the first row proves nothing.
    cdr_rows = [
        {"cdr_id": f"CDR00000{i}", "timestamp": f"2026-01-0{i} 10:00:00",
         "from_phone_id": "PH0002", "to_phone_id": "PH0001", "duration_seconds": "10",
         "call_type": "SMS", "cell_location_id": "L0001", "case_id": "CPV01"}
        for i in (1, 2, 3)
    ]
    cdr_rows.append(
        {"cdr_id": "CDR000099", "timestamp": "2026-01-09 21:34:00",
         "from_phone_id": "PH0001", "to_phone_id": "PH0002", "duration_seconds": "842",
         "call_type": "VOICE", "cell_location_id": "L0001", "case_id": "CPV01"}
    )
    _write_csv(
        op / "cdr.csv",
        ["cdr_id", "timestamp", "from_phone_id", "to_phone_id", "duration_seconds",
         "call_type", "cell_location_id", "case_id"],
        cdr_rows,
    )
    _write_csv(
        op / "documents.csv",
        ["document_id", "case_id", "document_type", "file_path", "language", "source_environment"],
        [{"document_id": "DOC00001", "case_id": "CPV01", "document_type": "FIR_NOTE",
          "file_path": "documents/DOC00001.txt", "language": "en", "source_environment": "synthetic"}],
    )

    docs = root / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "DOC00001.txt").write_text(
        "\n".join(
            [
                "Case note line one.",
                "Case note line two.",
                "Asha Reddy contacted Vikram Naidu on 9812345671.",
                "Case note line four.",
                "Case note line five.",
            ]
        ),
        encoding="utf-8",
    )
    # Evaluation material that must never become investigator-visible evidence.
    gt = root / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    (gt / "relationship_ground_truth.json").write_text('{"answer": "secret"}', encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def test_derived_cdr_rows_carry_origin_of_the_real_corpus_row(corpus: Path) -> None:
    """The per-case CDR document must know which cdr.csv row produced each call."""
    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    records = list(adapter.records_from_scan(adapter.scan()))
    cdr = next(r for r in records if r.filename.endswith("-cdr.csv"))

    rows = list(csv.DictReader(cdr.content.decode().splitlines()))
    origins = [OriginRef.decode(row[ORIGIN_COLUMN]) for row in rows]
    assert all(o is not None for o in origins)

    target = next(o for o in origins if o.record_id == "CDR000099")
    assert target.file == "operational/cdr.csv"
    # Header is line 1 and CDR000099 is the 4th data row, so line 5.
    assert target.row == 5
    assert target.values["from_phone_id"] == "PH0001"
    assert target.values["to_phone_id"] == "PH0002"

    # And that row number must address the real file, not a re-numbered slice.
    real = corpus.joinpath("operational/cdr.csv").read_text().splitlines()
    assert real[target.row - 1].startswith("CDR000099")


def test_origin_row_survives_phone_number_substitution(corpus: Path) -> None:
    """IDs are replaced by values in the derived row; the origin keeps the IDs."""
    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    records = list(adapter.records_from_scan(adapter.scan()))
    cdr = next(r for r in records if r.filename.endswith("-cdr.csv"))
    row = next(
        r for r in csv.DictReader(cdr.content.decode().splitlines())
        if r["calling_number"] == "9812345670"
    )
    origin = OriginRef.decode(row[ORIGIN_COLUMN])
    assert origin.values["from_phone_id"] == "PH0001"


def test_origin_column_is_not_mistaken_for_operational_data(corpus: Path) -> None:
    """The reserved column must never reach schema detection or the viewer."""
    from app.pipeline.adapters.protocol import strip_origin_column

    assert strip_origin_column(["a", ORIGIN_COLUMN, "b"]) == ["a", "b"]


def test_verbatim_documents_are_their_own_origin(corpus: Path) -> None:
    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    records = list(adapter.records_from_scan(adapter.scan()))
    doc = next(r for r in records if r.filename == "DOC00001.txt")
    origin = OriginRef.from_dict(doc.metadata["document_origin"])
    assert origin.file == "documents/DOC00001.txt"
    assert doc.metadata["verbatim"] is True


# ---------------------------------------------------------------------------
# Pipeline persistence
# ---------------------------------------------------------------------------


def test_pipeline_persists_source_references_pointing_at_the_corpus(corpus: Path) -> None:
    """End-to-end: ingest, then confirm stored references address cdr.csv rows."""
    import asyncio

    from sqlalchemy import select

    from app.db.models import SourceReference
    from app.db.session import async_session
    from app.synthetic_corpus.external import ingest_external_corpus

    async def run() -> list[SourceReference]:
        from app.container import get_container
        from app.synthetic_corpus.external import await_pipeline_quiet

        report = await ingest_external_corpus(root=corpus, safety_confirmed=True)
        # The inline broker processes documents in the background, so the
        # references only exist once the pipeline has drained.
        await await_pipeline_quiet(get_container(), report, timeout_seconds=60)
        async with async_session() as session:
            return list(
                (
                    await session.execute(
                        select(SourceReference).where(
                            SourceReference.origin_file == "operational/cdr.csv"
                        )
                    )
                ).scalars()
            )

    refs = asyncio.run(run())
    assert refs, "ingestion recorded no source references for cdr.csv"
    by_record = {r.record_id: r for r in refs}
    assert "CDR000099" in by_record
    assert by_record["CDR000099"].row_number == 5
    assert by_record["CDR000099"].source_type == "csv"
    assert "from_phone_id" in by_record["CDR000099"].field_names


def test_reimport_is_idempotent_for_source_references(corpus: Path) -> None:
    """Importing twice must converge, not duplicate provenance."""
    import asyncio

    from sqlalchemy import func, select

    from app.db.models import SourceReference
    from app.db.session import async_session
    from app.synthetic_corpus.external import ingest_external_corpus

    async def count_after(times: int) -> int:
        from app.container import get_container
        from app.synthetic_corpus.external import await_pipeline_quiet

        for _ in range(times):
            report = await ingest_external_corpus(root=corpus, safety_confirmed=True)
            await await_pipeline_quiet(get_container(), report, timeout_seconds=60)
        async with async_session() as session:
            return int(
                (await session.execute(select(func.count(SourceReference.id)))).scalar_one()
            )

    first = asyncio.run(count_after(1))
    second = asyncio.run(count_after(1))
    assert first == second


# ---------------------------------------------------------------------------
# Resolution / source viewer
# ---------------------------------------------------------------------------


def test_csv_window_highlights_the_requested_row_with_context(corpus: Path) -> None:
    window = source_viewer.read_window(
        "operational/cdr.csv", row=5, context=2, root=corpus
    )
    assert window.source_type == "csv"
    assert window.highlight == [5]
    assert "cdr_id" in window.columns
    highlighted = next(r for r in window.rows if r["row"] == 5)
    assert highlighted["values"]["cdr_id"] == "CDR000099"
    # Context either side, so the investigator sees the row in situ.
    assert {r["row"] for r in window.rows} == {3, 4, 5}


def test_text_window_highlights_line_range_with_surrounding_context(corpus: Path) -> None:
    window = source_viewer.read_window(
        "documents/DOC00001.txt", line_start=3, line_end=3, context=2, root=corpus
    )
    assert window.source_type == "txt"
    assert window.highlight == [3]
    text = {line["line"]: line["text"] for line in window.lines}
    assert "Asha Reddy contacted Vikram Naidu" in text[3]
    assert 1 in text and 5 in text          # context above and below
    assert window.total_units == 5


def test_large_csv_is_not_loaded_whole(corpus: Path) -> None:
    """A window must stay a window regardless of file size."""
    big = corpus / "operational" / "big.csv"
    _write_csv(
        big, ["id", "value"], [{"id": str(i), "value": f"v{i}"} for i in range(1, 5001)]
    )
    window = source_viewer.read_window("operational/big.csv", row=4000, context=3, root=corpus)
    assert window.total_units == 5001
    assert len(window.rows) == 7
    assert window.truncated is True


def test_source_paths_cannot_escape_the_dataset_root(corpus: Path) -> None:
    """An evidence URL must never become an arbitrary file read."""
    for attempt in ("../../../etc/passwd", "/etc/passwd", "operational/../../secret"):
        with pytest.raises(Exception):
            source_viewer.read_window(attempt, root=corpus)


def test_ground_truth_is_never_exposed_as_a_source(corpus: Path) -> None:
    """Ground truth may exist on disk but is not investigator-visible evidence."""
    from app.adapters.sources.synthetic_external import NEVER_INGEST_COMPONENTS

    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    scan = adapter.scan()
    excluded = {f.relative_path for f in scan.by_status("excluded")}
    assert any("ground_truth" in path for path in excluded)
    assert "groundtruth" in NEVER_INGEST_COMPONENTS
