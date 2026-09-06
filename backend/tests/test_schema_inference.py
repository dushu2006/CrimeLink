"""A header is a claim; the values are the evidence.

Real exports lie. A template gets reused after a column is inserted, an export
job writes six header names above eight columns of data, a "person_id" field
ends up holding dates. If the mapper believes the header regardless, that lie
becomes 600 people named ``2022-01-29`` on the People page -- entities that no
row in the dataset ever asserted.

These tests pin the behaviour that stops it:

* a column whose values contradict its header is mapped from the data;
* identifiers are learned from the dataset's own self-consistent tables, so a
  column of ``ACCT_*`` values is an account reference whatever it is labelled;
* role columns (``from_person``, ``owner_id``) keep their specific meaning --
  the lexicon may not flatten them into a generic id;
* every such decision is recorded per file and offered to an operator to
  accept, rather than being buried in an import log.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.datasets import schema_map as sm
from app.db.models import DatasetEntity, DatasetFile
from app.db.session import async_session

# --------------------------------------------------------------------------- #
# Fixture data: a dataset whose reference tables are clean and whose "export"
# has a header row that slipped two columns to the left.
# --------------------------------------------------------------------------- #

ACCOUNTS_CSV = "account_id,account_number,bank_name,branch_city\n" + "".join(
    f"ACCT_{n:04d},98221012202373{n:02d},Deccan Mercantile Bank,Kochi\n"
    for n in range(1, 9)
)

_NAMES = [
    "Meera Kurian",
    "Harish Varma",
    "Anil Menon",
    "Latha Nair",
    "Devika Pillai",
    "Rajesh Iyer",
    "Fatima Rasheed",
    "Joseph Mathew",
]
PERSONS_CSV = "person_id,full_name,city\n" + "".join(
    f"PERSON_{n:04d},{name},Kochi\n" for n, name in enumerate(_NAMES, start=1)
)

# Six header names, eight columns of data: the real columns are
# txn_id, date, account_id, person_id, category, amount, counterparty, note.
BROKEN_HEADER_CSV = (
    "account_id,person_id,transaction_type,amount,counterparty,description\n"
) + "".join(
    f"TXN_{n:04d},2022-0{(n % 9) + 1}-1{n % 9},ACCT_{n:04d},PERSON_{n:04d},"
    f"school fee,1473{n},VENDOR_0{n:02d},school fee\n"
    for n in range(1, 9)
)

# Role columns that a naive identifier lexicon would happily destroy.
SIGHTINGS_CSV = (
    "sighting_id,vehicle_id,date,observed_driver\n"
    "SIGHT_0001,VEH_0001,2024-03-01,PERSON_0001\n"
    "SIGHT_0002,VEH_0002,2024-03-02,PERSON_0002\n"
    "SIGHT_0003,VEH_0001,2024-03-03,PERSON_0003\n"
)

VEHICLES_CSV = (
    "vehicle_id,registration,make_model\n"
    "VEH_0001,KL07AB1234,Maruti Baleno\n"
    "VEH_0002,KL08CD5678,Hyundai i20\n"
)


async def _await_job(client, headers, job_id: str, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        body = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if body["terminal"]:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


async def _import(client, headers, name: str = "Mixed quality export") -> dict:
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        files=[
            ("files", ("accounts.csv", ACCOUNTS_CSV, "text/csv")),
            ("files", ("persons.csv", PERSONS_CSV, "text/csv")),
            ("files", ("vehicles.csv", VEHICLES_CSV, "text/csv")),
            ("files", ("sightings.csv", SIGHTINGS_CSV, "text/csv")),
            ("files", ("ledger_export.csv", BROKEN_HEADER_CSV, "text/csv")),
        ],
        data={"name": name},
    )
    assert response.status_code == 200, response.text
    final = await _await_job(client, headers, response.json()["job_id"])
    assert final["status"] == "SUCCEEDED", final.get("error") or final
    return final["result"]


# --------------------------------------------------------------------------- #
# Unit level: the mapper itself
# --------------------------------------------------------------------------- #


def test_values_that_contradict_a_header_win():
    """``person_id`` full of dates is not a person id, whatever the header says."""
    rows = [
        {"person_id": "2022-01-29"},
        {"person_id": "2022-02-08"},
        {"person_id": "2024-06-28"},
        {"person_id": "2024-07-02"},
    ]
    mapping = sm.map_table(["person_id"], rows)
    assert mapping.contradictions == ["person_id"]
    column = mapping.columns[0]
    assert column.canonical != "PERSON.id"
    assert mapping.needs_review is True
    assert any("does not" in note or "do not match" in note for note in mapping.notes)


def test_a_header_supported_by_its_values_is_left_alone():
    rows = [{"person_id": f"PERSON_{n:04d}"} for n in range(1, 6)]
    mapping = sm.map_table(["person_id"], rows)
    assert mapping.contradictions == []
    assert mapping.columns[0].canonical == "PERSON.id"
    assert mapping.columns[0].basis == "alias"


def test_amounts_in_indian_notation_are_not_treated_as_a_contradiction():
    """``27.87 lakh`` is an amount; the agreement check uses the normalizer."""
    rows = [{"declared_value": f"{n}.5 lakh"} for n in range(20, 25)]
    mapping = sm.map_table(["declared_value"], rows)
    assert mapping.contradictions == []
    assert mapping.columns[0].canonical == "PROPERTY.value"


def test_lexicon_learns_prefixes_and_overrules_a_shifted_header():
    lexicon = sm.SchemaLexicon()
    lexicon.learn_column("ACCOUNT.id", [f"ACCT_{n:04d}" for n in range(40)])
    lexicon.learn_column("TRANSACTION.id", [f"TXN_{n:04d}" for n in range(40)])

    rows = [{"account_id": f"TXN_{n:04d}"} for n in range(10)]
    mapping = sm.map_table(["account_id"], rows, lexicon)
    assert mapping.columns[0].canonical == "TRANSACTION.id"
    assert mapping.columns[0].basis == "dataset_lexicon"


def test_lexicon_does_not_flatten_role_columns():
    """``observed_driver`` says more than "a person key" -- keep it."""
    lexicon = sm.SchemaLexicon()
    lexicon.learn_column("PERSON.id", [f"PERSON_{n:04d}" for n in range(40)])

    rows = [{"observed_driver": f"PERSON_{n:04d}"} for n in range(10)]
    mapping = sm.map_table(["observed_driver"], rows, lexicon)
    assert mapping.columns[0].canonical == "SIGHTING.driver"
    assert mapping.contradictions == []


def test_lexicon_is_not_shared_between_datasets():
    """One dataset's ``ACC_`` may mean nothing in another."""
    first = sm.SchemaLexicon()
    first.learn_column("ACCOUNT.id", [f"ACCT_{n:04d}" for n in range(40)])
    second = sm.SchemaLexicon()
    assert first.as_dict() == {"ACCT_": "ACCOUNT.id"}
    assert second.as_dict() == {}


