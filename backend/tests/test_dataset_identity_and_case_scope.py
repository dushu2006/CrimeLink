"""Who a dataset's records are, and which case they belong to.

Two promises the importer has to keep, because a graph that breaks them is
worse than no graph at all:

* **Identity is the dataset's, not the importer's.**  An account written
  ``AC0001`` in the register and ``100000000001`` in a statement is one
  account; a phone written ``+91 98010 00001`` and ``9801000001`` is one
  phone.  When the importer misses that, the register's row and the ledger's
  row become two nodes, the owner's ``OWNS_ACCOUNT`` edge lands on one of them,
  and the other "floats" -- which is exactly what a badly imported dataset
  looks like.

* **A reference is not a type.**  ``CP_01`` in a counterparty column may be a
  person, a case property, or nothing this dataset defines.  Turning it into an
  organisation invents a company nobody described; resolving it when the
  dataset defines it is the whole job.

The tests below are deliberately dataset-agnostic: every corpus is written
into ``tmp_path`` and imported through the real pipeline, so nothing here
depends on the bundled CrimeLink corpus or on any filename.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.datasets import graph_build, readers
from app.datasets import schema_map as sm
from app.datasets.normalize import Normalizer
from app.datasets.pipeline import ImportOptions, run_import
from app.db.models import Case, DatasetEntity, DatasetRelationship
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


async def _import(root: Path, name: str = "Identity corpus"):
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


async def _relationship_count(dataset_id: str, rel_type: str) -> int:
    async with async_session() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(DatasetRelationship)
                    .where(
                        DatasetRelationship.dataset_id == dataset_id,
                        DatasetRelationship.rel_type == rel_type,
                    )
                )
            ).scalar()
            or 0
        )


# --------------------------------------------------------------------------- #
# Column mapping: the row's own key is not the foreign key
# --------------------------------------------------------------------------- #


def test_a_relation_row_id_does_not_claim_the_case_reference():
    """``case_member_id,case_id,person_id`` is a membership edge, not a case table.

    The row id used to win the token match (``case`` + ``id``) and the real
    ``case_id`` column went unmapped, so every membership row minted a phantom
    case named after the membership row.
    """
    rows = [
        {"case_member_id": "CM101001", "case_id": "C101", "person_id": "P001"},
        {"case_member_id": "CM101002", "case_id": "C101", "person_id": "P002"},
    ]
    mapping = sm.map_table(list(rows[0]), rows)
    assert mapping.mapped["CASE.id"] == "case_id"
    assert "case_member_id" not in mapping.mapped.values()


def test_person_org_row_ids_do_not_claim_the_person_reference():
    rows = [
        {"person_org_id": "PO101001", "person_id": "P001", "organization_id": "ORG001"},
    ]
    mapping = sm.map_table(list(rows[0]), rows)
    assert mapping.mapped["PERSON.id"] == "person_id"
    assert mapping.mapped["ORGANIZATION.id"] == "organization_id"


def test_an_owner_column_is_a_person_reference():
    """``owner_person_id`` names a person; the role word is not the entity."""
    phones = [
        {"phone_id": "PH0001", "phone_number": "+919801000001", "owner_person_id": "P001"},
        {"phone_id": "PH0002", "phone_number": "+919801000002", "owner_person_id": "P002"},
    ]
    mapping = sm.map_table(list(phones[0]), phones)
    assert mapping.mapped["PERSON.id"] == "owner_person_id"


def test_a_people_register_is_a_person_table_even_with_an_address():
    """A person's address does not make the register about addresses."""
    people = [
        {
            "person_id": "P001",
            "full_name": "Meera Kurian",
            "address": "12 Rose Lane",
            "city": "Kochi",
        },
        {
            "person_id": "P002",
            "full_name": "Anil Menon",
            "address": "8 Marine Drive",
            "city": "Kochi",
        },
    ]
    mapping = sm.map_table(list(people[0]), people)
    assert mapping.semantic_type == "PERSON_TABLE", mapping.semantic_type
    assert mapping.primary_entity == sm.PERSON


def test_a_file_index_is_not_a_case_register():
    """``document_id,case_id,file_path`` is an index of files, not of cases."""
    index = [
        {"document_id": "D001", "case_id": "C101", "file_path": "cases/C101/notes.txt"},
        {"document_id": "D002", "case_id": "C102", "file_path": "cases/C102/notes.txt"},
    ]
    mapping = sm.map_table(list(index[0]), index)
    assert mapping.semantic_type == "DOCUMENT_INDEX", mapping.semantic_type


# --------------------------------------------------------------------------- #
# Identity: one account, however the dataset spells it
# --------------------------------------------------------------------------- #


