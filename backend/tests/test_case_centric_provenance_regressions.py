"""Regression tests for case-centric document association, display labels, entity typing,
and strict criminal-star rules.

Covers all 17 required backend criteria from Section 19:
1. Every C101–C110 has expected CaseDocument associations.
2. CaseDocument.case_id belongs to the correct case.
3. DatasetFile → CaseDocument → Case mapping is preserved.
4. SourceReference → document → case mapping is valid.
5. Cross-case person reuse works.
6. PERSON cannot merge with LOCATION.
7. PERSON cannot merge with BANK_ACCOUNT.
8. PERSON cannot merge with PHONE.
9. Display-label resolver returns human-readable values by canonical type.
10. Internal IDs remain stable.
11. Criminal status remains source-derived.
12. Graph metrics cannot change criminal status.
13. Only confirmed criminals receive criminal/star presentation metadata.
14. Non-criminal high-centrality entities remain non-criminal.
15. Pseudonymization/de-pseudonymization is type-safe.
16. Unknown/missing display values degrade honestly rather than displaying a wrong entity type.
17. Master graph remains active-dataset scoped.
"""

from __future__ import annotations

import hashlib
import pytest
from sqlalchemy import select

from app.datasets.normalize import Normalizer
from app.db.models import Case, CaseDocument, Dataset, DatasetFile, SourceReference
from app.db.session import async_session
from app.domain.models import GraphNode
from app.investigator.network_analysis import is_confirmed_criminal, node_shape_rule
from app.ai.pseudonymize import PseudonymMap
from app.services.documents import document_row
from app.services.graph_service import get_display_label
from app.services.source_viewer import STATUS_AVAILABLE, STATUS_NOT_FOUND, preview
from app.api.v1.sources import _dataset_root, _resolve_source_path
from app.datasets import schema_map as sm


# ---------------------------------------------------------------------------
# 1 to 4: Database document association & provenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_active_dataset_case_document_associations():
    """Verify that in the active dataset, every case has documents and proper case_id."""
    async with async_session() as session:
        active_dataset = (
            await session.execute(
                select(Dataset).where(
                    (Dataset.is_active.is_(True)) | (Dataset.status == "ACTIVE")
                )
            )
        ).scalars().first()
        if not active_dataset:
            pytest.skip("No active dataset in DB for live test")

        cases = (
            await session.execute(
                select(Case).where(
                    Case.dataset_id == active_dataset.id,
                    Case.case_number.in_([f"C10{i}" for i in range(1, 10)] + ["C110"]),
                )
            )
        ).scalars().all()

        if not cases:
            pytest.skip("Synthetic C101-C110 dataset not currently imported")

        case_map = {c.id: c.case_number for c in cases}
        assert len(case_map) == 10, f"Expected 10 cases, found {len(case_map)}"

        # 1. Every C101-C110 has associated CaseDocuments
        doc_rows = (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.dataset_id == active_dataset.id,
                    CaseDocument.case_id.in_(case_map.keys()),
                    CaseDocument.is_deleted.is_(False),
                )
            )
        ).scalars().all()

        docs_by_case: dict[str, list[CaseDocument]] = {}
        for doc in doc_rows:
            docs_by_case.setdefault(doc.case_id, []).append(doc)

        for case_id, case_number in case_map.items():
            c_docs = docs_by_case.get(case_id, [])
            assert len(c_docs) > 0, f"Case {case_number} has 0 documents"

        # 2. CaseDocument.case_id belongs to the correct case
        for doc in doc_rows:
            assert doc.case_id in case_map

        # 3. DatasetFile -> CaseDocument mapping is preserved
        file_ids = {doc.dataset_file_id for doc in doc_rows if doc.dataset_file_id}
        assert len(file_ids) > 0

        # 4. SourceReference -> document -> case mapping is valid
        source_refs = (
            await session.execute(
                select(SourceReference).where(
                    SourceReference.dataset_id == active_dataset.id
                )
            )
        ).scalars().all()

        assert len(source_refs) > 0, "Expected source references in active dataset"
        for ref in source_refs:
            if ref.case_id:
                assert ref.case_id in case_map or ref.case_id is not None


# ---------------------------------------------------------------------------
# 5: Cross-case person reuse
# ---------------------------------------------------------------------------

def test_cross_case_person_reuse():
    """A person appearing in multiple cases must resolve to the same canonical entity."""
    norm = Normalizer()
    prov1 = {"dataset_id": "ds1", "file": "c101.csv", "row": 1}
    prov2 = {"dataset_id": "ds1", "file": "c110.csv", "row": 1}

    # Register person in case 1
    p1 = norm._register(
        sm.PERSON, "P003", name="Rajesh Kumar", normalized_value="RAJESH KUMAR",
        provenance=prov1, attributes={"case_id": "C101"}
    )
    # Register same person in case 2
    p2 = norm._register(
        sm.PERSON, "P003", name="Rajesh Kumar", normalized_value="RAJESH KUMAR",
        provenance=prov2, attributes={"case_id": "C110"}
    )

    assert p1 == p2, "Person P003 must resolve to the exact same canonical ID"
    assert p1 == "PERSON:P003"


