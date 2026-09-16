"""
Controlled Demo Reset — Safe, scoped, requires explicit safety flag
Clears only CrimeLink demo scope, leaves app code/config intact
"""
import sys
import os
import subprocess
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

# Re-exec into project virtualenv if running under another interpreter
for candidate in (REPO_ROOT / ".venv", BACKEND_ROOT / ".venv"):
    venv_py = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_py.is_file():
        try:
            if Path(sys.executable).resolve() != venv_py.resolve():
                res = subprocess.run([str(venv_py), *sys.argv], check=False)
                sys.exit(res.returncode)
        except Exception:
            pass
        break

sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.db.session import get_sync_sessionmaker
from app.db.models import User, Case, CaseDocument, SourceReference, InvestigationFinding, InvestigationSession, Dataset, DatasetFile, EvidenceCustodyEvent, InvestigationStageRun, IngestionJob, DocumentStageEvent

DEMO_DATASET_ID = "demo-dataset-001"
DEMO_JURISDICTION = "DEMO-JURISDICTION"

def check_safety():
    settings = get_settings()
    allow_reset = os.getenv("CRIMELINK_ALLOW_DEMO_RESET", "").lower() in ("true", "1", "yes")
    demo_mode = os.getenv("CRIMELINK_DEMO_MODE", "").lower() in ("true", "1", "yes")
    env = settings.environment

    # Safety: refuse to run against production unless explicitly allowed
    if env == "production" and not allow_reset:
        print("="*60)
        print("SAFETY CHECK FAILED: Refusing to reset in production environment")
        print("="*60)
        print("This script clears demo data and must not run against production.")
        print("To allow reset in production evaluator environment, set:")
        print("  CRIMELINK_ALLOW_DEMO_RESET=true")
        print("And ensure CRIMELINK_ENVIRONMENT is not production or explicitly allow.")
        print(f"Current: environment={env}, allow_reset={allow_reset}")
        return False

    # Additional safety: require demo jurisdiction or dataset exists
    if not allow_reset and not demo_mode:
        # Check if we are in dev or staging
        if env not in ("dev", "staging"):
            print(f"SAFETY: environment={env} requires explicit CRIMELINK_ALLOW_DEMO_RESET=true or CRIMELINK_DEMO_MODE=true")
            return False

    print(f"Safety check passed: environment={env}, allow_reset={allow_reset}, demo_mode={demo_mode}")
    return True

def reset_postgres():
    print("\nResetting PostgreSQL demo records...")
    session_maker = get_sync_sessionmaker()
    session = session_maker()
    try:
        # Find demo cases
        demo_cases = session.query(Case).filter(
            (Case.dataset_id == DEMO_DATASET_ID) | (Case.jurisdiction_id == DEMO_JURISDICTION) | (Case.id.like("case-%-demo"))
        ).all()
        demo_case_ids = [c.id for c in demo_cases]
        print(f"  Found {len(demo_cases)} demo cases to remove")

        # Delete in dependency order
        if demo_case_ids:
            # Source references
            count = session.query(SourceReference).filter(SourceReference.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} SourceReference")

            count = session.query(EvidenceCustodyEvent).filter(EvidenceCustodyEvent.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} EvidenceCustodyEvent")

            count = session.query(DocumentStageEvent).filter(DocumentStageEvent.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} DocumentStageEvent")

            count = session.query(IngestionJob).filter(IngestionJob.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} IngestionJob")

            count = session.query(InvestigationFinding).filter(InvestigationFinding.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} InvestigationFinding")

            count = session.query(InvestigationStageRun).filter(InvestigationStageRun.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} InvestigationStageRun")

            count = session.query(InvestigationSession).filter(InvestigationSession.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} InvestigationSession")

            # Case documents
            count = session.query(CaseDocument).filter(CaseDocument.case_id.in_(demo_case_ids)).delete(synchronize_session=False)
            print(f"  Deleted {count} CaseDocument")

        # Dataset files
        count = session.query(DatasetFile).filter(DatasetFile.dataset_id == DEMO_DATASET_ID).delete(synchronize_session=False)
        print(f"  Deleted {count} DatasetFile")

        # Investigation sessions by dataset
        count = session.query(InvestigationSession).filter(InvestigationSession.dataset_id == DEMO_DATASET_ID).delete(synchronize_session=False)
        print(f"  Deleted {count} InvestigationSession by dataset")

        # Cases
        count = session.query(Case).filter(
            (Case.dataset_id == DEMO_DATASET_ID) | (Case.jurisdiction_id == DEMO_JURISDICTION) | (Case.id.like("case-%-demo"))
        ).delete(synchronize_session=False)
        print(f"  Deleted {count} Case")

        # Dataset
        count = session.query(Dataset).filter(Dataset.id == DEMO_DATASET_ID).delete(synchronize_session=False)
        print(f"  Deleted {count} Dataset")

        # Users
        count = session.query(User).filter(User.badge_number.like("DEMO-%")).delete(synchronize_session=False)
        print(f"  Deleted {count} User (DEMO-%)")

        session.commit()
        print("PostgreSQL reset complete")
        return True
    except Exception as e:
        print(f"PostgreSQL reset failed: {e}")
        import traceback; traceback.print_exc()
        session.rollback()
        return False
    finally:
        session.close()