def test_account_number_and_account_id_resolve_to_one_account():
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [
            {"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P001"},
            {"account_id": "AC0002", "account_number": "100000000002", "holder_person_id": "P002"},
        ],
    )
    _ingest(
        normalizer,
        "statement.csv",
        ["transaction_id", "from_account", "to_account", "amount"],
        [
            {"transaction_id": "T1", "from_account": "100000000001", "to_account": "100000000002", "amount": "500"},
            {"transaction_id": "T2", "from_account": "AC0001", "to_account": "AC0002", "amount": "750"},
        ],
    )
    normalizer.reconcile_identifiers()

    accounts = [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ACCOUNT
    ]
    owned = [
        rel
        for rel in normalizer.result.relationships.values()
        if rel.rel_type == "OWNS_ACCOUNT"
    ]
    # AC0001/AC0002 and their numbers are two accounts, not four, and the
    # owner edges land on the accounts the register described.
    ids = {entity.canonical_id for entity in accounts}
    assert ids == {"ACCOUNT:AC0001", "ACCOUNT:AC0002"}, sorted(ids)
    assert len(owned) == 2
    assert {rel.target_canonical_id for rel in owned} == ids


def test_phone_number_and_phone_id_resolve_to_one_phone():
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "phones.csv",
        ["phone_id", "phone_number", "owner_person_id"],
        [{"phone_id": "PH0001", "phone_number": "+91 98010 00001", "owner_person_id": "P001"}],
    )
    _ingest(
        normalizer,
        "cdr.csv",
        ["cdr_id", "from_phone", "to_phone"],
        [
            {"cdr_id": "1", "from_phone": "9801000001", "to_phone": "9801000009"},
            {"cdr_id": "2", "from_phone": "+919801000001", "to_phone": "+919801000009"},
        ],
    )
    normalizer.reconcile_identifiers()

    phones = {
        entity.canonical_id
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.PHONE
    }
    assert len(phones) == 2, phones
    assert any(cid.split(":")[-1] == "PH0001" for cid in phones)


def test_an_undefined_code_is_not_made_into_an_organization():
    """``CP_01`` is a reference nobody defined; it is not a company."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [{"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P001"}],
    )
    _ingest(
        normalizer,
        "misc.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [{"transaction_id": "T1", "from_account": "AC0001", "counterparty": "CP_01", "amount": "500"}],
    )
    normalizer.reconcile_identifiers()

    organizations = [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ORGANIZATION
    ]
    assert organizations == [], [e.canonical_id for e in organizations]
    assert any("CP_01" in warning for warning in normalizer.result.warnings)


def test_a_defined_code_resolves_to_the_record_it_names():
    """The same ``CP_01`` *is* the person when a register defines that id."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "persons.csv",
        ["person_id", "full_name"],
        [{"person_id": "CP_01", "full_name": "Meera Kurian"}],
    )
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number", "holder_person_id"],
        [{"account_id": "AC0001", "account_number": "100000000001", "holder_person_id": "P001"}],
    )
    _ingest(
        normalizer,
        "misc.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [{"transaction_id": "T1", "from_account": "AC0001", "counterparty": "CP_01", "amount": "500"}],
    )
    normalizer.reconcile_identifiers()

    assert not [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ORGANIZATION
    ]
    assert any(
        entity.canonical_id == "PERSON:CP_01"
        for entity in normalizer.result.entities.values()
    )


def test_an_absence_marker_is_not_made_into_a_person():
    """``DATA_GAP`` says nobody was identified; it is not somebody's name."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "log.csv",
        ["log_id", "subject", "location"],
        [
            {"log_id": "L1", "subject": "DATA_GAP", "location": "Camera Point 3"},
            {"log_id": "L2", "subject": "Meera Kurian", "location": "Camera Point 1"},
        ],
    )
    normalizer.reconcile_identifiers()

    people = [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.PERSON
    ]
    assert [entity.name for entity in people] == ["Meera Kurian"]
    # The marker itself is reported, not silently swallowed.
    assert any("missing-value marker" in warning for warning in normalizer.result.warnings)
    assert any("DATA_GAP" in warning for warning in normalizer.result.warnings)


def test_a_name_in_capitals_is_still_a_name():
    """The marker vocabulary must not eat a register written in capitals."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "persons.csv",
        ["person_id", "full_name"],
        [{"person_id": "P001", "full_name": "RAM SINGH"}],
    )
    normalizer.reconcile_identifiers()
    people = [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.PERSON
    ]
    assert [entity.canonical_id for entity in people] == ["PERSON:P001"]


