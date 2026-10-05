"""Case extraction and graph quality for arbitrary datasets.

Every corpus here is written into ``tmp_path`` and pushed through the real
import pipeline, so nothing depends on the bundled CrimeLink corpus, on a
folder name, or on a filename.  The behaviours pinned down are the ones a
hosted import was observed to get wrong:

* a dataset that states ten cases must produce ten cases, and a dataset whose
  cases are stated only inside its documents must not collapse into the single
  container case;
* phones, bank accounts and vehicles must reach their owners even when the
  ownership is stated in a different file from the register;
* a code such as ``CP_01`` must resolve to the record the dataset defines it
  as, whichever file the definition is in, and must never be dressed up as an
  organisation when the dataset does not define it at all;
* the same hard identifier must not become two graph nodes;
* provenance must survive normalization for both entities and relationships.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.datasets import readers
from app.datasets import schema_map as sm
from app.datasets.normalize import Normalizer
from app.datasets.pipeline import ImportOptions, run_import
from app.db.models import Case, DatasetEntity, DatasetFile, DatasetRelationship
from app.db.session import async_session

JURISDICTION = "TEST"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _table(name: str, columns: list[str], rows: list[dict]) -> readers.Table:
    return readers.Table(name=name, columns=columns, rows=rows)


def _ingest(
    normalizer: Normalizer,
    file: str,
    columns: list[str],
    rows: list[dict],
    lexicon: sm.SchemaLexicon | None = None,
):
    mapping = sm.map_table(list(columns), rows, lexicon)
    normalizer.ingest_table(_table(file, list(columns), rows), mapping, {"file": file})
    return mapping


def _write(root: Path, files: dict[str, str]) -> Path:
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


async def _import(root: Path, name: str = "Graph quality corpus"):
    async with async_session() as session:
        return await run_import(
            session,
            [root],
            ImportOptions(
                name=name,
                copy_inputs=False,
                activate=False,
                build_graph=True,
                jurisdiction_id=JURISDICTION,
            ),
        )


async def _entities(dataset_id: str, entity_type: str | None = None):
    async with async_session() as session:
        stmt = select(DatasetEntity).where(DatasetEntity.dataset_id == dataset_id)
        if entity_type:
            stmt = stmt.where(DatasetEntity.entity_type == entity_type)
        return list((await session.execute(stmt)).scalars())


async def _relationships(dataset_id: str, rel_type: str | None = None):
    async with async_session() as session:
        stmt = select(DatasetRelationship).where(DatasetRelationship.dataset_id == dataset_id)
        if rel_type:
            stmt = stmt.where(DatasetRelationship.rel_type == rel_type)
        return list((await session.execute(stmt)).scalars())


async def _cases(dataset_id: str):
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(Case).where(Case.dataset_id == dataset_id)
                )
            ).scalars()
        )


# --------------------------------------------------------------------------- #
# Case extraction is content-driven
# --------------------------------------------------------------------------- #


async def test_a_multi_case_register_produces_one_case_per_row(tmp_path: Path):
    """Ten stated cases are ten cases; they are not collapsed into one."""
    rows = "".join(
        f"CASE-{index:03d},FIR/2024/{index:05d},ROBBERY,OPEN\n" for index in range(1, 11)
    )
    _write(
        tmp_path,
        {
            "cases.csv": "case_id,case_number,case_type,status\n" + rows,
            "people.csv": "person_id,full_name\nP001,Meera Kurian\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    cases = await _cases(report.dataset_id)
    stated = {case.dataset_case_key for case in cases if case.dataset_case_key != "ALL"}
    assert len(stated) == 10, sorted(stated)
    # The container case is additional bookkeeping, never a replacement for the
    # cases the source actually described.
    assert len(cases) == 11


async def test_cases_stated_only_in_document_content_are_still_cases(tmp_path: Path):
    """A corpus with no case register must not collapse into one case.

    The investigations are named inside the documents, which is content; a
    folder layout is not consulted at all.
    """
    files: dict[str, str] = {}
    for index in range(1, 6):
        files[f"bundle/summary_{index}.txt"] = (
            f"Investigation summary.\nCase Number: CASE-{index:03d}\n"
            f"Subject P{index:03d} was interviewed.\n"
        )
        files[f"bundle/fir_{index}.txt"] = (
            f"First Information Report\nFIR No: FIR/2024/{index:05d}\n"
        )
        files[f"bundle/people_{index}.csv"] = (
            "person_id,full_name\n" f"P{index:03d},Subject {index:03d} Name\n"
        )
    _write(tmp_path, files)

    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    cases = await _cases(report.dataset_id)
    stated = {case.dataset_case_key for case in cases if case.dataset_case_key != "ALL"}
    assert stated == {f"CASE-{index:03d}" for index in range(1, 6)}, sorted(stated)


async def test_an_fir_number_is_the_same_case_not_a_second_one(tmp_path: Path):
    """A document that states both a case and its FIR describes one case."""
    _write(
        tmp_path,
        {
            "dossier.txt": (
                "Case Number: CASE-001\nFIR No: FIR/2024/00001\n"
                "The accused was identified from CCTV.\n"
            ),
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    cases = await _cases(report.dataset_id)
    stated = [case for case in cases if case.dataset_case_key != "ALL"]
    assert [case.dataset_case_key for case in stated] == ["CASE-001"]


async def test_a_dataset_that_states_no_case_does_not_invent_one(tmp_path: Path):
    """No case information anywhere means the container case and nothing else."""
    _write(
        tmp_path,
        {
            "people.csv": "person_id,full_name\nP001,Meera Kurian\n",
            "notes.txt": "A register of known associates. Nobody was charged.\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    cases = await _cases(report.dataset_id)
    assert len(cases) == 1
    assert cases[0].dataset_case_key == "ALL"


async def test_dataset_level_documents_never_define_cases(tmp_path: Path):
    """A README quoting example identifiers is documentation, not a case list."""
    _write(
        tmp_path,
        {
            "README.md": (
                "# Corpus\n\nExample identifiers such as Case Number: CASE-901 and\n"
                "Case Number: CASE-902 appear here for illustration only.\n"
            ),
            "data_dictionary.csv": "field,description\ncase_id,Case Number: CASE-903\n",
            "people.csv": "person_id,full_name\nP001,Meera Kurian\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    cases = await _cases(report.dataset_id)
    stated = {case.dataset_case_key for case in cases if case.dataset_case_key != "ALL"}
    assert stated == set(), sorted(stated)


# --------------------------------------------------------------------------- #
# Identity across files
# --------------------------------------------------------------------------- #


async def test_a_person_is_one_person_across_the_files_that_mention_them(
    tmp_path: Path,
):
    """The register, the phone list and the ledger all mean the same P001."""
    _write(
        tmp_path,
        {
            "people.csv": "person_id,full_name,city\nP001,Meera Kurian,Pune\n",
            "phones.csv": "person_id,phone_number\nP001,+919801000001\n",
            "accounts.csv": "person_id,account_number,bank_name\nP001,100000000001,SBI\n",
            "vehicles.csv": "person_id,registration_number\nP001,MH11AB1001\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    people = await _entities(report.dataset_id, sm.PERSON)
    assert len(people) == 1, [person.canonical_id for person in people]
    assert people[0].canonical_id == "PERSON:P001"

    for rel_type in ("USES_PHONE", "OWNS_ACCOUNT", "OWNS_VEHICLE"):
        relationships = await _relationships(report.dataset_id, rel_type)
        assert len(relationships) == 1, rel_type
        assert relationships[0].source_canonical_id == "PERSON:P001"


async def test_ownership_stated_in_a_separate_file_still_connects(tmp_path: Path):
    """``owner_person_id`` in the phone register is the owner, not a vehicle."""
    _write(
        tmp_path,
        {
            "persons.csv": "person_id,full_name\nP001,Meera Kurian\nP002,Ajay Kumar\n",
            "phone_register.csv": (
                "phone_id,phone_number,owner_person_id,status\n"
                "PH0001,+919801000001,P001,ACTIVE\n"
                "PH0002,+919802000002,P002,ACTIVE\n"
            ),
            "account_register.csv": (
                "account_id,account_number,holder_person_id,bank_code\n"
                "AC0001,100000000001,P001,SBI\n"
            ),
            "vehicle_register.csv": (
                "vehicle_id,registration_number,owner_person_id\n"
                "V0001,MH11AB1001,P002\n"
            ),
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    for rel_type, expected in (("USES_PHONE", 2), ("OWNS_ACCOUNT", 1), ("OWNS_VEHICLE", 1)):
        relationships = await _relationships(report.dataset_id, rel_type)
        assert len(relationships) == expected, rel_type
        assert all(
            relationship.source_canonical_id.startswith("PERSON:")
            for relationship in relationships
        ), rel_type


async def test_a_shared_phone_does_not_merge_two_people(tmp_path: Path):
    """Two people may legitimately hold one number; both edges are kept."""
    _write(
        tmp_path,
        {
            "persons.csv": "person_id,full_name\nP001,Meera Kurian\nP002,Ajay Kumar\n",
            "phone_register.csv": (
                "phone_id,phone_number,owner_person_id\n"
                "PH0001,+919801000001,P001\n"
                "PH0002,+919801000001,P002\n"
            ),
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    people = await _entities(report.dataset_id, sm.PERSON)
    assert len(people) == 2
    phones = await _entities(report.dataset_id, sm.PHONE)
    assert len(phones) == 1, "one number is one phone"
    assert len(await _relationships(report.dataset_id, "USES_PHONE")) == 2


# --------------------------------------------------------------------------- #
# CP_* style codes
# --------------------------------------------------------------------------- #


def test_a_code_defined_in_another_file_resolves_to_the_record_it_names():
    """``counterparty_id`` on a register is that record's name, not a transaction.

    The mapper reads ``counterparty_id`` as a transaction's counterparty, so the
    identifier the dataset uses for the person was being dropped — and every
    later reference to it went with it.
    """
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "counterparties.csv",
        ["counterparty_id", "person_id", "name"],
        [{"counterparty_id": "CP_01", "person_id": "P042", "name": "John Doe"}],
    )
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [{"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P042"}],
    )
    _ingest(
        normalizer,
        "ledger.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [
            {
                "transaction_id": "T1",
                "from_account": "AC0001",
                "counterparty": "CP_01",
                "amount": "5000",
            }
        ],
    )
    normalizer.reconcile_identifiers()

    assert normalizer.result.entities["PERSON:P042"].attributes["source_identifiers"] == [
        "CP_01"
    ]
    transfers = [
        relationship
        for relationship in normalizer.result.relationships.values()
        if relationship.rel_type == "TRANSFER_TO"
    ]
    assert len(transfers) == 1
    assert transfers[0].target_canonical_id == "PERSON:P042"
    assert not [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ORGANIZATION
    ]


def test_a_code_defined_in_a_later_file_still_resolves():
    """File order must not decide whether a stated relationship survives."""
    normalizer = Normalizer()
    # The ledger is read first: nothing defines CP_07 yet.
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [{"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P007"}],
    )
    _ingest(
        normalizer,
        "ledger.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [
            {
                "transaction_id": "T1",
                "from_account": "AC0001",
                "counterparty": "CP_07",
                "amount": "5000",
            }
        ],
    )
    _ingest(
        normalizer,
        "counterparties.csv",
        ["counterparty_id", "person_id", "name"],
        [{"counterparty_id": "CP_07", "person_id": "P007", "name": "Nalini Rao"}],
    )
    normalizer.reconcile_identifiers()

    transfers = [
        relationship
        for relationship in normalizer.result.relationships.values()
        if relationship.rel_type == "TRANSFER_TO"
    ]
    assert len(transfers) == 1
    assert transfers[0].target_canonical_id == "PERSON:P007"
    assert normalizer.result.warnings == []


def test_a_code_the_dataset_never_defines_is_reported_not_invented():
    """An undefined code stays untyped, and the operator is told."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [{"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P001"}],
    )
    _ingest(
        normalizer,
        "ledger.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [
            {
                "transaction_id": "T1",
                "from_account": "AC0001",
                "counterparty": "CP_99",
                "amount": "5000",
            }
        ],
    )
    normalizer.reconcile_identifiers()

    assert not [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ORGANIZATION
    ]
    assert not [
        entity
        for entity in normalizer.result.entities.values()
        if "CP_99" in entity.canonical_id
    ], "an undefined code must not become a node of any invented type"
    assert sum("CP_99" in warning for warning in normalizer.result.warnings) == 1