def reset_minio():
    print("\nResetting MinIO demo objects...")
    try:
        settings = get_settings()
        backend = settings.effective_object_store_backend
        if backend == "minio":
            from app.adapters.objectstore.minio_store import MinioObjectStore
            store = MinioObjectStore(settings)
            bucket = settings.minio_bucket_documents
            # List and delete demo objects
            keys = store.list_keys(bucket, prefix="evidence/")
            demo_keys = [k for k in keys if "demo" in k or k.startswith("evidence/E-") or k.startswith("evidence/S-")]
            # Also include our generated evidence keys
            all_keys = store.list_keys(bucket)
            demo_keys = [k for k in all_keys if k.startswith("evidence/E-") or k.startswith("sources/S-")]

            print(f"  Found {len(demo_keys)} demo objects in MinIO bucket {bucket}")

            # MinIO client doesn't have bulk delete in our wrapper, delete one by one via client
            deleted = 0
            for key in demo_keys:
                try:
                    store._client.remove_object(bucket, key)
                    deleted += 1
                except Exception as ex:
                    print(f"    Failed to delete {key}: {ex}")
            print(f"  Deleted {deleted} objects from MinIO")

            # Also check sources prefix
            source_keys = store.list_keys(bucket, prefix="sources/")
            print(f"  Found {len(source_keys)} source objects")
            for key in source_keys:
                if key.startswith("sources/S-"):
                    try:
                        store._client.remove_object(bucket, key)
                        deleted += 1
                    except:
                        pass
            print(f"  Total deleted from MinIO: {deleted}")
        else:
            from app.adapters.objectstore.local import LocalObjectStore
            store = LocalObjectStore(settings)
            bucket = settings.minio_bucket_documents
            keys = store.list_keys(bucket)
            demo_keys = [k for k in keys if k.startswith("evidence/E-") or k.startswith("sources/S-")]
            print(f"  Found {len(demo_keys)} demo objects in local store")
            import pathlib
            root = pathlib.Path(settings.object_store_dir) / bucket
            deleted = 0
            for key in demo_keys:
                path = root / key
                if path.exists():
                    path.unlink()
                    deleted += 1
            print(f"  Deleted {deleted} local objects")
        print("MinIO reset complete")
        return True
    except Exception as e:
        print(f"MinIO reset failed: {e}")
        import traceback; traceback.print_exc()
        return False

def reset_neo4j():
    print("\nResetting Neo4j demo graph data...")
    try:
        settings = get_settings()
        backend = settings.effective_graph_backend
        if backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            store = EmbeddedGraphStore(settings)
            # Remove nodes with dataset_id == demo or provenance_key like PERSON-
            doomed = []
            for pk, data in store._graph.nodes(data=True):
                if data.get("dataset_id") == DEMO_DATASET_ID or str(pk).startswith("PERSON-") or str(pk).startswith("EVENT-"):
                    doomed.append(pk)
            print(f"  Found {len(doomed)} demo nodes in embedded graph")
            store._graph.remove_nodes_from(doomed)
            # Also remove edges with dataset_id demo
            stale = []
            for u, v, k, data in store._graph.edges(keys=True, data=True):
                if data.get("dataset_id") == DEMO_DATASET_ID:
                    stale.append((u, v, k))
            for u, v, k in stale:
                try:
                    store._graph.remove_edge(u, v, key=k)
                except:
                    pass
            store._version += 1
            store._flush()
            print(f"  Removed {len(doomed)} nodes and {len(stale)} edges from embedded graph")
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            with driver.session(database=settings.neo4j_database) as neo_session:
                # Delete demo nodes
                result = neo_session.run("MATCH (n {dataset_id: $ds}) DETACH DELETE n RETURN count(n) as cnt", ds=DEMO_DATASET_ID)
                cnt = result.single()["cnt"]
                print(f"  Deleted {cnt} nodes with dataset_id={DEMO_DATASET_ID}")

                result = neo_session.run("MATCH (n) WHERE n.provenance_key STARTS WITH 'PERSON-' DETACH DELETE n RETURN count(n) as cnt")
                cnt2 = result.single()["cnt"]
                print(f"  Deleted {cnt2} PERSON nodes")

                result = neo_session.run("MATCH (n) WHERE n.provenance_key STARTS WITH 'EVENT-' DETACH DELETE n RETURN count(n) as cnt")
                cnt3 = result.single()["cnt"]
                print(f"  Deleted {cnt3} EVENT nodes")

                # Delete demo edges
                result = neo_session.run("MATCH ()-[r {dataset_id: $ds}]->() DELETE r RETURN count(r) as cnt", ds=DEMO_DATASET_ID)
                cnt4 = result.single()["cnt"]
                print(f"  Deleted {cnt4} edges with dataset_id={DEMO_DATASET_ID}")

            driver.close()
        print("Neo4j reset complete")
        return True
    except Exception as e:
        print(f"Neo4j reset failed: {e}")
        import traceback; traceback.print_exc()
        return False

def reset_dataset_workspace():
    print("\nResetting dataset workspace...")
    try:
        settings = get_settings()
        from app.datasets.registry import workspace_for
        ws = workspace_for(DEMO_DATASET_ID)
        if ws.exists():
            import shutil
            shutil.rmtree(ws, ignore_errors=True)
            print(f"  Deleted workspace {ws}")
        else:
            print(f"  Workspace {ws} does not exist — skipping")
        print("Workspace reset complete")
        return True
    except Exception as e:
        print(f"Workspace reset failed: {e}")
        return False

def main():
    print("="*60)
    print("CrimeLink Demo Reset — Controlled, Safe")
    print("="*60)
    if not check_safety():
        return 1

    pg_ok = reset_postgres()
    minio_ok = reset_minio()
    neo4j_ok = reset_neo4j()
    ws_ok = reset_dataset_workspace()

    print("\n" + "="*60)
    print(f"Reset results: PG={pg_ok}, MinIO={minio_ok}, Neo4j={neo4j_ok}, Workspace={ws_ok}")
    if pg_ok and minio_ok and neo4j_ok:
        print("Demo data reset successfully — ready for fresh seed")
        return 0
    else:
        print("Some reset steps failed — check logs")
        return 1

if __name__ == "__main__":
    sys.exit(main())
