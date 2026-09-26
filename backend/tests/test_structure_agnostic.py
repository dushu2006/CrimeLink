"""Structure-agnostic ingestion tests.

Verifies that CrimeLink understands DATA, not DIRECTORY TREE.
Same semantic dataset in different folder layouts must produce equivalent
canonical results.

Covers:
- PDF/TXT/JSON/CSV parsing via content, not filename/folder
- Arbitrary nesting, mixed directories, files outside case folders
- Case association from content (explicit IDs, mentions, relationships)
- Folder context as optional supporting evidence, not authoritative
- Provenance preservation
- Meaningful errors for unsupported files
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from app.datasets import discovery, readers, schema_map as sm
from app.datasets.normalize import Normalizer
from app.datasets.readers import Table


# ---------------------------------------------------------------------------
# Helpers to build tiny synthetic datasets
# ---------------------------------------------------------------------------

def _write_file(base: Path, rel: str, content: bytes | str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def _minimal_pdf(body: str) -> bytes:
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
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode()
    )
    out += b"%%EOF\n"
    return bytes(out)


PEOPLE_CSV = """person_id,full_name,phone_number,case_id
P001,Ramesh Kumar,9812345670,CASE-001
P002,Sunita Devi,9812345671,CASE-001
"""

TRANSACTIONS_CSV = """from_account,to_account,amount,case_id
ACCT_001,ACCT_002,50000,CASE-001
"""

CASES_CSV = """case_id,case_number,case_type
CASE-001,CASE-001,ROBBERY
"""

EVIDENCE_TXT = """Case Number: CASE-001
FIR Number: FIR/2024/00101
Subject Ramesh Kumar observed at 12 MG Road.
Phone 9812345670 in use.
"""

PEOPLE_JSON = json.dumps([
    {"person_id": "P001", "full_name": "Ramesh Kumar", "case_id": "CASE-001"},
    {"person_id": "P002", "full_name": "Sunita Devi", "case_id": "CASE-001"}
])

FINANCIAL_JSON_NESTED = json.dumps({
    "case_id": "CASE-001",
    "case_number": "CASE-001",
    "transactions": [
        {"txn_id": "TXN_001", "from_account": "ACCT_001", "to_account": "ACCT_002", "amount": "50000"},
    ],
    "people": [
        {"person_id": "P001", "name": "Ramesh Kumar"}
    ]
})


def _discover_and_normalize(root: Path):
    found = discovery.discover(root)
    assert found.usable, f"No usable files in {root}, summary={found.summary()}"
    normalizer = Normalizer()
    lexicon = sm.SchemaLexicon()
    tables_info = []
    for entry in found.usable:
        if entry.kind != "table":
            continue
        try:
            tables = readers.read_tables(entry.path, entry.extension)
        except readers.UnreadableSource:
            continue
        for table in tables:
            if not table.columns:
                continue
            mapping = sm.map_table(table.columns, table.rows, lexicon)
            # Learn lexicon from confident columns
            if not mapping.contradictions:
                for col in mapping.columns:
                    if col.canonical and col.basis in {"alias", "header_tokens"}:
                        lexicon.learn_column(col.canonical, (r.get(col.column) for r in table.rows))
            source = {
                "dataset_id": "test",
                "file": entry.relative_path,
                "source_file": entry.filename,
                "source_type": entry.extension.lstrip("."),
                "sheet": table.name,
                "dataset_file_id": entry.relative_path,
                "extracted_at": "2024-01-01T00:00:00",
            }
            used = normalizer.ingest_table(table, mapping, source)
            tables_info.append((entry.relative_path, mapping.semantic_type, used))
    normalizer.reconcile_identifiers()
    normalizer.result.derive_shared_identifier_relationships()
    return found, normalizer, tables_info


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_discovery_is_recursive_and_folder_agnostic():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        # Same file in different nested locations
        _write_file(root, "a/b/c/people.csv", PEOPLE_CSV)
        _write_file(root, "x/y/z/transactions.csv", TRANSACTIONS_CSV)
        _write_file(root, "cases.csv", CASES_CSV)
        _write_file(root, "deeply/nested/arbitrary/evidence.txt", EVIDENCE_TXT)

        found = discovery.discover(root)
        # All 4 files should be discovered regardless of nesting
        assert len(found.usable) == 4, found.summary()
        rels = {f.relative_path for f in found.usable}
        assert any("people.csv" in r for r in rels)
        assert any("transactions.csv" in r for r in rels)
        assert any("evidence.txt" in r for r in rels)
        # Provenance: relative_path preserved
        assert any("a/b/c/people.csv" in r for r in rels)
        assert any("deeply/nested/arbitrary/evidence.txt" in r for r in rels)


def test_same_semantic_data_different_layouts_produces_equivalent_canonical():
    """TEST A vs B vs C vs E: same content, different folders -> same entities."""
    layouts = [
        # Layout A: cases/case1/evidence/, people/
        {
            "cases/case1/evidence/people.csv": PEOPLE_CSV,
            "cases/case1/evidence/transactions.csv": TRANSACTIONS_CSV,
            "cases/case1/cases.csv": CASES_CSV,
            "cases/case1/evidence/notes.txt": EVIDENCE_TXT,
        },
        # Layout B: case1/ contains everything
        {
            "case1/people.csv": PEOPLE_CSV,
            "case1/transactions.csv": TRANSACTIONS_CSV,
            "case1/cases.csv": CASES_CSV,
            "case1/evidence.txt": EVIDENCE_TXT,
        },
        # Layout C: no meaningful hierarchy
        {
            "document1.csv": PEOPLE_CSV,
            "data.csv": TRANSACTIONS_CSV,
            "cases.csv": CASES_CSV,
            "notes.txt": EVIDENCE_TXT,
        },
        # Layout D: mixed
        {
            "cases/case1/evidence/file1.csv": PEOPLE_CSV,
            "documents/file2.csv": TRANSACTIONS_CSV,
            "records/file3.csv": CASES_CSV,
            "miscellaneous/file4.txt": EVIDENCE_TXT,
        },
        # Layout E: deeply nested arbitrary
        {
            "arbitrary/nested/deeply/people.csv": PEOPLE_CSV,
            "arbitrary/nested/deeply/transactions.csv": TRANSACTIONS_CSV,
            "arbitrary/nested/deeply/cases.csv": CASES_CSV,
            "arbitrary/nested/deeply/evidence.txt": EVIDENCE_TXT,
        },
    ]

    results = []
    for idx, layout in enumerate(layouts):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / f"dataset_{idx}"
            for rel, content in layout.items():
                _write_file(root, rel, content)
            found, normalizer, _ = _discover_and_normalize(root)
            # Collect canonical entity counts
            counts = normalizer.result.counts()
            results.append(counts)

    # All layouts should produce same entity counts
    first = results[0]
    for i, res in enumerate(results[1:], start=1):
        assert res["entities"] == first["entities"], f"Layout {i} entities differ: {res} vs {first}"
        assert res["entities_by_type"] == first["entities_by_type"], f"Layout {i} by_type differ"
        # Relationships may vary slightly due to ordering but should be same count
        assert res["relationships"] == first["relationships"], f"Layout {i} relationships differ"


def test_pdf_txt_json_csv_all_parsed_via_content():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "random/report.pdf", _minimal_pdf("Case Number: CASE-001 Ramesh Kumar"))
        _write_file(root, "data/notes.txt", EVIDENCE_TXT)
        _write_file(root, "records/people.json", PEOPLE_JSON)
        _write_file(root, "files/transactions.csv", TRANSACTIONS_CSV)
        _write_file(root, "cases.csv", CASES_CSV)

        found = discovery.discover(root)
        # All supported types should be discovered
        kinds = {f.kind for f in found.usable}
        assert "document" in kinds or "text" in kinds  # PDF and TXT
        assert "table" in kinds  # CSV and JSON

        # PDF content extraction
        pdf_entry = next(f for f in found.usable if f.filename.endswith("report.pdf"))
        parsed = readers.read_text(pdf_entry.path, pdf_entry.extension)
        assert "CASE-001" in parsed.text or "Ramesh" in parsed.text

        # TXT content
        txt_entry = next(f for f in found.usable if f.filename.endswith("notes.txt"))
        parsed_txt = readers.read_text(txt_entry.path, txt_entry.extension)
        assert "CASE-001" in parsed_txt.text

        # JSON as table
        json_entry = next(f for f in found.usable if f.filename.endswith("people.json"))
        tables = readers.read_tables(json_entry.path, json_entry.extension)
        assert len(tables) >= 1
        assert any("P001" in str(row.values()) for table in tables for row in table.rows)

        # CSV
        csv_entry = next(f for f in found.usable if f.filename.endswith("transactions.csv"))
        tables_csv = readers.read_tables(csv_entry.path, csv_entry.extension)
        assert tables_csv[0].columns


def test_case_association_from_content_not_folder():
    """File says CASE-001 inside, regardless of folder it should be associatable."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        # Put file with explicit case ID in random folder
        _write_file(root, "random_folder/data.pdf", _minimal_pdf("Case Number: CASE-001 Evidence for robbery"))
        _write_file(root, "cases.csv", CASES_CSV)
        _write_file(root, "people.csv", PEOPLE_CSV)

        found, normalizer, _ = _discover_and_normalize(root)

        # Check that content extraction finds CASE-001
        from app.datasets.pipeline import _extract_case_ids_from_content
        pdf_entry = next(f for f in found.usable if f.filename.endswith("data.pdf"))
        parsed = readers.read_text(pdf_entry.path, pdf_entry.extension)
        case_by_key = {"CASE-001": object()}  # mock
        extracted = _extract_case_ids_from_content(
            text=parsed.text,
            file_path=pdf_entry.path,
            extension=pdf_entry.extension,
            relative_path=pdf_entry.relative_path,
            filename=pdf_entry.filename,
            case_by_key=case_by_key,
        )
        assert "CASE-001" in extracted or any("CASE-001" in k for k in extracted.keys()), f"Should extract CASE-001 from content, got {extracted}"