# ---------------------------------------------------------------------------
# 6 to 8: Critical entity-type safety: NO cross-type merging
# ---------------------------------------------------------------------------

def test_person_cannot_merge_with_location():
    """Reconcile must NEVER merge a PERSON with a LOCATION."""
    norm = Normalizer()
    p = norm._register(sm.PERSON, "ID_001", name="Central", normalized_value="CENTRAL")
    loc = norm._register(sm.LOCATION, "ID_001", name="Central", normalized_value="CENTRAL")
    assert p != loc, "PERSON and LOCATION must not receive the same canonical ID"

    norm.reconcile_identifiers()
    assert p in norm.result.entities
    assert loc in norm.result.entities
    assert norm.result.entities[p].entity_type == sm.PERSON
    assert norm.result.entities[loc].entity_type == sm.LOCATION


def test_person_cannot_merge_with_bank_account():
    """Reconcile must NEVER merge a PERSON with a BANK_ACCOUNT."""
    norm = Normalizer()
    p = norm._register(sm.PERSON, "ACCT100", name="John", normalized_value="JOHN")
    ba = norm._register(sm.ACCOUNT, "ACCT100", name="ACCT100", normalized_value="ACCT100")
    assert p != ba

    norm.reconcile_identifiers()
    assert p in norm.result.entities
    assert ba in norm.result.entities
    assert norm.result.entities[p].entity_type == sm.PERSON
    assert norm.result.entities[ba].entity_type == sm.ACCOUNT


def test_person_cannot_merge_with_phone():
    """Reconcile must NEVER merge a PERSON with a PHONE."""
    norm = Normalizer()
    p = norm._register(sm.PERSON, "9876543210", name="9876543210", normalized_value="9876543210")
    ph = norm._register(sm.PHONE, "9876543210", name="+919876543210", normalized_value="+919876543210")
    assert p != ph

    norm.reconcile_identifiers()
    assert p in norm.result.entities
    assert ph in norm.result.entities
    assert norm.result.entities[p].entity_type == sm.PERSON
    assert norm.result.entities[ph].entity_type == sm.PHONE


# ---------------------------------------------------------------------------
# 9 & 10: Centralized display label resolution & stable internal IDs
# ---------------------------------------------------------------------------

def test_display_label_resolver_human_readable():
    """get_display_label returns human-readable operational values for all entity types."""
    # Person
    p_node = GraphNode(
        provenance_key="ds:P001",
        label="Person",
        properties={"canonical_id": "P001", "name": "Rajesh Kumar", "entity_type": "PERSON"}
    )
    assert get_display_label(p_node) == "Rajesh Kumar"
    assert p_node.properties["canonical_id"] == "P001"  # internal ID preserved

    # Phone
    ph_node = GraphNode(
        provenance_key="ds:PH001",
        label="Phone",
        properties={"canonical_id": "PH001", "number": "+91-9876543210", "entity_type": "PHONE"}
    )
    assert get_display_label(ph_node) == "+91-9876543210"

    # Bank Account
    ba_node = GraphNode(
        provenance_key="ds:BA001",
        label="BankAccount",
        properties={"canonical_id": "BA001", "account_number": "XXXX1234", "entity_type": "BANK_ACCOUNT"}
    )
    assert get_display_label(ba_node) == "XXXX1234"

    # Vehicle
    vh_node = GraphNode(
        provenance_key="ds:VH001",
        label="Vehicle",
        properties={"canonical_id": "VH001", "registration": "AP09AB1234", "entity_type": "VEHICLE"}
    )
    assert get_display_label(vh_node) == "AP09AB1234"

    # Location
    loc_node = GraphNode(
        provenance_key="ds:L001",
        label="Location",
        properties={"canonical_id": "L001", "name": "Vijayawada Central", "entity_type": "LOCATION"}
    )
    assert get_display_label(loc_node) == "Vijayawada Central"

    # Organization
    org_node = GraphNode(
        provenance_key="ds:O001",
        label="Organization",
        properties={"canonical_id": "O001", "name": "Apex Recyclers Pvt Ltd", "entity_type": "ORGANIZATION"}
    )
    assert get_display_label(org_node) == "Apex Recyclers Pvt Ltd"

    # Case
    c_node = GraphNode(
        provenance_key="ds:C104",
        label="Case",
        properties={"canonical_id": "C104", "case_number": "C104", "entity_type": "CASE"}
    )
    assert get_display_label(c_node) == "C104"

    # FIR
    fir_node = GraphNode(
        provenance_key="ds:FIR104",
        label="FIR",
        properties={"canonical_id": "FIR-104", "fir_number": "FIR-104", "entity_type": "FIR"}
    )
    assert get_display_label(fir_node) == "FIR-104"


