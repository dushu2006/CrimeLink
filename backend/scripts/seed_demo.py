"""
Production Demo Seed — Idempotent, persistent, self-contained
"""

import hashlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.db.models import User, Case, CaseDocument
from app.db.session import get_sync_sessionmaker
from app.domain.enums import Role, CaseStatus, InformationClassification, DocumentType, SourceConfidence, IngestionStatus
from app.security.passwords import hash_password

DEMO_USERS = [
    {"id": "demo-admin-0001", "badge_number": "DEMO-ADMIN", "full_name": "Demo Admin", "password": "DemoAdmin@2026", "role": Role.ADMIN, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
    {"id": "demo-investigator-0001", "badge_number": "DEMO-INVESTIGATOR", "full_name": "Demo Investigator", "password": "DemoInvestigator@2026", "role": Role.INVESTIGATOR, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
    {"id": "demo-viewer-0001", "badge_number": "DEMO-VIEWER", "full_name": "Demo Viewer", "password": "DemoViewer@2026", "role": Role.VIEWER, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
]

DEMO_CASES = [
    {"id": "case-001-demo", "case_number": "CR-1024", "title": "Operation Cross-Link — Communication Network Analysis", "jurisdiction_id": "DEMO-JURISDICTION", "status": CaseStatus.OPEN, "classification": InformationClassification.CONFIDENTIAL},
    {"id": "case-002-demo", "case_number": "CR-1025", "title": "Financial Trail — Shared Resources Investigation", "jurisdiction_id": "DEMO-JURISDICTION", "status": CaseStatus.OPEN, "classification": InformationClassification.CONFIDENTIAL},
    {"id": "case-003-demo", "case_number": "CR-1026", "title": "Co-Location Pattern — Surveillance Evidence", "jurisdiction_id": "DEMO-JURISDICTION", "status": CaseStatus.OPEN, "classification": InformationClassification.INTERNAL},
]

DEMO_PEOPLE = [
    {"id": "PERSON-001", "name": "Person A", "label": "Person", "properties": {"role": "Subject"}},
    {"id": "PERSON-002", "name": "Person B", "label": "Person", "properties": {"role": "Associate"}},
    {"id": "PERSON-003", "name": "Person C", "label": "Person", "properties": {"role": "Contact"}},
    {"id": "PERSON-004", "name": "Person D", "label": "Person", "properties": {"role": "Witness"}},
]

DEMO_RELATIONSHIPS = [
    {"source": "PERSON-001", "target": "PERSON-002", "rel_type": "CALLED", "confidence": 0.92, "evidence": "E-042", "evidence_list": ["E-042", "E-103"], "display_type": "COMMUNICATION"},
    {"source": "PERSON-001", "target": "PERSON-003", "rel_type": "PARTICIPATED_IN", "confidence": 0.85, "evidence": "E-071", "evidence_list": ["E-071"], "display_type": "CO_LOCATION"},
    {"source": "PERSON-002", "target": "PERSON-004", "rel_type": "PARTICIPATED_IN", "confidence": 0.78, "evidence": "E-118", "evidence_list": ["E-118"], "display_type": "SHARED_EVENT"},
    {"source": "PERSON-002", "target": "PERSON-003", "rel_type": "ASSOCIATE_OF", "confidence": 0.71, "evidence": "E-103", "evidence_list": ["E-103"], "display_type": "COMMON_CONTACT"},
]

DEMO_EVIDENCE = [
    {"id": "evidence-042-demo", "evidence_id": "E-042", "case_id": "case-001-demo", "filename": "E-042_communication_record.pdf", "storage_key": "evidence/E-042/original.pdf", "mime_type": "application/pdf", "document_type": DocumentType.CDR, "content": b"%PDF-1.4 Demo Evidence E-042 Communication Record\nPerson A <-> Person B\nTimestamp: 2026-02-14 14:32\nDuration: 127s\nSource: CDR Database\nProvenance: Verified\n"},
    {"id": "evidence-103-demo", "evidence_id": "E-103", "case_id": "case-001-demo", "filename": "E-103_call_log.pdf", "storage_key": "evidence/E-103/original.pdf", "mime_type": "application/pdf", "document_type": DocumentType.CDR, "content": b"%PDF-1.4 Demo Evidence E-103 Call Log\nPerson A -> Person C -> Person B\nMulti-hop connection via common contact\nTimestamp: 2026-02-15 09:15\n"},
    {"id": "evidence-071-demo", "evidence_id": "E-071", "case_id": "case-002-demo", "filename": "E-071_location_record.pdf", "storage_key": "evidence/E-071/original.pdf", "mime_type": "application/pdf", "document_type": DocumentType.SCENE_REPORT, "content": b"%PDF-1.4 Demo Evidence E-071 Location Record\nPerson A and Person C co-located at Location X\nTimestamp: 2026-02-14 14:00\nContradictory: Person B at Location Y 14:05\n"},
    {"id": "evidence-118-demo", "evidence_id": "E-118", "case_id": "case-003-demo", "filename": "E-118_surveillance_log.pdf", "storage_key": "evidence/E-118/original.pdf", "mime_type": "application/pdf", "document_type": DocumentType.SURVEILLANCE_REPORT, "content": b"%PDF-1.4 Demo Evidence E-118 Surveillance Log\nPerson B and Person D shared event\nLocation: Warehouse District\nTimestamp: 2026-02-16 22:45\n"},
    {"id": "source-001-demo", "evidence_id": "S-001", "case_id": "case-001-demo", "filename": "S-001_source_document.pdf", "storage_key": "sources/S-001/source-document.pdf", "mime_type": "application/pdf", "document_type": DocumentType.FIR, "content": b"%PDF-1.4 Demo Source S-001\nFIR No. 231/2024\nComplainant statement\nOriginal source document for E-042\n"},
]

def seed_postgres():
    print("Seeding PostgreSQL...")
    session_maker = get_sync_sessionmaker()
    session = session_maker()
    try:
        for user_data in DEMO_USERS:
            existing = session.query(User).filter(User.badge_number == user_data["badge_number"]).one_or_none()
            if existing:
                print(f"  User {user_data['badge_number']} already exists — skipping")
                continue
            user = User(id=user_data["id"], badge_number=user_data["badge_number"], full_name=user_data["full_name"], hashed_password=hash_password(user_data["password"]), role=user_data["role"], station_id=user_data["station_id"], jurisdiction_id=user_data["jurisdiction_id"], is_active=True)
            session.add(user)
            print(f"  Created user {user_data['badge_number']} ({user_data['role'].value})")
        session.flush()
        for case_data in DEMO_CASES:
            existing = session.query(Case).filter(Case.id == case_data["id"]).one_or_none()
            if existing:
                print(f"  Case {case_data['case_number']} already exists — skipping")
                continue
            case = Case(id=case_data["id"], case_number=case_data["case_number"], title=case_data["title"], jurisdiction_id=case_data["jurisdiction_id"], status=case_data["status"], classification=case_data["classification"], created_by=DEMO_USERS[0]["id"])
            session.add(case)
            print(f"  Created case {case_data['case_number']}")
        session.flush()
        for ev_data in DEMO_EVIDENCE:
            existing = session.query(CaseDocument).filter(CaseDocument.id == ev_data["id"]).one_or_none()
            if existing:
                print(f"  Evidence {ev_data['evidence_id']} already exists — skipping")
                continue
            doc = CaseDocument(id=ev_data["id"], case_id=ev_data["case_id"], document_type=ev_data["document_type"], filename=ev_data["filename"], storage_key=ev_data["storage_key"], content_hash=hashlib.sha256(ev_data["content"]).hexdigest(), size_bytes=len(ev_data["content"]), mime_type=ev_data["mime_type"], ingestion_status=IngestionStatus.COMPLETE, ingestion_stage=6, source_confidence=SourceConfidence.VERIFIED, classification=InformationClassification.CONFIDENTIAL, uploaded_by=DEMO_USERS[0]["id"])
            session.add(doc)
            print(f"  Created evidence metadata {ev_data['evidence_id']} -> {ev_data['storage_key']}")
        session.commit()
        print("PostgreSQL seeding complete")
        user_count = session.query(User).filter(User.badge_number.like("DEMO-%")).count()
        case_count = session.query(Case).filter(Case.id.like("case-%-demo")).count()
        doc_count = session.query(CaseDocument).filter(CaseDocument.id.like("%-demo")).count()
        print(f"Verification: {user_count} demo users, {case_count} demo cases, {doc_count} demo evidence")
        return True
    except Exception as e:
        print(f"PostgreSQL seeding failed: {e}")
        import traceback; traceback.print_exc()
        session.rollback()
        return False
    finally:
        session.close()

def seed_minio():
    print("\nSeeding MinIO...")
    try:
        from app.adapters.objectstore.minio_store import MinioObjectStore
        from app.adapters.objectstore.local import LocalObjectStore
        settings = get_settings()
        try:
            if settings.effective_object_store_backend == "minio":
                store = MinioObjectStore(settings)
                store.ensure_buckets()
                backend = "minio"
            else:
                store = LocalObjectStore(settings)
                backend = "local"
        except Exception as e:
            print(f"  MinIO not available ({e}), using local store")
            store = LocalObjectStore(settings)
            backend = "local"
        bucket = settings.minio_bucket_documents
        for ev_data in DEMO_EVIDENCE:
            key = ev_data["storage_key"]
            existing = store.stat(bucket, key)
            if existing:
                print(f"  Object {key} already exists — skipping")
                continue
            store.put(bucket, key, ev_data["content"], content_type=ev_data["mime_type"])
            print(f"  Created object {key} in {backend} ({len(ev_data['content'])} bytes)")
        print(f"MinIO seeding complete ({backend})")
        return True
    except Exception as e:
        print(f"MinIO seeding failed: {e}")
        import traceback; traceback.print_exc()
        return False

def seed_neo4j():
    print("\nSeeding Neo4j...")
    try:
        settings = get_settings()
        backend = settings.effective_graph_backend
        if backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            from app.domain.models import GraphNode, GraphEdge
            store = EmbeddedGraphStore(settings)
            existing_nodes = list(store._graph.nodes(data=True))
            existing_ids = {pk for pk, _ in existing_nodes}
            nodes_to_add = []
            for person in DEMO_PEOPLE:
                if person["id"] in existing_ids:
                    print(f"  Person {person['id']} already exists — skipping")
                    continue
                nodes_to_add.append(GraphNode(
                    provenance_key=person["id"],
                    label=person["label"],
                    properties={"name": person["name"], **person["properties"], "case_id": "case-001-demo", "source_doc_id": "E-042", "source_doc_ids": ["E-042"], "confidence": 0.95}
                ))
            if nodes_to_add:
                store.upsert_nodes(nodes_to_add)
                print(f"  Added {len(nodes_to_add)} people to embedded graph")
            else:
                print("  All demo people already exist — skipping")
            edges_to_add = []
            for rel in DEMO_RELATIONSHIPS:
                exists = False
                for u, v, k, data in store._graph.edges(keys=True, data=True):
                    if u == rel["source"] and v == rel["target"] and data.get("_rel") == rel["rel_type"]:
                        exists = True
                        break
                if exists:
                    print(f"  Relationship {rel['source']} -{rel['rel_type']}-> {rel['target']} already exists — skipping")
                    continue
                edges_to_add.append(GraphEdge(
                    source_key=rel["source"],
                    target_key=rel["target"],
                    rel_type=rel["rel_type"],
                    key=f"{rel['source']}-{rel['rel_type']}-{rel['target']}",
                    properties={"confidence": rel["confidence"], "source_doc_id": rel["evidence"], "source_doc_ids": rel["evidence_list"], "case_id": "case-001-demo", "display_type": rel["display_type"]}
                ))
            if edges_to_add:
                store.upsert_edges(edges_to_add)
                print(f"  Added {len(edges_to_add)} relationships to embedded graph")
            print("Neo4j (embedded) seeding complete")
            return True
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            with driver.session(database=settings.neo4j_database) as neo_session:
                for person in DEMO_PEOPLE:
                    neo_session.run("MERGE (p:Entity {provenance_key: $id}) SET p.label = $label, p.name = $name, p.properties = $props, p.confidence = 0.95", id=person["id"], label=person["label"], name=person["name"], props=person["properties"])
                    print(f"  Merged person {person['id']}")
                for rel in DEMO_RELATIONSHIPS:
                    neo_session.run("MATCH (a:Entity {provenance_key: $source}), (b:Entity {provenance_key: $target}) MERGE (a)-[r:RELATIONSHIP {rel_type: $rel_type}]->(b) SET r.confidence = $confidence, r.source_doc_ids = $evidence, r.display_type = $display, r.source_doc_id = $single", source=rel["source"], target=rel["target"], rel_type=rel["rel_type"], confidence=rel["confidence"], evidence=rel["evidence_list"], display=rel["display_type"], single=rel["evidence"])
                    print(f"  Merged relationship {rel['source']} -{rel['rel_type']}({rel['display_type']})-> {rel['target']}")
            driver.close()
            print("Neo4j seeding complete")
            return True
    except Exception as e:
        print(f"Neo4j seeding failed: {e}")
        import traceback; traceback.print_exc()
        return False

def main():
    print("="*60)
    print("CrimeLink Production Demo Seed — Idempotent")
    print("="*60)
    pg_ok = seed_postgres()
    minio_ok = seed_minio()
    neo4j_ok = seed_neo4j()
    print("\n" + "="*60)
    print(f"Seed results: PostgreSQL={pg_ok}, MinIO={minio_ok}, Neo4j={neo4j_ok}")
    if pg_ok and minio_ok and neo4j_ok:
        print("All demo data seeded successfully — evaluator-ready")
        return 0
    else:
        print("Some seeding failed — check logs")
        return 1

if __name__ == "__main__":
    sys.exit(main())