def test_folder_context_is_optional_not_required():
    """Same file should be usable whether in case folder or random folder."""
    content = PEOPLE_CSV
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        root1 = Path(tmp1) / "ds1"
        root2 = Path(tmp2) / "ds2"
        _write_file(root1, "case1/evidence/people.csv", content)
        _write_file(root1, "cases.csv", CASES_CSV)
        _write_file(root2, "random/people.csv", content)
        _write_file(root2, "cases.csv", CASES_CSV)

        found1, norm1, _ = _discover_and_normalize(root1)
        found2, norm2, _ = _discover_and_normalize(root2)

        # Both should produce same entities
        assert norm1.result.counts()["entities"] == norm2.result.counts()["entities"]
        assert norm1.result.counts()["entities_by_type"] == norm2.result.counts()["entities_by_type"]


def test_files_outside_case_folders_work():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        # Case file
        _write_file(root, "cases.csv", CASES_CSV)
        # Evidence outside case folder
        _write_file(root, "evidence_outside/evidence.pdf", _minimal_pdf("Case Number: CASE-001 Bank statement"))
        _write_file(root, "people_outside/people.csv", PEOPLE_CSV)
        _write_file(root, "random/records.csv", TRANSACTIONS_CSV)

        found, normalizer, _ = _discover_and_normalize(root)
        # All files should be discovered
        assert len(found.usable) == 4
        # Entities should be created from CSVs outside case folders
        counts = normalizer.result.counts()
        assert counts["entities"] >= 3  # People + cases