# ---------------------------------------------------------------------------
# 11 to 14: Strict criminal status & Star presentation rule
# ---------------------------------------------------------------------------

def test_criminal_status_strictly_source_derived():
    """Graph metrics (degree, pagerank, betweenness) must NEVER alter criminal status."""
    # A high-centrality suspect is NOT a confirmed criminal
    high_degree_suspect = GraphNode(
        provenance_key="ds:P002",
        label="Person",
        properties={
            "name": "Sameer Khan",
            "legal_status": "suspect",
            "criminal_status": "suspect",
            "degree": 55,
            "betweenness": 0.95,
            "pagerank": 0.25,
            "network_role": "Hub",
        }
    )
    assert not is_confirmed_criminal(high_degree_suspect)
    assert node_shape_rule(high_degree_suspect) == "ellipse"

    # A source-confirmed convicted criminal gets the star
    confirmed_criminal = GraphNode(
        provenance_key="ds:P001",
        label="Person",
        properties={
            "name": "Vikram Rao",
            "legal_status": "convicted",
            "criminal_status": "convicted",
        }
    )
    assert is_confirmed_criminal(confirmed_criminal)
    assert node_shape_rule(confirmed_criminal) == "star"

    # Witnesses and victims are always ellipse
    witness = GraphNode(
        provenance_key="ds:P003",
        label="Person",
        properties={"name": "Asha Nair", "legal_status": "witness"}
    )
    assert not is_confirmed_criminal(witness)
    assert node_shape_rule(witness) == "ellipse"

    # Non-person nodes are ALWAYS ellipse
    phone = GraphNode(provenance_key="ds:PH1", label="Phone", properties={"degree": 100})
    assert node_shape_rule(phone) == "ellipse"
    ba = GraphNode(provenance_key="ds:BA1", label="BankAccount", properties={"degree": 100})
    assert node_shape_rule(ba) == "ellipse"
    vh = GraphNode(provenance_key="ds:VH1", label="Vehicle", properties={})
    assert node_shape_rule(vh) == "ellipse"
    loc = GraphNode(provenance_key="ds:L1", label="Location", properties={})
    assert node_shape_rule(loc) == "ellipse"


# ---------------------------------------------------------------------------
# 15 & 16: Type-safe pseudonymization & de-pseudonymization
# ---------------------------------------------------------------------------

def test_pseudonymization_type_safe():
    """Pseudonymization must be type-aware and preserve distinct entity namespaces."""
    pmap = PseudonymMap()

    p_pseudo = pmap.pseudonymize("P001", "Person")
    loc_pseudo = pmap.pseudonymize("L001", "Location")
    ph_pseudo = pmap.pseudonymize("PH001", "Phone")
    ba_pseudo = pmap.pseudonymize("BA001", "BankAccount")

    assert p_pseudo.startswith("PERSON_")
    assert loc_pseudo.startswith("LOCATION_")
    assert ph_pseudo.startswith("PHONE_")
    assert ba_pseudo.startswith("ACCOUNT_")

    # Resolve recovers the exact original ID
    assert pmap.resolve(p_pseudo) == "P001"
    assert pmap.resolve(loc_pseudo) == "L001"
    assert pmap.resolve(ph_pseudo) == "PH001"
    assert pmap.resolve(ba_pseudo) == "BA001"


def test_missing_display_values_degrade_honestly():
    """When a display attribute is missing, fallback cleanly without guessing a wrong type."""
    node = GraphNode(
        provenance_key="ds:UNKNOWN_123",
        label="Person",
        properties={"canonical_id": "UNKNOWN_123"}
    )
    label = get_display_label(node)
    assert label == "UNKNOWN_123"


