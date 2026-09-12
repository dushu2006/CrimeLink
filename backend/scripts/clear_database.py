"""Clear operational data from CrimeLink database, retaining only user accounts."""

import json
import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "var" / "data"
OBJECT_DIR = REPO_ROOT / "var" / "objects"
DB_PATH = DATA_DIR / "crimelink.db"
GRAPH_PATH = DATA_DIR / "graph.json"

TABLES_TO_CLEAR = [
    "dataset_jobs",
    "dataset_relationships",
    "dataset_entities",
    "dataset_files",
    "datasets",
    "investigation_findings",
    "investigation_stage_runs",
    "detected_patterns",
    "pattern_config",
    "investigation_sessions",
    "jurisdiction_access_requests",
    "entity_resolution_queue",
    "quarantined_records",
    "source_references",
    "document_stage_events",
    "ingestion_jobs",
    "case_documents",
    "cases",
    "audit_anchors",
    "audit_chain_head",
    "audit_logs",
    "refresh_tokens",
]


def clear_database() -> None:
    if not DB_PATH.exists():
        print(f"Database {DB_PATH} does not exist!")
        return

    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Verify users exist
    cursor.execute("SELECT count(*) FROM users")
    user_count = cursor.fetchone()[0]
    print(f"Current user count: {user_count}")
    if user_count == 0:
        print("WARNING: users table is empty! Aborting to avoid corrupting empty DB.")
        conn.close()
        return

    # Disable foreign keys during cleanup
    cursor.execute("PRAGMA foreign_keys = OFF;")

    for table in TABLES_TO_CLEAR:
        cursor.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
        if cursor.fetchone()[0] > 0:
            cursor.execute(f'DELETE FROM "{table}"')
            print(f"Cleared table: {table}")

    conn.commit()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("VACUUM;")
    conn.commit()

    print("\n--- Verification of tables ---")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    all_tables = [r[0] for r in cursor.fetchall()]
    for t in all_tables:
        cursor.execute(f'SELECT count(*) FROM "{t}"')
        count = cursor.fetchone()[0]
        status = "KEPT (User Data)" if t == "users" else ("EMPTY" if count == 0 else f"NON-EMPTY ({count})")
        print(f"  {t}: {count} -> {status}")

    conn.close()

    # Reset graph store
    print(f"\nResetting graph store at {GRAPH_PATH}...")
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps({"nodes": [], "edges": []}, indent=2), encoding="utf-8")
    print("Graph store reset to empty MultiDiGraph.")

    # Clean storage directories
    cleanup_dirs = [
        DATA_DIR / "datasets",
        DATA_DIR / "uploads",
        OBJECT_DIR / "documents",
        OBJECT_DIR / "documents-derived",
    ]
    for d in cleanup_dirs:
        if d.is_dir():
            for child in d.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                except Exception as exc:
                    print(f"  Warning removing {child}: {exc}")
            print(f"Emptied directory: {d}")


if __name__ == "__main__":
    clear_database()