def test_a_foreign_key_column_is_never_claimed_as_the_row_own_identifier():
    """``owner_person_id`` names the owner; it does not rename the phone."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "phone_register.csv",
        ["phone_id", "phone_number", "owner_person_id"],
        [{"phone_id": "PH0001", "phone_number": "+919801000001", "owner_person_id": "P001"}],
    )
    phone = normalizer.result.entities["PHONE:PH0001"]
    assert "source_identifiers" not in phone.attributes
    # The owner reference is still the owner; it was not claimed as a second
    # name for the handset.
    assert normalizer._lookup(sm.PHONE, "P001") is None


def test_a_numeric_value_is_never_claimed_as_a_person_identifier():
    """An account number in a person row is a value, not another name for them."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "people.csv",
        ["person_id", "full_name", "linked_account_no"],
        [{"person_id": "P001", "full_name": "Meera Kurian", "linked_account_no": "100000000001"}],
    )
    assert normalizer._lookup(sm.PERSON, "100000000001") is None


# --------------------------------------------------------------------------- #
# Hard identifiers and graph identity
# --------------------------------------------------------------------------- #


async def test_a_duplicate_hard_identifier_is_one_graph_node(tmp_path: Path):
    """The same plate in two files is one vehicle, not two."""
    _write(
        tmp_path,
        {
            "vehicles.csv": (
                "vehicle_id,registration_number,owner_person_id\n"
                "V0001,MH11AB1001,P001\n"
            ),
            "sightings.csv": (
                "sighting_id,registration,location,timestamp\n"
                "S0001,MH 11 AB 1001,Central Square,2024-03-11 06:00:00\n"
            ),
            "people.csv": "person_id,full_name\nP001,Meera Kurian\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    vehicles = await _entities(report.dataset_id, sm.VEHICLE)
    assert len(vehicles) == 1, [vehicle.canonical_id for vehicle in vehicles]

    labels = (report.graph.get("graph") or {}).get("labels") or {}
    assert labels.get("Vehicle", 0) == 1, labels


# --------------------------------------------------------------------------- #
# Case membership
# --------------------------------------------------------------------------- #


async def test_ownership_puts_a_record_in_its_case_but_a_transfer_does_not(
    tmp_path: Path,
):
    """A transfer names a case's file; it does not enrol the counterparty."""
    _write(
        tmp_path,
        {
            "cases.csv": "case_id,case_number,status\nC101,FIR/2024/00101,OPEN\n",
            "case_members.csv": "case_id,person_id,role\nC101,P001,ACCUSED\n",
            "people.csv": "person_id,full_name\nP001,Meera Kurian\nP002,Ajay Kumar\n",
            "accounts.csv": (
                "account_id,account_number,holder_person_id\n"
                "AC0001,100000000001,P001\n"
                "AC0002,100000000002,P002\n"
            ),
            "transactions.csv": (
                "transaction_id,txn_date,from_account_id,to_account_id,amount,case_id\n"
                "TX0001,2024-03-07,AC0001,AC0002,580000.00,C101\n"
            ),
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    transfers = await _relationships(report.dataset_id, "TRANSFER_TO")
    assert len(transfers) == 1
    # The edge is scoped to the case whose file stated it...
    assert any(
        case_id.endswith("C101") for case_id in transfers[0].case_ids
    ), transfers[0].case_ids
    # ...and the membership edge is the case-membership row, which is the only
    # row that says anybody belongs to the case.
    involved = await _relationships(report.dataset_id, "INVOLVED_IN")
    members = {relationship.source_canonical_id for relationship in involved}
    assert "PERSON:P001" in members


# --------------------------------------------------------------------------- #
# Provenance survives normalization
# --------------------------------------------------------------------------- #


async def test_entity_and_relationship_provenance_survive_the_pipeline(tmp_path: Path):
    _write(
        tmp_path,
        {
            "register/people.csv": "person_id,full_name\nP001,Meera Kurian\n",
            "register/phones.csv": "person_id,phone_number\nP001,+919801000001\n",
        },
    )
    report = await _import(tmp_path)
    assert report.status == "READY", report.error

    person = (await _entities(report.dataset_id, sm.PERSON))[0]
    assert person.provenance["file"] == "register/people.csv"
    assert person.provenance["dataset_id"] == report.dataset_id
    assert person.provenance["row"] == 2

    phone_edge = (await _relationships(report.dataset_id, "USES_PHONE"))[0]
    assert phone_edge.provenance["file"] == "register/phones.csv"
    assert phone_edge.provenance["dataset_id"] == report.dataset_id

    async with async_session() as session:
        manifest = list(
            (
                await session.execute(
                    select(DatasetFile).where(DatasetFile.dataset_id == report.dataset_id)
                )
            ).scalars()
        )
    assert sorted(row.relative_path for row in manifest) == [
        "register/people.csv",
        "register/phones.csv",
    ]