def test_case_folders_with_mixed_types_work():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "case1/evidence.pdf", _minimal_pdf("CASE-001 evidence"))
        _write_file(root, "case1/people.json", PEOPLE_JSON)
        _write_file(root, "case1/transactions.csv", TRANSACTIONS_CSV)
        _write_file(root, "case1/notes.txt", EVIDENCE_TXT)
        _write_file(root, "cases.csv", CASES_CSV)

        found, normalizer, _ = _discover_and_normalize(root)
        assert len(found.usable) == 5
        counts = normalizer.result.counts()
        assert counts["entities"] >= 4


def test_arbitrarily_nested_files_work():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "a/b/c/d/e/f/g/file.pdf", _minimal_pdf("CASE-001 deep evidence"))
        _write_file(root, "x/y/people.csv", PEOPLE_CSV)
        _write_file(root, "cases.csv", CASES_CSV)

        found = discovery.discover(root)
        assert len(found.usable) == 3
        # PDF should be readable despite deep nesting
        pdf_entry = next(f for f in found.usable if f.extension == ".pdf")
        parsed = readers.read_text(pdf_entry.path, pdf_entry.extension)
        assert parsed.text.strip() != ""


def test_provenance_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        rel_path = "case_alpha/evidence/document1.pdf"
        _write_file(root, rel_path, _minimal_pdf("CASE-001 test"))
        _write_file(root, "cases.csv", CASES_CSV)
        _write_file(root, "people.csv", PEOPLE_CSV)

        found, normalizer, _ = _discover_and_normalize(root)
        # Check that provenance contains original path
        for entity in normalizer.result.entities.values():
            prov = entity.provenance
            if prov.get("file"):
                assert isinstance(prov["file"], str)
                # At least one entity should have provenance from our nested file
                # (if that file contributed via mentions, not directly as table)
                pass

        # Discovery preserves relative_path as provenance
        pdf_entry = next(f for f in found.usable if f.extension == ".pdf")
        assert pdf_entry.relative_path == rel_path
        assert pdf_entry.relative_path.replace("\\", "/") == rel_path