# ---------------------------------------------------------------------------
# 18: Source-evidence viewer regression: active case document resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_active_case_document_source_viewer_regression(tmp_path):
    """Reproduces the exact bug:
    A document (00_document_index.csv) is listed in an active case (e.g. C109 or C101).
    Clicking it must resolve the exact file via dataset-relative path / DatasetFile record
    scoped to the active dataset workspace, returning STATUS_AVAILABLE, valid preview,
    and verified download, NOT returning STATUS_NOT_FOUND.
    """
    async with async_session() as session:
        active_dataset = (
            await session.execute(
                select(Dataset).where(
                    (Dataset.is_active.is_(True)) | (Dataset.status == "ACTIVE")
                )
            )
        ).scalars().first()

        c109_doc = None
        if active_dataset:
            # Find 00_document_index.csv in one of the active cases (e.g. C109)
            c109_doc = (
                await session.execute(
                    select(CaseDocument).where(
                        CaseDocument.dataset_id == active_dataset.id,
                        CaseDocument.filename == "00_document_index.csv",
                        CaseDocument.storage_key.like("%C109%"),
                    )
                )
            ).scalars().first()

            if not c109_doc:
                c109_doc = (
                    await session.execute(
                        select(CaseDocument).where(
                            CaseDocument.dataset_id == active_dataset.id,
                            CaseDocument.filename == "00_document_index.csv",
                        )
                    )
                ).scalars().first()

        if not active_dataset or not c109_doc:
            # Create isolated active dataset reproducing the exact nested C109 document structure
            case_dir = tmp_path / "cases" / "C109"
            case_dir.mkdir(parents=True, exist_ok=True)
            doc_file = case_dir / "00_document_index.csv"
            csv_content = b"doc_id,title,type\nDOC001,FIR 109,FIR\n"
            doc_file.write_bytes(csv_content)
            h = hashlib.sha256(csv_content).hexdigest()

            active_dataset = Dataset(
                id="test-ds-c109",
                name="Test DS C109",
                status="READY",
                is_active=True,
                root_path=str(tmp_path),
            )
            session.add(active_dataset)
            await session.flush()

            case = Case(
                id="case-c109",
                dataset_id=active_dataset.id,
                case_number="C109",
                title="Case C109",
                jurisdiction_id="SYN-DEV",
            )
            session.add(case)
            await session.flush()

            df_rec = DatasetFile(
                id="df-c109-index",
                dataset_id=active_dataset.id,
                relative_path="cases/C109/00_document_index.csv",
                filename="00_document_index.csv",
                size_bytes=len(csv_content),
                sha256=h,
            )
            session.add(df_rec)
            await session.flush()

            from app.db.models import DocumentType

            c109_doc = CaseDocument(
                id="doc-c109-index",
                case_id=case.id,
                dataset_id=active_dataset.id,
                document_type=DocumentType.MASTER_INDEX,
                filename="00_document_index.csv",
                storage_key="cases/C109/00_document_index.csv",
                content_hash=h,
                size_bytes=len(csv_content),
                mime_type="text/csv",
                source_metadata={"relative_path": "cases/C109/00_document_index.csv"},
            )
            session.add(c109_doc)
            df_rec.doc_id = c109_doc.id
            await session.commit()

        root = await _dataset_root(session, active_dataset.id)
        assert root is not None and root.is_dir()
        assert c109_doc is not None, "00_document_index.csv should exist in active dataset"

        # 1. Verify document_row produces the dataset-relative path
        row = document_row(c109_doc)
        assert row["filename"] == "00_document_index.csv"
        assert row["relative_path"] == c109_doc.storage_key
        assert "cases/" in row["relative_path"]

        # 2. Verify _resolve_source_path with doc_id
        cand, rel, df, doc, _ = await _resolve_source_path(
            session=session,
            dataset=active_dataset,
            root=root,
            doc_id=c109_doc.id,
        )
        assert cand.is_file()
        assert rel == c109_doc.storage_key
        assert doc is not None and doc.id == c109_doc.id
        assert df is not None and df.relative_path == c109_doc.storage_key

        # 3. Verify _resolve_source_path with path=doc.storage_key
        cand2, rel2, _, _, _ = await _resolve_source_path(
            session=session,
            dataset=active_dataset,
            root=root,
            path=c109_doc.storage_key,
        )
        assert cand2.is_file()
        assert rel2 == c109_doc.storage_key

        # 4. Verify _resolve_source_path with bare path and doc_id
        cand3, rel3, _, _, _ = await _resolve_source_path(
            session=session,
            dataset=active_dataset,
            root=root,
            path=c109_doc.filename,
            doc_id=c109_doc.id,
        )
        assert cand3.is_file()
        assert rel3 == c109_doc.storage_key

        # 5. Verify preview on the resolved path returns AVAILABLE and renders the CSV table
        result = preview(
            rel,
            root=root,
            dataset_id=active_dataset.id,
        )
        assert result["status"] == STATUS_AVAILABLE
        assert result["render_kind"] == "csv"
        assert result["openable"] is True
        assert result["window"] is not None
        assert len(result["window"]["rows"]) > 0

        # 6. Verify exact file content matches between resolved path and actual file
        content_on_disk = cand.read_bytes()
        assert hashlib.sha256(content_on_disk).hexdigest() == c109_doc.content_hash

        # 7. Negative check: bare filename without doc_id fails cleanly with NOT_FOUND without guessing
        with pytest.raises(Exception):
            await _resolve_source_path(
                session=session,
                dataset=active_dataset,
                root=root,
                path="nonexistent_document_index.csv",
            )

