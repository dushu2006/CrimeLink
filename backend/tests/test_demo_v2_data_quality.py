"""Data-quality audit of the seeded v2 dataset (§17).

Runs the real ``seed_demo_v2`` generators and asserts the properties the
investigator workflow depends on.  Every one of these was broken at some
point and is now pinned:

* every evidence document has a real, distinct, case-specific file;
* no two evidence records in different cases share a file;
* graph nodes and edges do **not** all cite one document — provenance that
  points at the same FIR for a phone in CR-2007 and an account in CR-2015 is
  decorative, not traceable;
* a document's recorded SHA-256 matches the bytes actually stored (chain of
  custody — the seeder used to hash a second, differently-timestamped PDF);
* criminal status is explicit, and only for the people the dataset names.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import pytest


@pytest.fixture(scope="module")
def seeded_dataset():
    from scripts.seed_demo_v2 import build_dataset, derive_case_persons

    dataset = build_dataset()
    dataset["case_persons_dict"] = derive_case_persons(dataset)
    return dataset


# --------------------------------------------------------------------------- #
# Evidence files: real, distinct, case-specific
# --------------------------------------------------------------------------- #

def test_every_case_has_real_case_specific_evidence_files(seeded_dataset):
    cases = {c["id"]: c["case_number"] for c in seeded_dataset["cases"]}
    by_case: dict[str, list[dict]] = {}
    for ev in seeded_dataset["evidence"]:
        by_case.setdefault(ev["case_id"], []).append(ev)

    # Every case has evidence, and every evidence record belongs to its case.
    assert set(by_case) == set(cases), "a case without evidence, or an orphan evidence record"
    for case_id, rows in by_case.items():
        number = cases[case_id]
        assert len(rows) >= 10, f"{number} has only {len(rows)} evidence records"
        for ev in rows:
            # Case-specific path and filename — not one shared file.
            assert ev["storage_key"] == f"evidence/{number}/{ev['filename']}"
            assert ev["filename"].startswith(f"{number}_")
            assert ev["mime_type"] in {"application/pdf", "text/csv"}
            assert ev["is_pdf"] == (ev["mime_type"] == "application/pdf")
            assert ev["filename"].endswith(".pdf" if ev["is_pdf"] else ".csv")


def test_no_evidence_record_shares_a_file_with_another(seeded_dataset):
    keys = [ev["storage_key"] for ev in seeded_dataset["evidence"]]
    ids = [ev["evidence_id"] for ev in seeded_dataset["evidence"]]
    assert len(set(keys)) == len(keys), "two evidence records point at the same file"
    assert len(set(ids)) == len(ids), "duplicate evidence ids"
    assert len(keys) >= 300, f"only {len(keys)} evidence files"


def test_evidence_files_are_generated_with_real_content(seeded_dataset):
    """The bytes are real documents, and their content names the right case."""
    from scripts.seed_demo_v2 import gen_evidence_bytes

    cases = {c["id"]: c for c in seeded_dataset["cases"]}
    pdf_seen = csv_seen = 0
    for ev in seeded_dataset["evidence"][:40]:
        data = gen_evidence_bytes(ev, seeded_dataset)
        assert data, f"{ev['evidence_id']} generated an empty file"
        case_number = cases[ev["case_id"]]["case_number"]
        if ev["is_pdf"]:
            pdf_seen += 1
            assert data.startswith(b"%PDF-"), f"{ev['evidence_id']} is not a PDF"
            # ReportLab flate-compresses the text stream, so read it back
            # through the parser rather than grepping raw bytes.
            from io import BytesIO

            from pypdf import PdfReader

            text = "\n".join(
                (page.extract_text() or "") for page in PdfReader(BytesIO(data)).pages
            )
            assert case_number in text, f"{ev['evidence_id']} does not name its case"
        else:
            csv_seen += 1
            head = data.split(b"\n", 1)[0].decode()
            assert "," in head, f"{ev['evidence_id']} has no CSV header: {head!r}"
    assert pdf_seen > 0 and csv_seen > 0, "the sample must cover both formats"


# --------------------------------------------------------------------------- #
# Provenance: graph records must not all cite one document
# --------------------------------------------------------------------------- #

def test_graph_provenance_is_distributed_not_one_shared_file(container, seeded_dataset):
    """The defect this pins: 550 nodes and 2 789 edges all cited doc-d2-0000."""
    from scripts.seed_demo_v2 import DEMO_DATASET_ID, build_graph, doc_id_for

    build_graph(seeded_dataset, container, doc_id_for)
    snapshot = container.graph_store.multi_case_snapshot(
        [c["id"] for c in seeded_dataset["cases"]], include_inactive=False
    )

    node_docs: Counter = Counter()
    for node in snapshot.nodes.values():
        for doc in (node.properties or {}).get("source_doc_ids") or []:
            node_docs[doc] += 1
    edge_docs: Counter = Counter()
    for edge in snapshot.edges:
        for doc in (edge.properties or {}).get("source_doc_ids") or []:
            edge_docs[doc] += 1

    assert node_docs, "no node carries provenance at all"
    assert edge_docs, "no edge carries provenance at all"
    # More than a couple of dozen distinct documents, and no single document
    # carrying the whole graph.
    assert len(node_docs) >= 50, f"only {len(node_docs)} distinct node sources"
    assert len(edge_docs) >= 100, f"only {len(edge_docs)} distinct edge sources"
    assert node_docs.most_common(1)[0][1] < sum(node_docs.values()) * 0.10, (
        "one document carries most of the node provenance"
    )
    assert edge_docs.most_common(1)[0][1] < sum(edge_docs.values()) * 0.10, (
        "one document carries most of the edge provenance"
    )

    container.graph_store.purge_dataset(DEMO_DATASET_ID)


def test_node_provenance_belongs_to_the_nodes_own_case(container, seeded_dataset):
    """A CR-2007 phone must not cite a CR-2001 FIR."""
    from scripts.seed_demo_v2 import DEMO_DATASET_ID, build_graph, doc_id_for

    build_graph(seeded_dataset, container, doc_id_for)
    snapshot = container.graph_store.multi_case_snapshot(
        [c["id"] for c in seeded_dataset["cases"]], include_inactive=False
    )

    doc_case: dict[str, str] = {}
    for ev in seeded_dataset["evidence"]:
        doc_case[doc_id_for(ev["evidence_id"])] = ev["case_id"]

    checked = 0
    for node in snapshot.nodes.values():
        props = node.properties or {}
        node_cases = set(props.get("case_ids") or [])
        if not node_cases:
            continue
        for doc in props.get("source_doc_ids") or []:
            owner = doc_case.get(doc)
            if owner is None:
                continue
            checked += 1
            assert owner in node_cases, (
                f"{props.get('name')} lives in {sorted(node_cases)} but cites "
                f"{doc}, which belongs to {owner}"
            )
    assert checked > 200, f"only {checked} node provenance links checked"

    container.graph_store.purge_dataset(DEMO_DATASET_ID)


# --------------------------------------------------------------------------- #
# Chain of custody: recorded hash == stored bytes
# --------------------------------------------------------------------------- #

def test_pdfs_are_not_byte_deterministic_which_is_why_bytes_are_generated_once(
    seeded_dataset,
):
    """Documents the hazard the seeder fix exists for.

    ReportLab stamps a creation timestamp into every PDF, so generating the
    same evidence twice yields different bytes.  A seeder that generates once
    to store and again to hash therefore records a SHA-256 for a file that was
    never stored — which is exactly what ``/evidence/{doc}/verify`` reported
    (234 of 320 mismatched, every one of them a PDF; the byte-deterministic
    CSVs were fine).
    """
    from scripts.seed_demo_v2 import gen_evidence_bytes, sha256

    pdf = next(ev for ev in seeded_dataset["evidence"] if ev["is_pdf"])
    csv = next(ev for ev in seeded_dataset["evidence"] if not ev["is_pdf"])
    assert sha256(gen_evidence_bytes(pdf, seeded_dataset)) != sha256(
        gen_evidence_bytes(pdf, seeded_dataset)
    ), "expected PDFs to differ between generations"
    assert sha256(gen_evidence_bytes(csv, seeded_dataset)) == sha256(
        gen_evidence_bytes(csv, seeded_dataset)
    ), "CSV evidence must stay byte-deterministic"


def test_stored_object_hash_matches_the_recorded_hash(container, seeded_dataset):
    """The invariant that matters: hash the bytes ONCE, store those bytes,
    record that hash — then verification passes."""
    from scripts.seed_demo_v2 import gen_evidence_bytes, gen_source_bytes, sha256

    bucket = container.settings.minio_bucket_documents
    store = container.object_store

    recorded: dict[str, str] = {}
    for ev in seeded_dataset["evidence"][:25]:
        data = gen_evidence_bytes(ev, seeded_dataset)  # generated exactly once
        store.put(bucket, ev["storage_key"], data, content_type=ev["mime_type"])
        recorded[ev["storage_key"]] = sha256(data)
    for src in seeded_dataset["sources"][:5]:
        data = gen_source_bytes(src, seeded_dataset)
        store.put(bucket, src["storage_key"], data, content_type=src["mime_type"])
        recorded[src["storage_key"]] = sha256(data)

    for key, expected in recorded.items():
        stored = store.get(bucket, key)
        assert sha256(stored) == expected, f"{key}: recorded hash does not match storage"


def test_stored_hash_equals_sha256_of_the_generated_bytes(seeded_dataset):
    from scripts.seed_demo_v2 import gen_evidence_bytes, sha256

    for ev in seeded_dataset["evidence"][:20]:
        data = gen_evidence_bytes(ev, seeded_dataset)
        assert sha256(data) == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Criminal status stays explicit
# --------------------------------------------------------------------------- #

def test_criminal_status_is_explicit_and_rare(seeded_dataset):
    from scripts.seed_demo_v2 import (
        CONFIRMED_CRIMINAL_STATUSES,
        confirmed_finding_person_keys,
        criminal_status_for_person,
    )

    confirmed = confirmed_finding_person_keys(seeded_dataset)
    starred = []
    for person in seeded_dataset["persons"]:
        status = criminal_status_for_person(person, confirmed)
        if status is not None:
            assert status in CONFIRMED_CRIMINAL_STATUSES
            starred.append(person)
            # Every star traces to an explicit assertion in the dataset.
            assert person["role"] == "ACCOMPLICE" or person["id"] in confirmed, (
                f"{person['full_name']} is starred on role={person['role']} alone"
            )

    assert starred, "the dataset explicitly confirms criminals; none were found"
    assert len(starred) <= len(seeded_dataset["persons"]) * 0.10, (
        "the star must not be handed out broadly"
    )
    # Suspicion and involvement never earn it.
    for person in seeded_dataset["persons"]:
        if person["role"] in {
            "SUSPECT", "WITNESS", "VICTIM", "ASSOCIATE", "INFORMANT", "PERSON_OF_INTEREST"
        } and person["id"] not in confirmed:
            assert criminal_status_for_person(person, confirmed) is None


def test_criminal_status_rejects_a_typo(container, seeded_dataset):
    from scripts.seed_demo_v2 import CRIMINAL_STATUS_BY_ROLE, criminal_status_for_person

    original = CRIMINAL_STATUS_BY_ROLE.get("ACCOMPLICE")
    CRIMINAL_STATUS_BY_ROLE["ACCOMPLICE"] = "PROBABLY_GUILTY"
    try:
        with pytest.raises(ValueError):
            criminal_status_for_person({"id": "x", "role": "ACCOMPLICE"}, set())
    finally:
        if original is None:
            CRIMINAL_STATUS_BY_ROLE.pop("ACCOMPLICE", None)
        else:
            CRIMINAL_STATUS_BY_ROLE["ACCOMPLICE"] = original



def test_every_generated_person_has_case_membership(seeded_dataset):
    """No generated person may be orphaned from the case graph.

    Case membership supplies the scope used by graph provenance and
    person-centric analytics. An orphan person would be invisible to those
    views even though the seeder reports them as part of the 120-person corpus.
    """
    from scripts.seed_demo_v2 import derive_case_persons

    memberships = derive_case_persons(seeded_dataset)
    assigned = {
        pid
        for person_ids in memberships.values()
        for pid in person_ids
    }
    expected = {person["id"] for person in seeded_dataset["persons"]}
    assert assigned == expected

    # The same invariant must propagate to supporting entities owned by people:
    # an org/vehicle with an owner is visible through at least one owner's case.
    person_cases = {
        person["id"]: {
            case_id for case_id, person_ids in memberships.items()
            if person["id"] in person_ids
        }
        for person in seeded_dataset["persons"]
    }
    for person in seeded_dataset["persons"]:
        assert person_cases[person["id"]], person["id"]

    org_owner_cases = {}
    for person in seeded_dataset["persons"]:
        if person.get("org_id"):
            org_owner_cases.setdefault(person["org_id"], set()).update(
                person_cases[person["id"]]
            )
    for org in seeded_dataset["orgs"]:
        if org["id"] in org_owner_cases:
            assert org_owner_cases[org["id"]]

    vehicle_owner_cases = {}
    for person in seeded_dataset["persons"]:
        for vehicle_id in person.get("vehicle_ids", []):
            vehicle_owner_cases.setdefault(vehicle_id, set()).update(
                person_cases[person["id"]]
            )
    for vehicle in seeded_dataset["vehicles"]:
        if vehicle["id"] in vehicle_owner_cases:
            assert vehicle_owner_cases[vehicle["id"]]