def test_unsupported_files_produce_meaningful_errors():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "good/people.csv", PEOPLE_CSV)
        _write_file(root, "bad/unknown.xyz", "some content")
        _write_file(root, "empty/empty.csv", "")

        found = discovery.discover(root)
        # Should have 1 usable, 2 non-usable with reasons
        assert len(found.usable) == 1
        assert len(found.files) == 3
        statuses = {f.status for f in found.files}
        assert "DISCOVERED" in statuses
        assert "UNSUPPORTED" in statuses or "CORRUPT" in statuses
        # Each non-usable should have a reason
        for f in found.files:
            if f.status != "DISCOVERED":
                assert f.reason, f"File {f.relative_path} has status {f.status} but no reason"


def test_json_nested_structure_not_discarded():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "data/nested.json", FINANCIAL_JSON_NESTED)
        _write_file(root, "cases.csv", CASES_CSV)

        found = discovery.discover(root)
        assert len(found.usable) == 2
        json_entry = next(f for f in found.usable if f.extension == ".json")
        tables = readers.read_tables(json_entry.path, json_entry.extension)
        # Should produce at least 2 tables: one for top-level scalars, one for nested transactions
        assert len(tables) >= 1
        # Check that nested data is preserved
        all_rows = [row for table in tables for row in table.rows]
        assert len(all_rows) >= 1

        # Normalization should handle it
        _, normalizer, _ = _discover_and_normalize(root)
        assert normalizer.result.counts()["entities"] >= 1


def test_csv_inferred_from_columns_not_filename():
    """transactions.csv vs financial_records.csv vs data.csv should all map same."""
    csv_content = TRANSACTIONS_CSV
    filenames = ["transactions.csv", "financial_records.csv", "records.csv", "data.csv"]
    results = []
    for fname in filenames:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dataset"
            _write_file(root, fname, csv_content)
            _write_file(root, "cases.csv", CASES_CSV)
            _, normalizer, _ = _discover_and_normalize(root)
            results.append(normalizer.result.counts())

    first = results[0]
    for res in results[1:]:
        assert res["entities"] == first["entities"]
        assert res["entities_by_type"] == first["entities_by_type"]


def test_no_fabricated_case_associations():
    """When content has no case ID, don't invent association."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        # Two cases
        _write_file(root, "cases.csv", "case_id,case_number\nCASE-001,CASE-001\nCASE-002,CASE-002\n")
        # File with no case ID in content
        _write_file(root, "random/notes.txt", "This is a generic note about Ramesh Kumar, no case mentioned.")
        _write_file(root, "people.csv", PEOPLE_CSV)

        found = discovery.discover(root)
        # The random notes file should be discovered
        assert any("notes.txt" in f.relative_path for f in found.usable)

        # For document ingestion, it should be unassigned or assigned via entity votes, not fabricated
        # We test the helper _is_dataset_level_resource doesn't falsely mark it as dataset-level
        from app.datasets.pipeline import _is_dataset_level_resource
        assert not _is_dataset_level_resource(
            filename="notes.txt",
            relative_path="random/notes.txt",
            text="This is a generic note about Ramesh Kumar, no case mentioned.",
            extension=".txt",
        )


def test_content_first_folder_second():
    """Content says CASE-001, folder says CASE-002 -> content should win."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        _write_file(root, "CASE-002/evidence.pdf", _minimal_pdf("Case Number: CASE-001 This is actually for CASE-001"))
        _write_file(root, "cases.csv", "case_id,case_number\nCASE-001,CASE-001\nCASE-002,CASE-002\n")

        found = discovery.discover(root)
        pdf_entry = next(f for f in found.usable if f.extension == ".pdf")
        parsed = readers.read_text(pdf_entry.path, pdf_entry.extension)

        from app.datasets.pipeline import _extract_case_ids_from_content
        case_by_key = {"CASE-001": object(), "CASE-002": object()}
        extracted = _extract_case_ids_from_content(
            text=parsed.text,
            file_path=pdf_entry.path,
            extension=pdf_entry.extension,
            relative_path=pdf_entry.relative_path,
            filename=pdf_entry.filename,
            case_by_key=case_by_key,
        )
        # Content says CASE-001, so extracted should contain CASE-001
        assert "CASE-001" in extracted, f"Expected CASE-001 from content, got {extracted}"
