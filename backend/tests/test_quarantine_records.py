"""Corpus rows that cannot be attached to a case must be quarantined, not dropped.

The adapter builds per-case documents, so a row it cannot route to a case never
becomes one.  Those rows used to be counted in a scan warning and discarded,
which made coverage unauditable: nothing recorded which rows were missing or
why.  A row that vanishes silently is indistinguishable from a row that was
never in the corpus, and that is exactly the ambiguity an investigator cannot
afford.

These tests pin the two things that matter: nothing is discarded silently, and
what is kept is enough to reopen the original record.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.adapters.sources.synthetic_external import ExternalSyntheticCorpusAdapter


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """A corpus containing one attachable row and three unattachable ones.

    Each unattachable row fails for a *different* reason, because the whole
    point of quarantining is to tell those reasons apart.
    """
    root = tmp_path / "Corpus_q"
    op = root / "operational"

    _write_csv(
        op / "cases.csv",
        ["case_id", "case_number", "registered_date", "case_type", "police_station", "city", "status"],
        [{"case_id": "CQ01", "case_number": "FIR/2098/07001", "registered_date": "2026-02-02",
          "case_type": "FRAUD", "police_station": "PS-07", "city": "Guntur", "status": "OPEN"}],
    )
    _write_csv(
        op / "persons.csv",
        ["person_id", "full_name", "gender", "dob", "address", "city", "state", "status"],
        [
            # P0001 is party to the case; P0002 exists but is in no case.
            {"person_id": "P0001", "full_name": "Meera Rao", "gender": "F", "dob": "",
             "address": "", "city": "Guntur", "state": "Andhra Pradesh", "status": "ACTIVE"},
            {"person_id": "P0002", "full_name": "Background Person", "gender": "M", "dob": "",
             "address": "", "city": "Guntur", "state": "Andhra Pradesh", "status": "ACTIVE"},
        ],
    )
    _write_csv(
        op / "case_members.csv",
        ["case_member_id", "case_id", "person_id", "role"],
        [{"case_member_id": "CM1", "case_id": "CQ01", "person_id": "P0001", "role": "SUSPECT"}],
    )
    _write_csv(
        op / "phones.csv",
        ["phone_id", "phone_number", "owner_person_id", "status", "source"],
        [
            {"phone_id": "PH0001", "phone_number": "9700000001", "owner_person_id": "P0001",
             "status": "ACTIVE", "source": "SYNTHETIC"},
            {"phone_id": "PH0002", "phone_number": "9700000002", "owner_person_id": "P0002",
             "status": "ACTIVE", "source": "SYNTHETIC"},
            # An unregistered handset: no owner at all.
            {"phone_id": "PH0003", "phone_number": "9700000003", "owner_person_id": "",
             "status": "ACTIVE", "source": "SYNTHETIC"},
        ],
    )
    _write_csv(
        op / "cdr.csv",
        ["cdr_id", "timestamp", "from_phone_id", "to_phone_id", "duration_seconds",
         "call_type", "cell_location_id", "case_id"],
        [
            # row 2: attachable through P0001's case membership.
            {"cdr_id": "CDR000001", "timestamp": "2026-02-03 10:00:00", "from_phone_id": "PH0001",
             "to_phone_id": "PH0002", "duration_seconds": "60", "call_type": "VOICE",
             "cell_location_id": "L1", "case_id": ""},
            # row 3: both parties known, neither in any case.
            {"cdr_id": "CDR000002", "timestamp": "2026-02-03 11:00:00", "from_phone_id": "PH0002",
             "to_phone_id": "PH0002", "duration_seconds": "30", "call_type": "SMS",
             "cell_location_id": "L1", "case_id": ""},
            # row 4: unregistered handsets, no owner resolvable at all.
            {"cdr_id": "CDR000003", "timestamp": "2026-02-03 12:00:00", "from_phone_id": "PH0003",
             "to_phone_id": "PH0003", "duration_seconds": "15", "call_type": "VOICE",
             "cell_location_id": "L1", "case_id": ""},
            # row 5: names a case that does not exist in cases.csv.
            {"cdr_id": "CDR000004", "timestamp": "2026-02-03 13:00:00", "from_phone_id": "PH0001",
             "to_phone_id": "PH0002", "duration_seconds": "45", "call_type": "VOICE",
             "cell_location_id": "L1", "case_id": "CQ99"},
        ],
    )
    _write_csv(
        op / "documents.csv",
        ["document_id", "case_id", "document_type", "file_path", "language", "source_environment"],
        [],
    )
    (root / "documents").mkdir(parents=True, exist_ok=True)
    return root


def _quarantine(corpus: Path) -> list[dict]:
    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    scan = adapter.scan()
    list(adapter.records_from_scan(scan))   # drives the routing
    return [q for q in scan.quarantined if q["source_type"] == "cdr"]


# ---------------------------------------------------------------------------
# Nothing is discarded silently
# ---------------------------------------------------------------------------


def test_unattachable_rows_are_quarantined_rather_than_dropped(corpus: Path) -> None:
    rows = _quarantine(corpus)
    assert {q["record_id"] for q in rows} == {"CDR000002", "CDR000003", "CDR000004"}


def test_attachable_rows_are_not_quarantined(corpus: Path) -> None:
    """Quarantining must not become a dumping ground for rows that did import."""
    assert "CDR000001" not in {q["record_id"] for q in _quarantine(corpus)}


def test_every_row_is_either_imported_or_quarantined(corpus: Path) -> None:
    """The accounting must close: no row may fall between the two outcomes."""
    adapter = ExternalSyntheticCorpusAdapter(root=corpus)
    scan = adapter.scan()
    records = list(adapter.records_from_scan(scan))

    total_in_corpus = sum(
        1 for _ in csv.DictReader((corpus / "operational" / "cdr.csv").open())
    )
    quarantined = len([q for q in scan.quarantined if q["source_type"] == "cdr"])

    derived = next(r for r in records if r.filename.endswith("-cdr.csv"))
    imported = len(list(csv.DictReader(derived.content.decode().splitlines())))

    assert imported + quarantined == total_in_corpus


# ---------------------------------------------------------------------------
# The reason is specific enough to act on
# ---------------------------------------------------------------------------


def test_reasons_distinguish_background_population_from_referential_gaps(
    corpus: Path,
) -> None:
    """"Not in any case" and "cannot be looked up" need different responses.

    The first is ordinary background data; the second is a data-quality defect.
    Collapsing them into one "unresolved" bucket would hide real corpus faults.
    """
    by_id = {q["record_id"]: q for q in _quarantine(corpus)}
    assert by_id["CDR000002"]["reason_code"] == "subject_in_no_case"
    assert by_id["CDR000003"]["reason_code"] == "unresolvable_subject"
    assert by_id["CDR000004"]["reason_code"] == "unknown_case_id"


def test_a_row_naming_a_missing_case_records_that_case_id(corpus: Path) -> None:
    """The offending identifier is what makes the row fixable."""
    by_id = {q["record_id"]: q for q in _quarantine(corpus)}
    assert by_id["CDR000004"]["unresolved_case_id"] == "CQ99"
    # Rows with a blank case_id have nothing to report, and must not invent one.
    assert by_id["CDR000002"]["unresolved_case_id"] is None


# ---------------------------------------------------------------------------
# Enough coordinates to reopen the original record
# ---------------------------------------------------------------------------


def test_quarantined_rows_address_the_real_corpus_line(corpus: Path) -> None:
    """The stored row number must locate the record in the *original* file."""
    by_id = {q["record_id"]: q for q in _quarantine(corpus)}
    lines = (corpus / "operational" / "cdr.csv").read_text().splitlines()

    for record_id, expected_line in (
        ("CDR000002", 3), ("CDR000003", 4), ("CDR000004", 5),
    ):
        entry = by_id[record_id]
        assert entry["row_number"] == expected_line
        assert entry["origin_file"] == "operational/cdr.csv"
        assert lines[expected_line - 1].startswith(record_id)


def test_quarantined_rows_retain_their_source_fields(corpus: Path) -> None:
    entry = {q["record_id"]: q for q in _quarantine(corpus)}["CDR000002"]
    values = entry["field_values"]
    assert values["from_phone_id"] == "PH0002"
    assert values["call_type"] == "SMS"
    # Internal bookkeeping keys must not leak into the stored record.
    assert not [k for k in values if k.startswith("__crimelink")]


# ---------------------------------------------------------------------------
# Persistence and idempotency
# ---------------------------------------------------------------------------


def test_persisting_the_same_rows_twice_does_not_duplicate_them(corpus: Path) -> None:
    """A re-import must converge, matching the source-reference guarantee."""
    import asyncio

    from sqlalchemy import func, select

    from app.db.models import QuarantinedRecord
    from app.db.session import async_session, init_db
    from app.services.quarantine import persist_quarantined_records

    rows = _quarantine(corpus)

    async def run() -> tuple[int, int, int]:
        await init_db()
        async with async_session() as session:
            first = await persist_quarantined_records(
                session, rows, dataset_version="1.0", import_run_id="run-1"
            )
            await session.commit()
        async with async_session() as session:
            second = await persist_quarantined_records(
                session, rows, dataset_version="1.0", import_run_id="run-2"
            )
            await session.commit()
        async with async_session() as session:
            total = (
                await session.execute(
                    select(func.count(QuarantinedRecord.id)).where(
                        QuarantinedRecord.origin_file == "operational/cdr.csv",
                        QuarantinedRecord.record_id.in_(
                            [r["record_id"] for r in rows]
                        ),
                    )
                )
            ).scalar_one()
        return first, second, int(total)

    first, second, total = asyncio.run(run())
    assert first == len(rows)
    assert second == 0, "a second import must not create duplicate quarantine rows"
    assert total == len(rows)