def test_an_absence_marker_in_a_reference_column_creates_no_stub():
    """A transaction row whose counterparty is ``N/A`` asserts no counterparty."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number"],
        [{"account_id": "AC0001", "account_number": "100000000001"}],
    )
    _ingest(
        normalizer,
        "misc.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [{"transaction_id": "T1", "from_account": "AC0001", "counterparty": "N/A", "amount": "500"}],
    )
    normalizer.reconcile_identifiers()

    names = {entity.name for entity in normalizer.result.entities.values()}
    assert "N/A" not in names
    assert any("missing-value marker" in warning for warning in normalizer.result.warnings)


def test_a_counterparty_named_as_a_business_is_still_an_organization():
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "accounts.csv",
        ["account_id", "account_number"],
        [{"account_id": "AC0001", "account_number": "100000000001"}],
    )
    _ingest(
        normalizer,
        "misc.csv",
        ["transaction_id", "from_account", "counterparty", "amount"],
        [{"transaction_id": "T1", "from_account": "AC0001", "counterparty": "Sunrise Traders", "amount": "500"}],
    )
    normalizer.reconcile_identifiers()
    names = {
        entity.name for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.ORGANIZATION
    }
    assert names == {"Sunrise Traders"}


def test_a_name_only_reference_folds_into_the_register_record():
    """A CCTV sheet naming "Meera Kurian" is the person the register defines."""
    normalizer = Normalizer()
    _ingest(
        normalizer,
        "log.csv",
        ["log_id", "subject", "location"],
        [{"log_id": "L1", "subject": "Meera Kurian", "location": "Gate 3"}],
    )
    _ingest(
        normalizer,
        "persons.csv",
        ["person_id", "full_name"],
        [{"person_id": "P001", "full_name": "Meera Kurian"}],
    )
    normalizer.reconcile_identifiers()

    people = [
        entity
        for entity in normalizer.result.entities.values()
        if entity.entity_type == sm.PERSON
    ]
    assert [entity.canonical_id for entity in people] == ["PERSON:P001"]


# --------------------------------------------------------------------------- #
# Case scope: the case file's own records belong to the case
# --------------------------------------------------------------------------- #


async def test_document_tables_link_their_records_to_the_case(tmp_path, container):
    """A bank statement inside C101's folder belongs to C101.

    The statement's rows carry no case column, and the file is a table rather
    than prose, so nothing else in the pipeline would connect the accounts it
    names to the case that holds the file.
    """
    root = _write(
        tmp_path / "corpus",
        {
            "cases.csv": (
                "case_id,case_number,registered_date,status\n"
                "C101,FIR/2024/00101,2024-03-15,OPEN\n"
            ),
            "persons.csv": (
                "person_id,full_name\n"
                "P001,Meera Kurian\n"
            ),
            "accounts.csv": (
                "account_id,account_number,holder_person_id\n"
                "AC0001,100000000001,P001\n"
            ),
            "documents/cases/C101/statement.csv": (
                "transaction_id,from_account,to_account,amount\n"
                "T1,100000000001,100000000002,500\n"
            ),
        },
    )
    report = await _import(root)
    assert report.error is None, report.error

    async with async_session() as session:
        case = (
            await session.execute(
                select(Case).where(
                    Case.dataset_id == report.dataset_id,
                    Case.dataset_case_key == "C101",
                )
            )
        ).scalars().one()

    raw = container.graph_store._graph
    case_nodes = [
        data
        for _pk, data in raw.nodes(data=True)
        if case.id in (data.get("case_ids") or [])
    ]
    labels = {data.get("_label") or data.get("label") for data in case_nodes}
    assert "BankAccount" in labels, labels
    assert "Person" in labels, labels


async def test_nothing_is_left_outside_every_case(tmp_path, container):
    """Records no case claims still belong to the dataset, not to nowhere."""
    root = _write(
        tmp_path / "corpus",
        {
            "cases.csv": (
                "case_id,case_number,registered_date,status\n"
                "C101,FIR/2024/00101,2024-03-15,OPEN\n"
                "C102,FIR/2024/00102,2024-04-01,OPEN\n"
            ),
            "persons.csv": (
                "person_id,full_name\n"
                "P001,Meera Kurian\n"
            ),
            "phones.csv": (
                "phone_id,phone_number,owner_person_id\n"
                "PH0001,+919801000001,P001\n"
            ),
        },
    )
    report = await _import(root)
    assert report.error is None, report.error
    raw = container.graph_store._graph
    unscoped = [pk for pk, data in raw.nodes(data=True) if not data.get("case_ids")]
    assert unscoped == [], unscoped


async def test_a_shared_observation_point_does_not_merge_two_cases(db, container):
    """Two cases filming the same place are still two cases.

    A camera point is shared by every file that names it.  If case membership
    spread across "was seen at", each case would absorb every vehicle ever
    recorded at the shared place -- the hub would drag the dataset into every
    case.  Belonging is what propagates; co-observation is not.
    """
    from app.datasets import registry
    from app.db.base import new_uuid
    from app.db.models import Case as CaseRow

    async with async_session() as session:
        dataset = await registry.create_dataset(
            session, name="Shared camera point", version="1", source_kind="folder"
        )
        c101 = CaseRow(
            id=new_uuid(), case_number="FIR/2024/00901", title="C101",
            jurisdiction_id=JURISDICTION, dataset_id=dataset.id, dataset_case_key="C101",
        )
        c102 = CaseRow(
            id=new_uuid(), case_number="FIR/2024/00902", title="C102",
            jurisdiction_id=JURISDICTION, dataset_id=dataset.id, dataset_case_key="C102",
        )
        container_case = CaseRow(
            id=new_uuid(), case_number="DATASET", title="unassigned",
            jurisdiction_id=JURISDICTION, dataset_id=dataset.id, dataset_case_key="ALL",
        )
        session.add_all([c101, c102, container_case])

        def mention(entity: str, case: CaseRow) -> DatasetRelationship:
            return DatasetRelationship(
                dataset_id=dataset.id,
                source_canonical_id=entity,
                target_canonical_id=f"CASE:{case.dataset_case_key}",
                rel_type="MENTIONED_IN",
                confidence=0.9,
                edge_key=f"m|{entity}|{case.dataset_case_key}",
                attributes={},
                provenance={"file": f"documents/{case.dataset_case_key}_file.csv"},
            )

        def seen(vehicle: str, case_file: str) -> DatasetRelationship:
            return DatasetRelationship(
                dataset_id=dataset.id,
                source_canonical_id=vehicle,
                target_canonical_id="LOCATION:Camera Point 1",
                rel_type="SEEN_AT",
                confidence=1.0,
                edge_key=f"s|{vehicle}",
                attributes={},
                provenance={"file": case_file},
            )

        session.add_all(
            [
                # Each case's own file names its own vehicle ...
                mention("VEHICLE:V001", c101),
                mention("VEHICLE:V002", c102),
                # ... and both files record the same shared camera point.
                mention("LOCATION:Camera Point 1", c101),
                mention("LOCATION:Camera Point 1", c102),
                seen("VEHICLE:V001", "documents/C101_file.csv"),
                seen("VEHICLE:V002", "documents/C102_file.csv"),
            ]
        )
        await session.commit()

        links = await graph_build._case_links(
            session,
            dataset.id,
            {"C101": c101, "C102": c102},
            default_case_id=container_case.id,
        )
        # nothing is left outside every case
        for canonical_id in (
            "VEHICLE:V001", "VEHICLE:V002", "LOCATION:Camera Point 1",
        ):
            assert links.get(canonical_id), canonical_id

        assert c101.id in links["VEHICLE:V001"]
        assert c102.id not in links["VEHICLE:V001"], (
            "C101's vehicle must not join C102 just because both files name the "
            "same camera point"
        )
        assert c102.id in links["VEHICLE:V002"]
        assert c101.id not in links["VEHICLE:V002"]
        # The camera point itself is evidence in both cases.
        assert {c101.id, c102.id} <= set(links["LOCATION:Camera Point 1"])

        await registry.purge_dataset_data(session, dataset.id)
        await session.commit()


async def test_documents_are_evidence_not_graph_actors(tmp_path, container):
    """A file is not a person: no DOCUMENT node, but its mentions are edges."""
    root = _write(
        tmp_path / "corpus",
        {
            "cases.csv": (
                "case_id,case_number,registered_date,status\n"
                "C101,FIR/2024/00101,2024-03-15,OPEN\n"
            ),
            "persons.csv": (
                "person_id,full_name\n"
                "P001,Meera Kurian\n"
            ),
            "documents/dossier.txt": (
                "Witness statement recorded for case C101. "
                "The complainant Meera Kurian (P001) attended the station.\n"
            ),
        },
    )
    report = await _import(root)
    assert report.error is None, report.error

    documents = await _entities(report.dataset_id, sm.DOCUMENT)
    assert documents == []
    assert await _relationship_count(report.dataset_id, "MENTIONED_IN") >= 1

    labels = {
        data.get("_label") or data.get("label")
        for _pk, data in container.graph_store._graph.nodes(data=True)
    }
    assert "Document" not in labels
