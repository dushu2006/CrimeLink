"""
Demo Validation — Health Check
"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

def check_postgres():
    print("Checking PostgreSQL...")
    from app.db.session import get_sync_sessionmaker
    from app.db.models import User, Case, CaseDocument
    session = get_sync_sessionmaker()()
    try:
        demo_users = session.query(User).filter(User.badge_number.like("DEMO-%")).all()
        print(f"  Demo users: {len(demo_users)} found")
        for u in demo_users:
            print(f"    - {u.badge_number} ({u.role.value})")
        demo_cases = session.query(Case).filter(Case.id.like("case-%-demo")).all()
        print(f"  Demo cases: {len(demo_cases)} found")
        for c in demo_cases:
            print(f"    - {c.case_number}: {c.title}")
        demo_docs = session.query(CaseDocument).filter(CaseDocument.id.like("%-demo")).all()
        print(f"  Demo evidence: {len(demo_docs)} found")
        for d in demo_docs:
            print(f"    - {d.id} -> {d.storage_key} ({d.size_bytes} bytes)")
        if len(demo_users) < 3 or len(demo_cases) < 1 or len(demo_docs) < 1:
            print("  FAILED: Missing demo data in PostgreSQL")
            return False
        print("  PostgreSQL OK")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback; traceback.print_exc()
        return False
    finally:
        session.close()

def check_minio():
    print("\nChecking MinIO...")
    try:
        from app.config import get_settings
        from app.adapters.objectstore.minio_store import MinioObjectStore
        from app.adapters.objectstore.local import LocalObjectStore
        settings = get_settings()
        try:
            if settings.effective_object_store_backend == "minio":
                store = MinioObjectStore(settings)
                backend = "minio"
            else:
                store = LocalObjectStore(settings)
                backend = "local"
        except Exception:
            store = LocalObjectStore(settings)
            backend = "local"
        bucket = settings.minio_bucket_documents
        keys_to_check = ["evidence/E-042/original.pdf", "evidence/E-103/original.pdf", "evidence/E-071/original.pdf", "evidence/E-118/original.pdf", "sources/S-001/source-document.pdf"]
        all_ok = True
        for key in keys_to_check:
            exists = store.exists(bucket, key)
            if exists:
                data = store.get(bucket, key)
                print(f"  {key}: OK ({len(data)} bytes, {backend})")
            else:
                print(f"  {key}: MISSING in {backend}")
                all_ok = False
        if all_ok:
            print(f"  MinIO ({backend}) OK")
        else:
            print(f"  MinIO ({backend}) FAILED")
        return all_ok
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback; traceback.print_exc()
        return False

def check_neo4j():
    print("\nChecking Neo4j...")
    try:
        from app.config import get_settings
        settings = get_settings()
        backend = settings.effective_graph_backend
        if backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            store = EmbeddedGraphStore(settings)
            nodes = list(store._graph.nodes(data=True))
            edges = list(store._graph.edges(data=True, keys=True))
            print(f"  Embedded graph: {len(nodes)} nodes, {len(edges)} edges")
            demo_nodes = [pk for pk, _ in nodes if str(pk).startswith("PERSON-")]
            print(f"  Demo people: {len(demo_nodes)} found")
            for pk in demo_nodes:
                print(f"    - {pk}")
            if len(demo_nodes) >= 2:
                print("  Neo4j (embedded) OK")
                return True
            else:
                print("  FAILED: Not enough demo people")
                return False
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            with driver.session(database=settings.neo4j_database) as neo_session:
                result = neo_session.run("MATCH (p:Entity) WHERE p.provenance_key STARTS WITH 'PERSON-' RETURN count(p) as cnt")
                cnt = result.single()["cnt"]
                print(f"  Neo4j people: {cnt} found")
                result2 = neo_session.run("MATCH (a:Entity)-[r:RELATIONSHIP]->(b:Entity) RETURN count(r) as cnt")
                rcnt = result2.single()["cnt"]
                print(f"  Neo4j relationships: {rcnt} found")
                if cnt >= 2 and rcnt >= 1:
                    print("  Neo4j OK")
                    driver.close()
                    return True
                else:
                    print("  FAILED: Not enough demo data in Neo4j")
                    driver.close()
                    return False
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback; traceback.print_exc()
        return False

def main():
    print("="*60)
    print("CrimeLink Demo Validation — Health Check")
    print("="*60)
    pg_ok = check_postgres()
    minio_ok = check_minio()
    neo4j_ok = check_neo4j()
    print("\n" + "="*60)
    print(f"Results: PostgreSQL={pg_ok}, MinIO={minio_ok}, Neo4j={neo4j_ok}")
    if pg_ok and minio_ok and neo4j_ok:
        print("Validation PASSED — evaluator-ready")
        print("\nDemo Data Scope:")
        print("  Cases: CR-1024, CR-1025, CR-1026 (same for all roles)")
        print("  People: PERSON-001..004")
        print("  Evidence: E-042, E-103, E-071, E-118, S-001")
        print("  Investigations: INV-0042 (Viewer read-only)")
        print("\nDemo Access:")
        print("  Admin: DEMO-ADMIN / DemoAdmin@2026 — all cases/people/relationships/evidence/source/timelines/investigations/patterns/attention/audit/users")
        print("  Investigator: DEMO-INVESTIGATOR / DemoInvestigator@2026 — Cases/Overview/Search/People/Relationships/Evidence/Timeline/Investigate/Patterns/Attention/Audit")
        print("  Viewer: DEMO-VIEWER / DemoViewer@2026 — read-only + Investigator Activity (INV-0042, finding, evidence E-042/E-103/E-118, HIGH, FACT/INFERENCE, completed 15 Sep 2026)")
        print("\nJudge Test (2-3 min):")
        print("  Test A Admin login→cases exist/files/admin works")
        print("  Test B Investigator 30s open CR-1024 identify people/relationship/evidence/source/timeline/initiate investigation")
        print("  Test C Viewer 30s open CR-1024 people/relationship/evidence/source/timeline/Investigator Activity confirm no investigation controls then attempt direct Investigator route/API must be denied")
        return 0
    else:
        print("Validation FAILED — check logs")
        return 1

if __name__ == "__main__":
    sys.exit(main())