# --------------------------------------------------------------------------- #
# End to end: through the real import endpoint
# --------------------------------------------------------------------------- #


async def test_broken_header_export_does_not_invent_entities(client, admin_headers, container):
    report = await _import(client, admin_headers)
    dataset_id = report["dataset_id"]

    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(DatasetEntity.entity_type, DatasetEntity.name).where(
                        DatasetEntity.dataset_id == dataset_id
                    )
                )
            ).all()
        )

    people = sorted(name for kind, name in rows if kind == "PERSON")
    accounts = sorted(name for kind, name in rows if kind == "ACCOUNT")

    # Four people are asserted by the dataset. None of them is a date.
    assert people == sorted(_NAMES), people
    # Eight accounts are asserted by the dataset. None of them is a TXN key.
    assert len(accounts) == 8, accounts
    assert not [name for name in accounts if str(name).startswith("TXN_")]


async def test_the_operator_is_shown_what_was_inferred(client, admin_headers, container):
    report = await _import(client, admin_headers, name="Reviewable export")
    dataset_id = report["dataset_id"]

    listing = client.get(
        f"/api/v1/datasets/{dataset_id}/mappings?needs_review=true",
        headers=admin_headers,
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()
    paths = [item["path"] for item in body["items"]]
    assert any("ledger_export" in path for path in paths), body

    entry = next(item for item in body["items"] if "ledger_export" in item["path"])
    assert entry["needs_review"] is True
    assert entry["accepted_at"] is None
    # The reason is stated in words, naming the column.
    text = " ".join(note for sheet in entry["notes"] for note in sheet["notes"])
    assert "person_id" in text or "amount" in text, text

    # The clean reference tables are not dragged into the review queue.
    assert not [p for p in paths if p.endswith("persons.csv")], paths

    accepted = client.post(
        f"/api/v1/datasets/{dataset_id}/mappings/accept",
        headers=admin_headers,
        json={"file_ids": [entry["file_id"]]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] == 1

    after = client.get(
        f"/api/v1/datasets/{dataset_id}/mappings?needs_review=true",
        headers=admin_headers,
    ).json()
    assert not [item for item in after["items"] if item["file_id"] == entry["file_id"]]

    async with async_session() as session:
        row = await session.get(DatasetFile, entry["file_id"])
        assert row is not None
        assert row.mapping_accepted_at is not None
        assert row.mapping_accepted_by


async def test_accepting_mappings_requires_admin(client, investigator_headers, admin_headers, container):
    report = await _import(client, admin_headers, name="Permission check")
    dataset_id = report["dataset_id"]
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/mappings/accept",
        headers=investigator_headers,
        json={"file_ids": []},
    )
    assert response.status_code == 403, response.text
