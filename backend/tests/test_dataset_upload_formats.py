"""Universal ingestion: whatever the user uploads, it has to work.

The brief lists six shapes an upload can take, and they are all tested here
against the real HTTP endpoint:

  A. a single CSV
  B. a single XLSX
  C. a ZIP of mixed files
  D. a folder of mixed files (browser folder upload, relative paths preserved)
  E. PDF + CSV + TXT together
  F. the same data under completely different column names

No required folder names, no required file names, no required extension mix.
The last case is the important one: if column mapping only works for the
column headings of one corpus, then nothing has been solved -- the corpus has
just been hardcoded somewhere less obvious.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from app.datasets import registry
from app.db.session import async_session

pytestmark = pytest.mark.anyio if False else []


# --------------------------------------------------------------------------- #
# Fixture data
# --------------------------------------------------------------------------- #

PEOPLE_CSV = (
    "person_id,full_name,phone_number,address,city\n"
    "P001,Ramesh Kumar,9812345670,12 MG Road,Vijayawada\n"
    "P002,Sunita Devi,9812345671,44 Beach Road,Visakhapatnam\n"
    "P003,Arjun Reddy,9812345672,7 Temple Street,Guntur\n"
)

# Format F: the same three people, described by a system that names nothing
# the same way. Nothing in the mapper may depend on the headings above.
PEOPLE_CSV_ALIASED = (
    "sr_no;NAME OF PERSON;Mobile No.;Residential Address;District\n"
    "1;Ramesh Kumar;+91 98123 45670;12 MG Road;Vijayawada\n"
    "2;Sunita Devi;09812345671;44 Beach Road;Visakhapatnam\n"
    "3;Arjun Reddy;9812345672;7 Temple Street;Guntur\n"
)

CALLS_CSV = (
    "caller,callee,duration_sec,call_time\n"
    "9812345670,9812345671,120,2024-03-01 10:15:00\n"
    "9812345671,9812345672,45,2024-03-01 11:00:00\n"
)

VEHICLES_CSV = (
    "registration_number,owner_name,make,model\n"
    "AP16 CD 4567,Ramesh Kumar,Maruti,Swift\n"
    "AP31 EF 1122,Arjun Reddy,Hyundai,Creta\n"
)

NOTES_TXT = (
    "Surveillance note, 01 March 2024.\n"
    "Subject Ramesh Kumar observed at 12 MG Road with vehicle AP16 CD 4567.\n"
    "Contact number 9812345670 in use throughout.\n"
)


def _minimal_pdf(body: str) -> bytes:
    """A genuinely valid one-page PDF, built by hand.

    Small enough to keep in the test, real enough that the PDF reader is
    actually exercised rather than a stub of it.
    """
    text = body.replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n"
    ).encode()
    out += b"%%EOF\n"
    return bytes(out)


def _xlsx(rows: list[list[str]], sheet_name: str = "People") -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _await_job(client, headers, job_id: str, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        body = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if body["terminal"]:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"import job {job_id} did not finish within {timeout}s")


async def _upload(client, headers, files, data=None) -> dict:
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        files=files,
        data=data or {},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    final = await _await_job(client, headers, body["job_id"])
    assert final["status"] == "SUCCEEDED", final.get("error") or final
    return final


async def _entities_of(dataset_id: str) -> dict[str, int]:
    async with async_session() as session:
        stats = await registry.dataset_stats(session, dataset_id)
    return stats


# --------------------------------------------------------------------------- #
# A. one CSV
# --------------------------------------------------------------------------- #


async def test_format_a_single_csv(client, admin_headers, container):
    final = await _upload(
        client,
        admin_headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
        data={"name": "Format A"},
    )
    report = final["result"]
    dataset_id = report["dataset_id"]

    stats = await _entities_of(dataset_id)
    assert stats["entities"] >= 3, report
    assert report["canonical"]["entities"] >= 3

    listing = client.get("/api/v1/datasets", headers=admin_headers).json()["items"]
    row = next(item for item in listing if item["id"] == dataset_id)
    assert row["status"] == "READY"
    assert row["is_active"] is True, "an imported dataset becomes the active one"


async def test_a_single_csv_produces_people_not_just_rows(client, admin_headers, container):
    """Rows are not the deliverable; canonical people, phones and addresses are."""
    final = await _upload(
        client,
        admin_headers,
        files=[("files", ("anything.csv", PEOPLE_CSV, "text/csv"))],
    )
    dataset_id = final["result"]["dataset_id"]

    async with async_session() as session:
        from sqlalchemy import func, select

        from app.db.models import DatasetEntity

        counts = dict(
            (
                await session.execute(
                    select(DatasetEntity.entity_type, func.count())
                    .where(DatasetEntity.dataset_id == dataset_id)
                    .group_by(DatasetEntity.entity_type)
                )
            ).all()
        )
    assert counts.get("PERSON", 0) == 3, counts
    assert counts.get("PHONE", 0) == 3, counts


# --------------------------------------------------------------------------- #
# B. one XLSX
# --------------------------------------------------------------------------- #


async def test_format_b_single_xlsx(client, admin_headers, container):
    payload = _xlsx(
        [
            ["person_id", "full_name", "phone_number", "address"],
            ["P001", "Ramesh Kumar", "9812345670", "12 MG Road"],
            ["P002", "Sunita Devi", "9812345671", "44 Beach Road"],
        ]
    )
    final = await _upload(
        client,
        admin_headers,
        files=[
            (
                "files",
                (
                    "roster.xlsx",
                    payload,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        ],
        data={"name": "Format B"},
    )
    stats = await _entities_of(final["result"]["dataset_id"])
    assert stats["entities"] >= 2, final["result"]


# --------------------------------------------------------------------------- #
# C. a ZIP of mixed files
# --------------------------------------------------------------------------- #


async def test_format_c_mixed_zip(client, admin_headers, container):
    archive = _zip(
        {
            "whatever/people.csv": PEOPLE_CSV.encode(),
            "whatever/deeper/calls.csv": CALLS_CSV.encode(),
            "vehicles.csv": VEHICLES_CSV.encode(),
            "notes/surveillance.txt": NOTES_TXT.encode(),
            "notes/report.pdf": _minimal_pdf("Case report for Ramesh Kumar"),
        }
    )
    final = await _upload(
        client,
        admin_headers,
        files=[("files", ("evidence.zip", archive, "application/zip"))],
        data={"name": "Format C"},
    )
    report = final["result"]
    # The archive is expanded and every member is discovered, at any depth and
    # under any folder name.
    assert report["files"]["files_usable"] >= 5, report["files"]
    stats = await _entities_of(report["dataset_id"])
    assert stats["entities"] >= 5
    assert stats["relationships"] >= 1


# --------------------------------------------------------------------------- #
# D. a folder of mixed files
# --------------------------------------------------------------------------- #


async def test_format_d_folder_upload_preserves_layout(client, admin_headers, container):
    """A browser folder upload sends relative paths; they become provenance."""
    files = [
        ("files", ("people.csv", PEOPLE_CSV, "text/csv")),
        ("files", ("calls.csv", CALLS_CSV, "text/csv")),
        ("files", ("vehicles.csv", VEHICLES_CSV, "text/csv")),
    ]
    data = {
        "name": "Format D",
        "paths": [
            "MyCaseFolder/tables/people.csv",
            "MyCaseFolder/tables/cdr/calls.csv",
            "MyCaseFolder/rto/vehicles.csv",
        ],
    }
    final = await _upload(client, admin_headers, files=files, data=data)
    dataset_id = final["result"]["dataset_id"]

    async with async_session() as session:
        from sqlalchemy import select

        from app.db.models import DatasetFile

        rows = (
            (
                await session.execute(
                    select(DatasetFile.relative_path).where(
                        DatasetFile.dataset_id == dataset_id
                    )
                )
            )
            .scalars()
            .all()
        )
    joined = " ".join(rows)
    assert "cdr/calls.csv" in joined, rows
    assert "rto/vehicles.csv" in joined, rows


async def test_a_folder_upload_needs_no_particular_folder_names(
    client, admin_headers, container
):
    """No ``operational/`` and no ``documents/`` -- and it still imports."""
    files = [
        ("files", ("a.csv", PEOPLE_CSV, "text/csv")),
        ("files", ("b.csv", CALLS_CSV, "text/csv")),
    ]
    data = {"paths": ["random name/x/a.csv", "random name/y/b.csv"]}
    final = await _upload(client, admin_headers, files=files, data=data)
    stats = await _entities_of(final["result"]["dataset_id"])
    assert stats["entities"] >= 3
    assert stats["relationships"] >= 1


# --------------------------------------------------------------------------- #
# E. PDF + CSV + TXT
# --------------------------------------------------------------------------- #


async def test_format_e_pdf_csv_txt_together(client, admin_headers, container):
    final = await _upload(
        client,
        admin_headers,
        files=[
            ("files", ("people.csv", PEOPLE_CSV, "text/csv")),
            ("files", ("notes.txt", NOTES_TXT, "text/plain")),
            (
                "files",
                ("statement.pdf", _minimal_pdf("Statement of Sunita Devi"), "application/pdf"),
            ),
        ],
        data={"name": "Format E"},
    )
    report = final["result"]
    assert report["files"]["files_usable"] >= 2, report["files"]
    # Documents are ingested as evidence, not silently dropped for not being
    # a table.
    assert report["documents_created"] >= 1, report


# --------------------------------------------------------------------------- #
# F. different column names entirely
# --------------------------------------------------------------------------- #


async def test_format_f_unfamiliar_column_names_still_map(
    client, admin_headers, container
):
    """Semicolon-delimited, upper-case, spaced, punctuated headings.

    ``NAME OF PERSON`` / ``Mobile No.`` / ``Residential Address`` appear in no
    fixture anywhere in this repository. If they map, the mapping layer is
    doing real work.
    """
    final = await _upload(
        client,
        admin_headers,
        files=[("files", ("legacy_export.csv", PEOPLE_CSV_ALIASED, "text/csv"))],
        data={"name": "Format F"},
    )
    dataset_id = final["result"]["dataset_id"]

    async with async_session() as session:
        from sqlalchemy import select

        from app.db.models import DatasetEntity

        entities = (
            (
                await session.execute(
                    select(DatasetEntity).where(DatasetEntity.dataset_id == dataset_id)
                )
            )
            .scalars()
            .all()
        )

    people = {e.name for e in entities if e.entity_type == "PERSON"}
    phones = {e.normalized_value for e in entities if e.entity_type == "PHONE"}
    assert "Ramesh Kumar" in people, sorted(people)
    # Three spellings of one number -- "+91 98123 45670", "09812345671",
    # "9812345672" -- must normalise to a comparable form.
    assert "9812345670" in phones, sorted(phones)
    assert "9812345671" in phones, sorted(phones)


async def test_mapping_confidence_is_reported_for_review(client, admin_headers, container):
    """Low confidence must be visible, not silently guessed at."""
    unknowable = "col_a,col_b,col_c\n1,2,3\n4,5,6\n"
    final = await _upload(
        client,
        admin_headers,
        files=[
            ("files", ("people.csv", PEOPLE_CSV, "text/csv")),
            ("files", ("mystery.csv", unknowable, "text/csv")),
        ],
    )
    report = final["result"]
    flagged = " ".join(str(item) for item in report.get("needs_review", []))
    assert "mystery.csv" in flagged, report.get("needs_review")
    # And the confident table is not dragged down with it.
    assert report["canonical"]["entities"] >= 3


# --------------------------------------------------------------------------- #
# Replacement: the old dataset must stop being the answer
# --------------------------------------------------------------------------- #


async def test_importing_a_second_dataset_replaces_the_first_everywhere(
    client, admin_headers, container
):
    """The whole point of the exercise, tested end to end."""
    first = await _upload(
        client,
        admin_headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
        data={"name": "Old dataset"},
    )
    first_id = first["result"]["dataset_id"]

    replacement = (
        "person_id,full_name,phone_number\n"
        "Z900,Meera Nair,9800000001\n"
        "Z901,Vikram Rao,9800000002\n"
    )
    second = await _upload(
        client,
        admin_headers,
        files=[("files", ("people.csv", replacement, "text/csv"))],
        data={"name": "New dataset"},
    )
    second_id = second["result"]["dataset_id"]
    assert second_id != first_id

    active = client.get("/api/v1/datasets/active", headers=admin_headers).json()
    assert active["active"]["id"] == second_id
    assert active["active"]["name"] == "New dataset"

    # Exactly one active dataset, and it is the new one.
    listing = client.get("/api/v1/datasets", headers=admin_headers).json()["items"]
    assert [item["id"] for item in listing if item["is_active"]] == [second_id]

    # The graph holds the new people and none of the old ones.
    node_names = {
        item["name"] for item in container.graph_store.list_nodes(limit=1000)["items"]
    }
    assert "Meera Nair" in node_names, sorted(n for n in node_names if n)
    assert "Ramesh Kumar" not in node_names, (
        "the replaced dataset's people are still in the graph"
    )


async def test_every_graph_node_carries_its_dataset(client, admin_headers, container):
    """No orphans: a node with no dataset is a node nothing can clean up."""
    final = await _upload(
        client,
        admin_headers,
        files=[
            ("files", ("people.csv", PEOPLE_CSV, "text/csv")),
            ("files", ("calls.csv", CALLS_CSV, "text/csv")),
        ],
    )
    dataset_id = final["result"]["dataset_id"]

    listing = container.graph_store.list_nodes(limit=1000)["items"]
    assert listing
    for item in listing:
        node = container.graph_store.get_node(item["id"])
        assert node is not None, item
        properties = node.properties or {}
        assert properties.get("dataset_id") == dataset_id, (item, properties)
        assert properties.get("canonical_id"), (item, properties)
        assert properties.get("entity_type") or node.label, (item, properties)


# --------------------------------------------------------------------------- #
# Refusals that must stay refusals
# --------------------------------------------------------------------------- #


async def test_import_requires_admin(client, investigator_headers):
    response = client.post(
        "/api/v1/datasets/import",
        headers=investigator_headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
    )
    assert response.status_code == 403


async def test_upload_paths_cannot_escape_the_staging_directory(
    client, admin_headers, container, tmp_path
):
    """``../../etc/passwd`` is a path traversal attempt, not a filename."""
    final = await _upload(
        client,
        admin_headers,
        files=[("files", ("people.csv", PEOPLE_CSV, "text/csv"))],
        data={"paths": ["../../../../tmp/escaped.csv"]},
    )
    dataset_id = final["result"]["dataset_id"]

    async with async_session() as session:
        from sqlalchemy import select

        from app.db.models import DatasetFile

        rows = (
            (
                await session.execute(
                    select(DatasetFile.relative_path).where(
                        DatasetFile.dataset_id == dataset_id
                    )
                )
            )
            .scalars()
            .all()
        )
    for path in rows:
        assert ".." not in path, f"an upload kept a traversal segment: {path}"
    assert not Path("/tmp/escaped.csv").exists()
    # The file still arrived — it was renamed, not rejected.
    assert any(path.endswith("escaped.csv") for path in rows), rows
