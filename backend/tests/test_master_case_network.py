"""Regression tests for Master Case Network (PRD & Investigation Analysis).

Covers all 12 backend requirements:
1. master case graph contains active dataset cases
2. inactive dataset cases are excluded
3. NULL/legacy dataset records are excluded
4. case-to-case edge exists only with actual supported shared entities
5. unrelated cases are not connected
6. shared person produces correct case connection
7. shared phone/account/etc. produces correct connection
8. case edge exposes supporting entities
9. case edge has provenance
10. case graph changes after dataset replacement
11. entity-level master graph remains functional
12. case/person/master scopes remain distinct
"""

import pytest
from sqlalchemy import select, delete

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType
from app.domain.models import GraphEdge, GraphNode
from app.security.deps import JurisdictionScope, Principal
from app.services.graph_service import GraphService


@pytest.mark.asyncio
async def test_master_case_network_full_lifecycle(container, users):
    """Verify Master Case Network requirements 1 through 12."""

    async with async_session() as session:
        # Create Dataset A
        ds_a = await registry.create_dataset(
            session, name="Dataset A Master Case Test", version="1", source_kind="folder"
        )
        await registry.activate(session, ds_a)
        await session.commit()

        # Cases for Dataset A:
        # C1 and C2 share person P1 and phone PH1
        # C2 and C3 share bank account BA1
        # C4 is unrelated (no shared entities with C1, C2, C3)
        c1 = Case(
            id=new_uuid(),
            case_number="C101-TEST",
            title="Burglary Ring A",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=ds_a.id,
            status=CaseStatus.OPEN,
        )
        c2 = Case(
            id=new_uuid(),
            case_number="C102-TEST",
            title="Burglary Ring B",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=ds_a.id,
            status=CaseStatus.OPEN,
        )
        c3 = Case(
            id=new_uuid(),
            case_number="C103-TEST",
            title="Money Laundering",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=ds_a.id,
            status=CaseStatus.OPEN,
        )
        c4 = Case(
            id=new_uuid(),
            case_number="C104-TEST",
            title="Isolated Theft",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=ds_a.id,
            status=CaseStatus.OPEN,
        )

        # Legacy NULL case: should be excluded from master case network
        c_legacy = Case(
            id=new_uuid(),
            case_number="L999-LEGACY",
            title="Legacy Case",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=None,
            status=CaseStatus.OPEN,
        )

        # Inactive Dataset B case: should also be excluded
        ds_b = await registry.create_dataset(
            session, name="Dataset B Inactive Test", version="1", source_kind="folder"
        )
        c_inactive = Case(
            id=new_uuid(),
            case_number="B201-INACTIVE",
            title="Inactive Case",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=ds_b.id,
            status=CaseStatus.OPEN,
        )

        session.add_all([c1, c2, c3, c4, c_legacy, c_inactive])
        await session.flush()

        # Add a document to C1
        doc1 = CaseDocument(
            id=new_uuid(),
            case_id=c1.id,
            dataset_id=ds_a.id,
            document_type=DocumentType.FIR,
            filename="FIR_2026_001.txt",
            storage_key="cases/c1/fir.txt",
            content_hash="sha256_mock_hash_001",
        )
        session.add(doc1)
        await session.commit()

        principal = Principal(users["INV-0001"])
        scope = JurisdictionScope(principal, granted_jurisdictions=set(), granted_case_ids=set())
        svc = GraphService()

        # Setup Graph nodes in store
        # P1: Confirmed criminal Person shared between C1 and C2
        node_p1 = GraphNode(
            provenance_key=f"person:p1:{ds_a.id}",
            label="Person",
            properties={
                "name": "Vikram Singh",
                "case_ids": [c1.id, c2.id],
                "dataset_id": ds_a.id,
                "criminal_status": "CONVICTED",
                "source_doc_ids": ["FIR_2026_001.txt"],
                "origin": {"file": "FIR_2026_001.txt", "row": 12},
            },
        )
        # PH1: Phone shared between C1 and C2
        node_ph1 = GraphNode(
            provenance_key=f"phone:ph1:{ds_a.id}",
            label="Phone",
            properties={
                "name": "+919876543210",
                "case_ids": [c1.id, c2.id],
                "dataset_id": ds_a.id,
                "source_doc_ids": ["CDR_2026_04.csv"],
                "origin": {"file": "CDR_2026_04.csv", "row": 50},
            },
        )
        # BA1: Bank Account shared between C2 and C3
        node_ba1 = GraphNode(
            provenance_key=f"bank:ba1:{ds_a.id}",
            label="BankAccount",
            properties={
                "name": "ACC-998877",
                "case_ids": [c2.id, c3.id],
                "dataset_id": ds_a.id,
                "source_doc_ids": ["FIN_2026.csv"],
                "origin": {"file": "FIN_2026.csv", "row": 105},
            },
        )
        # P4: Person only in C4
        node_p4 = GraphNode(
            provenance_key=f"person:p4:{ds_a.id}",
            label="Person",
            properties={
                "name": "Rohan Verma",
                "case_ids": [c4.id],
                "dataset_id": ds_a.id,
                "source_doc_ids": ["FIR_C4.txt"],
            },
        )
        # Legacy node in L999
        node_legacy = GraphNode(
            provenance_key=f"person:legacy:{c_legacy.id}",
            label="Person",
            properties={"name": "Legacy Person", "case_ids": [c_legacy.id]},
        )

        svc.container.graph_store.upsert_nodes([node_p1, node_ph1, node_ba1, node_p4, node_legacy])

        # Add cross-case relationship edge between P1 and PH1
        edge_p1_ph1 = GraphEdge(
            source_key=node_p1.provenance_key,
            target_key=node_ph1.provenance_key,
            rel_type="USES_PHONE",
            properties={"source_doc_id": "CDR_2026_04.csv", "origin": {"file": "CDR_2026_04.csv", "row": 50}},
        )
        svc.container.graph_store.upsert_edges([edge_p1_ph1])

        # -------------------------------------------------------------
        # 1. Master case graph contains active dataset cases
        # 2. Inactive dataset cases are excluded
        # 3. NULL/legacy dataset records are excluded
        # -------------------------------------------------------------
        net = await svc.master_case_network(session, scope)
        case_ids_returned = {n["id"] for n in net["nodes"]}

        assert c1.id in case_ids_returned
        assert c2.id in case_ids_returned
        assert c3.id in case_ids_returned
        assert c4.id in case_ids_returned
        # Inactive and legacy must be excluded
        assert c_inactive.id not in case_ids_returned
        assert c_legacy.id not in case_ids_returned

        # Case nodes must be circles
        for n in net["nodes"]:
            assert n["label"] == "CASE"
            assert n["is_criminal"] is False

        # -------------------------------------------------------------
        # 4. Case-to-case edge exists only with actual supported shared entities
        # 5. Unrelated cases are not connected
        # -------------------------------------------------------------
        edge_pairs = {(e["source"], e["target"]) for e in net["edges"]}
        c1_c2_connected = (c1.id, c2.id) in edge_pairs or (c2.id, c1.id) in edge_pairs
        c2_c3_connected = (c2.id, c3.id) in edge_pairs or (c3.id, c2.id) in edge_pairs
        c1_c3_connected = (c1.id, c3.id) in edge_pairs or (c3.id, c1.id) in edge_pairs
        c1_c4_connected = (c1.id, c4.id) in edge_pairs or (c4.id, c1.id) in edge_pairs

        assert c1_c2_connected, "C1 and C2 share P1 and PH1, must be connected"
        assert c2_c3_connected, "C2 and C3 share BA1, must be connected"
        assert not c1_c3_connected, "C1 and C3 share no entities directly, must not have a direct edge"
        assert not c1_c4_connected, "C4 is unrelated, must not be connected to C1"

        # -------------------------------------------------------------
        # 6. Shared person produces correct case connection
        # 7. Shared phone/account/etc. produces correct connection
        # 8. Case edge exposes supporting entities
        # 9. Case edge has provenance / evidence pointers
        # -------------------------------------------------------------
        c1_c2_edge = next(
            e for e in net["edges"]
            if (e["source"] == c1.id and e["target"] == c2.id)
            or (e["source"] == c2.id and e["target"] == c1.id)
        )
        assert c1_c2_edge["shared_entity_count"] == 2  # P1 and PH1
        shared_keys = {ent["provenance_key"] for ent in c1_c2_edge["shared_entities"]}
        assert node_p1.provenance_key in shared_keys
        assert node_ph1.provenance_key in shared_keys

        # Check P1 is marked as criminal star, PH1 is not
        p1_info = next(ent for ent in c1_c2_edge["shared_entities"] if ent["provenance_key"] == node_p1.provenance_key)
        assert p1_info["is_criminal"] is True
        assert p1_info["criminal_status"] == "CONVICTED"

        ph1_info = next(ent for ent in c1_c2_edge["shared_entities"] if ent["provenance_key"] == node_ph1.provenance_key)
        assert ph1_info["is_criminal"] is False

        # Check analytical basis
        assert "Shared person" in c1_c2_edge["analytical_basis"]
        assert "Shared phone" in c1_c2_edge["analytical_basis"]

        # Check provenance in supporting evidence
        assert len(c1_c2_edge["supporting_evidence"]) > 0
        assert any(ev["category"] in {"CDR", "FIR"} for ev in c1_c2_edge["supporting_evidence"])
        assert "Vikram Singh" in c1_c2_edge["why"]

        # Check C2 ↔ C3 edge for shared bank account
        c2_c3_edge = next(
            e for e in net["edges"]
            if (e["source"] == c2.id and e["target"] == c3.id)
            or (e["source"] == c3.id and e["target"] == c2.id)
        )
        assert "Shared bank account" in c2_c3_edge["analytical_basis"]

        # -------------------------------------------------------------
        # 11. Entity-level master graph remains functional
        # 12. Case / person / master scopes remain distinct
        # -------------------------------------------------------------
        entity_master = await svc.master_graph(session, scope)
        assert "nodes" in entity_master
        assert "edges" in entity_master
        assert any(n["provenance_key"] == node_p1.provenance_key for n in entity_master["nodes"])
        assert net["mode"] == "master_case"
        assert entity_master["mode"] == "master"

        # -------------------------------------------------------------
        # 10. Case graph changes after dataset replacement
        # -------------------------------------------------------------
        # Activate Dataset B
        await registry.activate(session, ds_b)
        await session.commit()

        net_b = await svc.master_case_network(session, scope)
        case_ids_b = {n["id"] for n in net_b["nodes"]}
        assert c_inactive.id in case_ids_b
        assert c1.id not in case_ids_b
        assert c2.id not in case_ids_b

        # Cleanup
        await registry.purge_dataset_data(session, ds_a.id)
        await registry.purge_dataset_data(session, ds_b.id)
        await session.execute(delete(Case).where(Case.id.in_([c_legacy.id])))
        await session.commit()
        svc.container.graph_store.purge_dataset(ds_a.id)
        svc.container.graph_store.purge_dataset(ds_b.id)


def test_master_case_network_api_endpoint(client, investigator_headers):
    """Verify GET /api/v1/graph/master/case-network route works via HTTP."""
    resp = client.get("/api/v1/graph/master/case-network", headers=investigator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "master_case"
    assert "nodes" in data
    assert "edges" in data
    assert "counts" in data
    for node in data["nodes"]:
        assert node["label"] == "CASE"
        assert node["is_criminal"] is False

