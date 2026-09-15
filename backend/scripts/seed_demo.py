"""
Production Demo Seed — Idempotent, persistent, reproducible
Creates 20 cases, 100 people, 300 evidence, 120 sources, 216 relationships, timeline, investigations
Real files: valid PDFs and CSVs, SHA-256 verified, MinIO-backed
"""
import hashlib
import sys
import json
import os
from pathlib import Path
from datetime import datetime

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Demo dataset roots (repo root and backend copy)
DEMO_ROOTS = [
    REPO_ROOT / "demo_dataset",
    BACKEND_ROOT / "demo_dataset",
]

def find_demo_root():
    for p in DEMO_ROOTS:
        if (p / "manifest.json").exists():
            return p
    # Fallback: try to generate if missing
    return DEMO_ROOTS[0]

DEMO_ROOT = find_demo_root()

from app.config import get_settings
from app.db.models import (
    User, Case, CaseDocument, SourceReference, InvestigationFinding,
    InvestigationSession, Dataset, DatasetFile, EvidenceCustodyEvent
)
from app.db.session import get_sync_sessionmaker
from app.domain.enums import Role, CaseStatus, InformationClassification, DocumentType, SourceConfidence, IngestionStatus
from app.security.passwords import hash_password

DEMO_USERS = [
    {"id": "demo-admin-0001", "badge_number": "DEMO-ADMIN", "full_name": "Demo Admin", "password": "DemoAdmin@2026", "role": Role.ADMIN, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
    {"id": "demo-investigator-0001", "badge_number": "DEMO-INVESTIGATOR", "full_name": "Demo Investigator", "password": "DemoInvestigator@2026", "role": Role.INVESTIGATOR, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
    {"id": "demo-viewer-0001", "badge_number": "DEMO-VIEWER", "full_name": "Demo Viewer", "password": "DemoViewer@2026", "role": Role.VIEWER, "jurisdiction_id": "DEMO-JURISDICTION", "station_id": "DEMO-STATION"},
]

DEMO_DATASET_ID = "demo-dataset-001"

def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, 'r') as f:
        return json.load(f)

def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def get_file_bytes(storage_key: str) -> bytes:
    """Find file in demo_dataset roots and return bytes."""
    for root in DEMO_ROOTS:
        fp = root / storage_key
        if fp.exists():
            return fp.read_bytes()
    raise FileNotFoundError(f"Demo file not found for key {storage_key} in {DEMO_ROOTS}")

def ensure_demo_dataset_files():
    """Ensure demo dataset exists, generate if needed."""
    manifest_path = DEMO_ROOT / "manifest.json"
    if not manifest_path.exists():
        print("Demo dataset not found, generating...")
        sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
        try:
            import generate_demo_dataset
        except Exception as e:
            print(f"Failed to generate demo dataset: {e}")
            raise
    return DEMO_ROOT

def seed_postgres():
    print("Seeding PostgreSQL...")
    settings = get_settings()
    settings.ensure_directories()
    # Ensure all tables exist (for embedded SQLite where alembic may not have created datasets tables)
    try:
        from app.db.models import Base
        from app.db.session import get_sync_engine
        engine = get_sync_engine(settings)
        Base.metadata.create_all(bind=engine)
        print("  Ensured tables exist via create_all")
    except Exception as e:
        print(f"  Warning: create_all failed: {e}")
    session_maker = get_sync_sessionmaker()
    session = session_maker()
    try:
        # Ensure demo dataset entry exists for source viewer compatibility
        dataset = session.query(Dataset).filter(Dataset.id == DEMO_DATASET_ID).one_or_none()
        if not dataset:
            print(f"  Creating dataset {DEMO_DATASET_ID}")
            # Ensure data_dir/datasets/demo-dataset-001 exists and contains files for source viewer
            from app.datasets.registry import workspace_for
            ws = workspace_for(DEMO_DATASET_ID)
            ws.mkdir(parents=True, exist_ok=True)
            # Copy demo files to workspace for filesystem fallback (source viewer)
            demo_root = ensure_demo_dataset_files()
            # Copy evidence and sources
            import shutil
            for sub in ["evidence", "sources"]:
                src_dir = demo_root / sub
                if src_dir.exists():
                    for file_path in src_dir.rglob("*"):
                        if file_path.is_file() and file_path.suffix in [".pdf", ".csv"]:
                            rel = file_path.relative_to(demo_root)
                            dest = ws / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            if not dest.exists():
                                shutil.copy2(file_path, dest)
            dataset = Dataset(
                id=DEMO_DATASET_ID,
                name="Production Demo Dataset v1",
                version="1.0",
                status="READY",
                is_active=True,
                source_kind="builtin",
                root_path=str(ws),
                origin_note="Production demo seed — persistent evaluator dataset",
                stage_detail={"stage": "READY", "steps": [{"stage": "READY", "detail": "Demo dataset ready"}]},
                stats={},
                created_by=DEMO_USERS[0]["id"]
            )
            session.add(dataset)
            session.flush()
            print(f"  Created dataset {DEMO_DATASET_ID} at {ws}")
        else:
            # Ensure active
            if not dataset.is_active:
                dataset.is_active = True
                session.flush()
            print(f"  Dataset {DEMO_DATASET_ID} already exists — ensuring active")
            # Update root_path if needed to point to workspace with files
            from app.datasets.registry import workspace_for
            ws = workspace_for(DEMO_DATASET_ID)
            if not Path(dataset.root_path).exists():
                dataset.root_path = str(ws)
                session.flush()

        # Deactivate other datasets? For demo, keep demo active, but don't purge hand-created
        # Ensure only demo is active for evaluator
        from app.db.models import Dataset as DS
        other_active = session.query(DS).filter(DS.is_active == True, DS.id != DEMO_DATASET_ID).all()
        for od in other_active:
            od.is_active = False
        if other_active:
            print(f"  Deactivated {len(other_active)} other datasets")
            session.flush()

        # Seed users
        for user_data in DEMO_USERS:
            existing = session.query(User).filter(User.badge_number == user_data["badge_number"]).one_or_none()
            if existing:
                # Check consistency
                if existing.id != user_data["id"]:
                    raise ValueError(f"User {user_data['badge_number']} exists with different id: {existing.id} vs {user_data['id']}")
                if existing.role != user_data["role"]:
                    print(f"  WARNING: User {user_data['badge_number']} role mismatch: {existing.role} vs {user_data['role']} — updating")
                    existing.role = user_data["role"]
                    session.flush()
                print(f"  User {user_data['badge_number']} already exists — skipping")
                continue
            user = User(
                id=user_data["id"],
                badge_number=user_data["badge_number"],
                full_name=user_data["full_name"],
                hashed_password=hash_password(user_data["password"]),
                role=user_data["role"],
                station_id=user_data["station_id"],
                jurisdiction_id=user_data["jurisdiction_id"],
                is_active=True
            )
            session.add(user)
            print(f"  Created user {user_data['badge_number']} ({user_data['role'].value})")
        session.flush()

        # Load cases from demo_dataset
        demo_root = ensure_demo_dataset_files()
        cases_manifest = load_json(demo_root / "manifest.json")
        if not cases_manifest:
            raise FileNotFoundError("manifest.json not found in demo_dataset")

        # Load all cases from cases/ directories
        cases_data = []
        cases_dir = demo_root / "cases"
        if cases_dir.exists():
            for case_sub in cases_dir.iterdir():
                if case_sub.is_dir():
                    cj = load_json(case_sub / "case.json")
                    if cj:
                        cases_data.append(cj)
        # Fallback to hardcoded if needed
        if not cases_data:
            # Use generator data
            from scripts.generate_demo_dataset import CASES as GEN_CASES
            cases_data = GEN_CASES

        # Seed cases
        for case_data in cases_data:
            case_id = case_data["id"]
            existing = session.query(Case).filter(Case.id == case_id).one_or_none()
            if existing:
                # Check consistency
                if existing.case_number != case_data["case_number"]:
                    raise ValueError(f"Case {case_id} exists with different case_number: {existing.case_number} vs {case_data['case_number']}")
                if existing.jurisdiction_id != case_data["jurisdiction_id"]:
                    raise ValueError(f"Case {case_id} jurisdiction mismatch")
                print(f"  Case {case_data['case_number']} already exists — skipping")
                continue
            # Map status
            status_map = {
                "OPEN": CaseStatus.OPEN,
                "ACTIVE_INVESTIGATION": CaseStatus.ACTIVE_INVESTIGATION,
                "CLOSED": CaseStatus.CLOSED,
                "DRAFT": CaseStatus.DRAFT,
            }
            status = status_map.get(case_data.get("status", "OPEN"), CaseStatus.OPEN)
            classification_map = {
                "CONFIDENTIAL": InformationClassification.CONFIDENTIAL,
                "INTERNAL": InformationClassification.INTERNAL,
                "RESTRICTED": InformationClassification.RESTRICTED,
                "SECRET": InformationClassification.SECRET,
            }
            classification = classification_map.get(case_data.get("classification", "CONFIDENTIAL"), InformationClassification.CONFIDENTIAL)
            case = Case(
                id=case_id,
                case_number=case_data["case_number"],
                title=case_data["title"],
                jurisdiction_id=case_data["jurisdiction_id"],
                dataset_id=DEMO_DATASET_ID,
                dataset_case_key=case_data.get("case_key") or case_data["case_number"],
                status=status,
                classification=classification,
                created_by=DEMO_USERS[0]["id"]
            )
            session.add(case)
            print(f"  Created case {case_data['case_number']}: {case_data['title'][:50]}")
        session.flush()

        # Load evidence and sources
        evidence_data = load_json(demo_root / "evidence" / "evidence.json") or []
        sources_data = load_json(demo_root / "sources" / "sources.json") or []

        # If not found, try to load from cases
        if not evidence_data:
            # Aggregate from case evidence.json
            for case_sub in (demo_root / "cases").iterdir():
                if case_sub.is_dir():
                    evj = load_json(case_sub / "evidence.json")
                    if evj:
                        evidence_data.extend(evj)

        # Deduplicate evidence by evidence_id
        seen_ev = set()
        deduped_ev = []
        for ev in evidence_data:
            eid = ev.get("evidence_id")
            if eid not in seen_ev:
                seen_ev.add(eid)
                deduped_ev.append(ev)
        evidence_data = deduped_ev

        # Seed evidence CaseDocuments
        for ev_data in evidence_data:
            ev_id = ev_data["evidence_id"]
            # Stable doc id
            doc_id = f"evidence-{ev_id.lower().replace(' ', '-')}-demo" if not ev_id.startswith("evidence-") else ev_id
            # For hero evidence, use existing stable ids
            if ev_id == "E-042":
                doc_id = "evidence-042-demo"
            elif ev_id == "E-103":
                doc_id = "evidence-103-demo"
            elif ev_id == "E-071":
                doc_id = "evidence-071-demo"
            elif ev_id == "E-118":
                doc_id = "evidence-118-demo"
            elif ev_id == "S-001":
                doc_id = "source-001-demo"
            else:
                # Use evidence id as basis
                doc_id = f"evidence-{ev_id.replace('E-', '').replace('S-', '').lower()}-demo" if ev_id.startswith("E-") or ev_id.startswith("S-") else f"evidence-{ev_id}-demo"
                # Ensure uniqueness
                if ev_id.startswith("S-"):
                    doc_id = f"source-{ev_id.replace('S-', '').lower()}-demo"

            # Get file bytes
            try:
                file_bytes = get_file_bytes(ev_data["storage_key"])
            except FileNotFoundError as e:
                print(f"  WARNING: File not found for {ev_id} at {ev_data['storage_key']}: {e}")
                # Generate minimal valid PDF as fallback for missing file (should not happen)
                file_bytes = b"%PDF-1.4 minimal fallback"

            content_hash = compute_sha256(file_bytes)
            size_bytes = len(file_bytes)

            existing = session.query(CaseDocument).filter(CaseDocument.id == doc_id).one_or_none()
            if existing:
                # Check consistency
                if existing.content_hash != content_hash:
                    raise ValueError(f"Evidence {ev_id} exists with different hash: {existing.content_hash} vs {content_hash} — fail loudly per idempotency rule")
                if existing.storage_key != ev_data["storage_key"]:
                    raise ValueError(f"Evidence {ev_id} storage_key mismatch")
                if existing.case_id != ev_data["case_id"]:
                    # Allow if case exists, but warn
                    print(f"  WARNING: Evidence {ev_id} case_id mismatch: {existing.case_id} vs {ev_data['case_id']}")
                print(f"  Evidence {ev_id} already exists — skipping")
                continue

            # Map document type
            doc_type_str = ev_data.get("document_type", "OTHER")
            try:
                doc_type = DocumentType(doc_type_str)
            except ValueError:
                doc_type = DocumentType.OTHER

            doc = CaseDocument(
                id=doc_id,
                case_id=ev_data["case_id"],
                dataset_id=DEMO_DATASET_ID,
                document_type=doc_type,
                filename=ev_data["filename"],
                storage_key=ev_data["storage_key"],
                content_hash=content_hash,
                size_bytes=size_bytes,
                mime_type=ev_data["mime_type"],
                ingestion_status=IngestionStatus.COMPLETE,
                ingestion_stage=6,
                source_confidence=SourceConfidence.VERIFIED,
                classification=InformationClassification.CONFIDENTIAL,
                uploaded_by=DEMO_USERS[0]["id"],
                source_metadata={
                    "evidence_id": ev_id,
                    "relative_path": ev_data["storage_key"],
                    "title": ev_data.get("title", ""),
                    "demo": True
                }
            )
            session.add(doc)
            print(f"  Created evidence {ev_id} -> {ev_data['storage_key']} ({size_bytes} bytes)")

            # Custody event
            custody = EvidenceCustodyEvent(
                evidence_id=doc_id,
                case_id=ev_data["case_id"],
                event_type="STORED",
                actor_id=DEMO_USERS[0]["id"],
                object_hash=content_hash,
                location=ev_data["storage_key"],
                details={"filename": ev_data["filename"], "demo": True}
            )
            session.add(custody)

        session.flush()

        # Seed sources as CaseDocuments too (if not already)
        for src_data in sources_data:
            src_id = src_data["source_id"]
            doc_id = f"source-{src_id.replace('S-', '').lower()}-demo"
            try:
                file_bytes = get_file_bytes(src_data["storage_key"])
            except FileNotFoundError:
                print(f"  WARNING: Source file not found for {src_id}")
                continue
            content_hash = compute_sha256(file_bytes)
            size_bytes = len(file_bytes)
            existing = session.query(CaseDocument).filter(CaseDocument.id == doc_id).one_or_none()
            if existing:
                if existing.content_hash != content_hash:
                    raise ValueError(f"Source {src_id} hash mismatch")
                print(f"  Source {src_id} already exists — skipping")
                continue
            try:
                doc_type = DocumentType(src_data.get("document_type", "OTHER"))
            except ValueError:
                doc_type = DocumentType.OTHER
            doc = CaseDocument(
                id=doc_id,
                case_id=src_data["case_id"],
                dataset_id=DEMO_DATASET_ID,
                document_type=doc_type,
                filename=src_data["filename"],
                storage_key=src_data["storage_key"],
                content_hash=content_hash,
                size_bytes=size_bytes,
                mime_type=src_data["mime_type"],
                ingestion_status=IngestionStatus.COMPLETE,
                ingestion_stage=6,
                source_confidence=SourceConfidence.VERIFIED,
                classification=InformationClassification.CONFIDENTIAL,
                uploaded_by=DEMO_USERS[0]["id"],
                source_metadata={
                    "source_id": src_id,
                    "relative_path": src_data["storage_key"],
                    "title": src_data.get("title", ""),
                    "demo": True
                }
            )
            session.add(doc)
            print(f"  Created source {src_id} -> {src_data['storage_key']}")

            custody = EvidenceCustodyEvent(
                evidence_id=doc_id,
                case_id=src_data["case_id"],
                event_type="STORED",
                actor_id=DEMO_USERS[0]["id"],
                object_hash=content_hash,
                location=src_data["storage_key"],
                details={"filename": src_data["filename"], "demo": True, "source": True}
            )
            session.add(custody)

        session.flush()

        # Seed DatasetFile entries for source viewer — deduplicate by relative_path
        all_files = evidence_data + sources_data
        seen_paths = set()
        deduped_files = []
        for f in all_files:
            sk = f["storage_key"]
            if sk not in seen_paths:
                seen_paths.add(sk)
                deduped_files.append(f)
        all_files = deduped_files
        for f_data in all_files:
            storage_key = f_data["storage_key"]
            # Find file to get size and hash
            try:
                file_bytes = get_file_bytes(storage_key)
            except FileNotFoundError:
                continue
            sha256 = compute_sha256(file_bytes)
            size = len(file_bytes)
            # Check existing
            existing_df = session.query(DatasetFile).filter(
                DatasetFile.dataset_id == DEMO_DATASET_ID,
                DatasetFile.relative_path == storage_key
            ).one_or_none()
            if existing_df:
                print(f"  DatasetFile {storage_key} already exists — skipping")
                continue
            # Determine doc_id
            ev_id = f_data.get("evidence_id") or f_data.get("source_id")
            if ev_id:
                if ev_id.startswith("E-"):
                    if ev_id == "E-042":
                        doc_id = "evidence-042-demo"
                    elif ev_id == "E-103":
                        doc_id = "evidence-103-demo"
                    elif ev_id == "E-071":
                        doc_id = "evidence-071-demo"
                    elif ev_id == "E-118":
                        doc_id = "evidence-118-demo"
                    else:
                        doc_id = f"evidence-{ev_id.replace('E-', '').lower()}-demo"
                else:
                    doc_id = f"source-{ev_id.replace('S-', '').lower()}-demo"
            else:
                doc_id = None

            ext = Path(storage_key).suffix.lower().lstrip('.')
            df = DatasetFile(
                dataset_id=DEMO_DATASET_ID,
                relative_path=storage_key,
                filename=f_data["filename"],
                extension=ext,
                media_type=f_data["mime_type"],
                file_kind="document" if ext == "pdf" else "table" if ext == "csv" else "unknown",
                size_bytes=size,
                sha256=sha256,
                semantic_type=f_data.get("document_type", "UNKNOWN"),
                classification_confidence=1.0,
                status="INGESTED",
                doc_id=doc_id
            )
            session.add(df)

        session.flush()

        # Seed SourceReference
        # For each evidence, create a source reference pointing to its storage_key
        for ev_data in evidence_data:
            ev_id = ev_data["evidence_id"]
            if ev_id.startswith("E-"):
                if ev_id == "E-042":
                    doc_id = "evidence-042-demo"
                elif ev_id == "E-103":
                    doc_id = "evidence-103-demo"
                elif ev_id == "E-071":
                    doc_id = "evidence-071-demo"
                elif ev_id == "E-118":
                    doc_id = "evidence-118-demo"
                else:
                    doc_id = f"evidence-{ev_id.replace('E-', '').lower()}-demo"
            else:
                continue
            # Check existing
            existing_ref = session.query(SourceReference).filter(
                SourceReference.doc_id == doc_id,
                SourceReference.origin_file == ev_data["storage_key"]
            ).one_or_none()
            if existing_ref:
                continue
            # Create reference
            ref = SourceReference(
                doc_id=doc_id,
                case_id=ev_data["case_id"],
                dataset_id=DEMO_DATASET_ID,
                origin_file=ev_data["storage_key"],
                source_type="pdf" if ev_data["mime_type"] == "application/pdf" else "csv",
                record_id=ev_id,
                row_number=None,
                field_names=[],
                field_values={},
                excerpt=f"Evidence {ev_id} for case {ev_data['case_id']}"
            )
            session.add(ref)

        session.flush()

        # Seed InvestigationFindings
        investigations = load_json(demo_root / "investigations.json") or []
        if not investigations:
            # Try to load from generator
            try:
                from scripts.generate_demo_dataset import INVESTIGATIONS as GEN_INV
                investigations = GEN_INV
            except:
                investigations = []

        for inv_data in investigations:
            inv_id = inv_data["id"]
            existing = session.query(InvestigationFinding).filter(InvestigationFinding.id == inv_id).one_or_none()
            if existing:
                print(f"  Investigation {inv_id} already exists — skipping")
                continue
            # Map evidence ids to doc ids
            evidence_doc_ids = []
            for ev_code in inv_data.get("evidence", []):
                if ev_code == "E-042":
                    evidence_doc_ids.append("evidence-042-demo")
                elif ev_code == "E-103":
                    evidence_doc_ids.append("evidence-103-demo")
                elif ev_code == "E-071":
                    evidence_doc_ids.append("evidence-071-demo")
                elif ev_code == "E-118":
                    evidence_doc_ids.append("evidence-118-demo")
                else:
                    # Generic
                    num = ev_code.replace("E-", "")
                    evidence_doc_ids.append(f"evidence-{num.lower()}-demo")

            finding = InvestigationFinding(
                id=inv_id,
                case_id=inv_data["case_id"],
                finding_type=inv_data.get("finding_type", "GENERAL"),
                title=inv_data.get("finding", f"Finding for {inv_data['case_id']}"),
                narrative=inv_data.get("narrative", inv_data.get("finding", "")),
                reason=inv_data.get("provenance", "Verified from multiple sources"),
                confidence=inv_data.get("confidence", 0.8),
                confidence_band=inv_data.get("confidence_band", "MEDIUM"),
                method="deterministic",
                entity_keys=inv_data.get("entity_keys", []),
                evidence=[{"doc_id": doc_id, "evidence_id": ev_code} for doc_id, ev_code in zip(evidence_doc_ids, inv_data.get("evidence", []))],
                details={
                    "investigator": inv_data.get("investigator_badge"),
                    "investigator_name": inv_data.get("investigator_name"),
                    "subject": inv_data.get("subject"),
                    "evidence_strength": inv_data.get("evidence_strength"),
                    "classification": inv_data.get("classification"),
                    "connection_path": inv_data.get("connection_path", []),
                    "limitations": inv_data.get("limitations", []),
                    "completed_at": inv_data.get("completed_at"),
                    "case_number": inv_data.get("case_number")
                },
                status=inv_data.get("status", "NEW"),
                reviewed_by=inv_data.get("investigator_id"),
                review_note=inv_data.get("finding"),
            )
            # Set created_at from completed_at if possible
            try:
                if inv_data.get("completed_at"):
                    dt = datetime.fromisoformat(inv_data["completed_at"].replace("Z", ""))
                    finding.created_at = dt
            except:
                pass
            session.add(finding)
            print(f"  Created investigation {inv_id} for {inv_data.get('case_number')}")

        session.flush()

        # Seed InvestigationSession for INV-0042 etc.
        for inv_data in investigations:
            sess_id = f"session-{inv_data['id'].lower()}"
            existing_sess = session.query(InvestigationSession).filter(InvestigationSession.id == sess_id).one_or_none()
            if existing_sess:
                continue
            sess = InvestigationSession(
                id=sess_id,
                dataset_id=DEMO_DATASET_ID,
                case_id=inv_data["case_id"],
                scope="case",
                title=inv_data.get("finding", "")[:200],
                state={
                    "investigation_id": inv_data["id"],
                    "finding": inv_data.get("finding"),
                    "evidence": inv_data.get("evidence"),
                    "subject": inv_data.get("subject"),
                    "classification": inv_data.get("classification"),
                    "evidence_strength": inv_data.get("evidence_strength"),
                    "connection_path": inv_data.get("connection_path"),
                    "limitations": inv_data.get("limitations"),
                    "provenance": inv_data.get("provenance"),
                    "investigator": inv_data.get("investigator_badge"),
                    "completed_at": inv_data.get("completed_at")
                },
                created_by=inv_data.get("investigator_id")
            )
            session.add(sess)
            print(f"  Created investigation session {sess_id}")

        session.commit()
        print("PostgreSQL seeding complete")

        # Verification
        user_count = session.query(User).filter(User.badge_number.like("DEMO-%")).count()
        case_count = session.query(Case).filter(Case.dataset_id == DEMO_DATASET_ID).count()
        doc_count = session.query(CaseDocument).filter(CaseDocument.dataset_id == DEMO_DATASET_ID).count()
        finding_count = session.query(InvestigationFinding).filter(InvestigationFinding.case_id.in_([c["id"] for c in cases_data])).count()
        print(f"Verification: {user_count} demo users, {case_count} demo cases, {doc_count} demo evidence, {finding_count} findings")
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
    settings = get_settings()
    # Check production MinIO mandatory
    is_production = settings.profile == "production" or settings.environment == "production"
    backend = settings.effective_object_store_backend

    try:
        if backend == "minio":
            from app.adapters.objectstore.minio_store import MinioObjectStore
            store = MinioObjectStore(settings)
            store.ensure_buckets()
            backend_name = "minio"
        else:
            if is_production:
                print("  FAILED: MinIO is mandatory in production but backend is not minio")
                print("  Refusing to silently fallback to local storage in production")
                return False
            from app.adapters.objectstore.local import LocalObjectStore
            store = LocalObjectStore(settings)
            backend_name = "local"
    except Exception as e:
        if is_production:
            print(f"  FAILED: MinIO unavailable in production ({e}) — seed fails per requirement")
            import traceback; traceback.print_exc()
            return False
        print(f"  MinIO not available ({e}), using local store for dev")
        from app.adapters.objectstore.local import LocalObjectStore
        store = LocalObjectStore(settings)
        backend_name = "local"

    bucket = settings.minio_bucket_documents

    # Load evidence and sources
    demo_root = ensure_demo_dataset_files()
    evidence_data = load_json(demo_root / "evidence" / "evidence.json") or []
    sources_data = load_json(demo_root / "sources" / "sources.json") or []

    if not evidence_data:
        for case_sub in (demo_root / "cases").iterdir():
            if case_sub.is_dir():
                evj = load_json(case_sub / "evidence.json")
                if evj:
                    evidence_data.extend(evj)

    # Deduplicate
    seen = set()
    deduped = []
    for ev in evidence_data:
        if ev.get("evidence_id") not in seen:
            seen.add(ev.get("evidence_id"))
            deduped.append(ev)
    evidence_data = deduped

    all_files = evidence_data + sources_data
    # Deduplicate by storage_key for MinIO (S-001 appears as both evidence and source)
    seen_sk = set()
    deduped_all = []
    for f in all_files:
        if f["storage_key"] not in seen_sk:
            seen_sk.add(f["storage_key"])
            deduped_all.append(f)
    all_files = deduped_all
    success = 0
    for f_data in all_files:
        storage_key = f_data["storage_key"]
        try:
            file_bytes = get_file_bytes(storage_key)
        except FileNotFoundError:
            print(f"  WARNING: File not found for {f_data.get('evidence_id') or f_data.get('source_id')} at {storage_key}")
            continue
        existing = store.stat(bucket, storage_key)
        if existing:
            # Verify hash
            existing_hash = existing.etag
            actual_hash = compute_sha256(file_bytes)
            # For MinIO, etag may be stored as sha256 in metadata
            if existing_hash and existing_hash != actual_hash and len(existing_hash) == 64:
                raise ValueError(f"Object {storage_key} exists with different hash: {existing_hash} vs {actual_hash}")
            print(f"  Object {storage_key} already exists — skipping")
            success += 1
            continue
        try:
            store.put(bucket, storage_key, file_bytes, content_type=f_data["mime_type"])
            print(f"  Created object {storage_key} in {backend_name} ({len(file_bytes)} bytes)")
            success += 1
        except Exception as e:
            print(f"  Failed to create object {storage_key}: {e}")
            import traceback; traceback.print_exc()
            if is_production:
                return False

    print(f"MinIO seeding complete ({backend_name}): {success}/{len(all_files)} objects")
    return True

def seed_neo4j():
    print("\nSeeding Neo4j...")
    try:
        settings = get_settings()
        backend = settings.effective_graph_backend
        demo_root = ensure_demo_dataset_files()

        # Load people and relationships
        people_data = load_json(demo_root / "people" / "people.json") or []
        relationships_data = load_json(demo_root / "relationships.json") or []
        timeline_data = load_json(demo_root / "timeline.json") or []

        if not people_data:
            # Try to aggregate from cases
            people_data = []
            seen_p = set()
            for case_sub in (demo_root / "cases").iterdir():
                if case_sub.is_dir():
                    pj = load_json(case_sub / "people.json")
                    if pj:
                        for p in pj:
                            if p["id"] not in seen_p:
                                seen_p.add(p["id"])
                                people_data.append(p)

        if not relationships_data:
            for case_sub in (demo_root / "cases").iterdir():
                if case_sub.is_dir():
                    rj = load_json(case_sub / "relationships.json")
                    if rj:
                        relationships_data.extend(rj)

        if backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            from app.domain.models import GraphNode, GraphEdge
            store = EmbeddedGraphStore(settings)

            # Load existing nodes
            existing_nodes = list(store._graph.nodes(data=True))
            existing_ids = {pk for pk, _ in existing_nodes}

            nodes_to_add = []
            for person in people_data:
                if person["id"] in existing_ids:
                    print(f"  Person {person['id']} already exists — skipping")
                    continue
                # Find case_ids for this person
                case_ids = []
                for case_sub in (demo_root / "cases").iterdir():
                    if case_sub.is_dir():
                        pj = load_json(case_sub / "people.json")
                        if pj and any(p["id"] == person["id"] for p in pj):
                            cj = load_json(case_sub / "case.json")
                            if cj:
                                case_ids.append(cj["id"])
                # Find evidence for this person from relationships
                source_docs = []
                for rel in relationships_data:
                    if rel["source"] == person["id"] or rel["target"] == person["id"]:
                        source_docs.extend(rel.get("evidence_list", []))
                # Map evidence codes to doc ids
                doc_ids_mapped = []
                for ev_code in source_docs:
                    if ev_code == "E-042":
                        doc_ids_mapped.append("evidence-042-demo")
                    elif ev_code == "E-103":
                        doc_ids_mapped.append("evidence-103-demo")
                    elif ev_code == "E-071":
                        doc_ids_mapped.append("evidence-071-demo")
                    elif ev_code == "E-118":
                        doc_ids_mapped.append("evidence-118-demo")
                    else:
                        num = ev_code.replace("E-", "")
                        doc_ids_mapped.append(f"evidence-{num.lower()}-demo")

                props = {
                    "name": person["name"],
                    "display_name": person["name"],
                    "full_name": person["name"],
                    "role": person.get("role", "Subject"),
                    "case_id": case_ids[0] if case_ids else "case-001-demo",
                    "case_ids": case_ids,
                    "source_doc_id": doc_ids_mapped[0] if doc_ids_mapped else "evidence-042-demo",
                    "source_doc_ids": doc_ids_mapped[:5],
                    "confidence": 0.95,
                    "dataset_id": DEMO_DATASET_ID,
                    "canonical_id": person["id"],
                    "entity_type": "PERSON"
                }
                props.update(person.get("properties", {}))
                nodes_to_add.append(GraphNode(
                    provenance_key=person["id"],
                    label=person.get("label", "Person"),
                    properties=props
                ))

            # Add event nodes for timeline
            for tl in timeline_data:
                event_id = tl["id"]
                if event_id in existing_ids:
                    continue
                props = {
                    "name": tl["title"],
                    "title": tl["title"],
                    "event_type": tl["event_type"],
                    "timestamp": tl["timestamp"],
                    "observed_at": tl["timestamp"],
                    "description": tl["description"],
                    "location": tl.get("location"),
                    "case_id": tl["case_id"],
                    "case_ids": [tl["case_id"]],
                    "source_doc_id": "evidence-042-demo",
                    "source_doc_ids": ["evidence-042-demo"],
                    "confidence": 0.9,
                    "dataset_id": DEMO_DATASET_ID,
                    "canonical_id": event_id,
                    "entity_type": "EVENT"
                }
                nodes_to_add.append(GraphNode(
                    provenance_key=event_id,
                    label="Event",
                    properties=props
                ))

            if nodes_to_add:
                store.upsert_nodes(nodes_to_add)
                print(f"  Added {len(nodes_to_add)} nodes to embedded graph")
            else:
                print("  All demo nodes already exist — skipping")

            # Edges
            edges_to_add = []
            existing_edges = set()
            for u, v, k, data in store._graph.edges(keys=True, data=True):
                existing_edges.add((u, v, data.get("_rel")))

            for rel in relationships_data:
                key = (rel["source"], rel["target"], rel["rel_type"])
                if key in existing_edges:
                    print(f"  Relationship {rel['source']} -{rel['rel_type']}-> {rel['target']} already exists — skipping")
                    continue
                # Map evidence
                ev_list = rel.get("evidence_list", [])
                doc_ids_mapped = []
                for ev_code in ev_list:
                    if ev_code == "E-042":
                        doc_ids_mapped.append("evidence-042-demo")
                    elif ev_code == "E-103":
                        doc_ids_mapped.append("evidence-103-demo")
                    elif ev_code == "E-071":
                        doc_ids_mapped.append("evidence-071-demo")
                    elif ev_code == "E-118":
                        doc_ids_mapped.append("evidence-118-demo")
                    else:
                        num = ev_code.replace("E-", "")
                        doc_ids_mapped.append(f"evidence-{num.lower()}-demo")

                props = {
                    "confidence": rel.get("confidence", 0.85),
                    "source_doc_id": doc_ids_mapped[0] if doc_ids_mapped else "evidence-042-demo",
                    "source_doc_ids": doc_ids_mapped,
                    "case_id": rel["case_id"],
                    "case_ids": [rel["case_id"]],
                    "dataset_id": DEMO_DATASET_ID,
                    "display_type": rel.get("properties", {}).get("display_type", rel["rel_type"])
                }
                props.update(rel.get("properties", {}))
                edges_to_add.append(GraphEdge(
                    source_key=rel["source"],
                    target_key=rel["target"],
                    rel_type=rel["rel_type"],
                    key=f"{rel['source']}-{rel['rel_type']}-{rel['target']}-{rel['case_id']}",
                    properties=props
                ))

            # Add PARTICIPATED_IN edges for timeline
            for tl in timeline_data:
                event_id = tl["id"]
                for participant in tl.get("participants", []):
                    edge_key = f"{participant}-PARTICIPATED_IN-{event_id}"
                    if (participant, event_id, "PARTICIPATED_IN") in existing_edges:
                        continue
                    edges_to_add.append(GraphEdge(
                        source_key=participant,
                        target_key=event_id,
                        rel_type="PARTICIPATED_IN",
                        key=edge_key,
                        properties={
                            "confidence": 0.9,
                            "source_doc_id": "evidence-042-demo",
                            "source_doc_ids": ["evidence-042-demo"],
                            "case_id": tl["case_id"],
                            "case_ids": [tl["case_id"]],
                            "dataset_id": DEMO_DATASET_ID,
                            "role": "Participant"
                        }
                    ))

            if edges_to_add:
                store.upsert_edges(edges_to_add)
                print(f"  Added {len(edges_to_add)} relationships to embedded graph")
            store.close()
            print("Neo4j (embedded) seeding complete")
            return True
        else:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            with driver.session(database=settings.neo4j_database) as neo_session:
                # Ensure constraints
                try:
                    neo_session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Person) REQUIRE n.provenance_key IS UNIQUE")
                    neo_session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Event) REQUIRE n.provenance_key IS UNIQUE")
                except:
                    pass

                for person in people_data:
                    case_ids = []
                    for case_sub in (demo_root / "cases").iterdir():
                        if case_sub.is_dir():
                            pj = load_json(case_sub / "people.json")
                            if pj and any(p["id"] == person["id"] for p in pj):
                                cj = load_json(case_sub / "case.json")
                                if cj:
                                    case_ids.append(cj["id"])
                    source_docs = []
                    for rel in relationships_data:
                        if rel["source"] == person["id"] or rel["target"] == person["id"]:
                            source_docs.extend(rel.get("evidence_list", []))
                    doc_ids_mapped = []
                    for ev_code in source_docs[:5]:
                        if ev_code == "E-042":
                            doc_ids_mapped.append("evidence-042-demo")
                        elif ev_code == "E-103":
                            doc_ids_mapped.append("evidence-103-demo")
                        elif ev_code == "E-071":
                            doc_ids_mapped.append("evidence-071-demo")
                        elif ev_code == "E-118":
                            doc_ids_mapped.append("evidence-118-demo")
                        else:
                            num = ev_code.replace("E-", "")
                            doc_ids_mapped.append(f"evidence-{num.lower()}-demo")

                    neo_session.run(
                        """
                        MERGE (p:Person {provenance_key: $id})
                        SET p.label = $label,
                            p.name = $name,
                            p.display_name = $name,
                            p.case_ids = $case_ids,
                            p.case_id = $case_id,
                            p.source_doc_ids = $source_docs,
                            p.source_doc_id = $source_doc,
                            p.confidence = 0.95,
                            p.dataset_id = $dataset_id,
                            p.canonical_id = $id,
                            p.entity_type = 'PERSON',
                            // Projection flags: Neo4j raises
                            // UnknownPropertyKeyWarning for every projection
                            // that reads a property key no node carries, so the
                            // seed writes them like the graph adapter does.
                            p.is_active = true,
                            p.is_document_artifact = false,
                            p.staging = false
                        """,
                        id=person["id"],
                        label=person.get("label", "Person"),
                        name=person["name"],
                        case_ids=case_ids,
                        case_id=case_ids[0] if case_ids else "case-001-demo",
                        source_docs=doc_ids_mapped,
                        source_doc=doc_ids_mapped[0] if doc_ids_mapped else "evidence-042-demo",
                        dataset_id=DEMO_DATASET_ID
                    )
                    print(f"  Merged person {person['id']}")

                for tl in timeline_data:
                    neo_session.run(
                        """
                        MERGE (e:Event {provenance_key: $id})
                        SET e.name = $title,
                            e.title = $title,
                            e.event_type = $event_type,
                            e.timestamp = $ts,
                            e.observed_at = $ts,
                            e.case_ids = [$case_id],
                            e.case_id = $case_id,
                            e.dataset_id = $dataset_id,
                            e.canonical_id = $id,
                            e.entity_type = 'EVENT',
                            e.is_active = true,
                            e.is_document_artifact = false,
                            e.staging = false
                        """,
                        id=tl["id"],
                        title=tl["title"],
                        event_type=tl["event_type"],
                        ts=tl["timestamp"],
                        case_id=tl["case_id"],
                        dataset_id=DEMO_DATASET_ID
                    )
                    for participant in tl.get("participants", []):
                        neo_session.run(
                            """
                            MATCH (a:Person {provenance_key: $source}), (b:Event {provenance_key: $target})
                            MERGE (a)-[r:PARTICIPATED_IN {key: $key}]->(b)
                            SET r.confidence = 0.9,
                                r.source_doc_id = 'evidence-042-demo',
                                r.case_id = $case_id,
                                r.dataset_id = $dataset_id
                            """,
                            source=participant,
                            target=tl["id"],
                            key=f"{participant}-PARTICIPATED_IN-{tl['id']}",
                            case_id=tl["case_id"],
                            dataset_id=DEMO_DATASET_ID
                        )

                for rel in relationships_data:
                    ev_list = rel.get("evidence_list", [])
                    doc_ids_mapped = []
                    for ev_code in ev_list[:5]:
                        if ev_code == "E-042":
                            doc_ids_mapped.append("evidence-042-demo")
                        elif ev_code == "E-103":
                            doc_ids_mapped.append("evidence-103-demo")
                        elif ev_code == "E-071":
                            doc_ids_mapped.append("evidence-071-demo")
                        elif ev_code == "E-118":
                            doc_ids_mapped.append("evidence-118-demo")
                        else:
                            num = ev_code.replace("E-", "")
                            doc_ids_mapped.append(f"evidence-{num.lower()}-demo")
                    source_doc = doc_ids_mapped[0] if doc_ids_mapped else "evidence-042-demo"

                    # Use safe rel type from whitelist
                    rel_type = rel["rel_type"]
                    if rel_type not in ["PARTICIPATED_IN", "OWNS_VEHICLE", "USES_PHONE", "OWNS_ACCOUNT", "CALLED", "MEMBER_OF", "ASSOCIATE_OF", "RELATIVE_OF", "ARRESTED_WITH", "NAMED_ACCOMPLICE_OF", "TRANSFER_TO", "SHARED_PHONE", "SHARED_ACCOUNT", "SHARED_VEHICLE", "SHARED_LOCATION", "LOCATED_AT"]:
                        continue

                    query = f"""
                        MATCH (a {{provenance_key: $source}}), (b {{provenance_key: $target}})
                        MERGE (a)-[r:{rel_type} {{key: $key}}]->(b)
                        SET r.confidence = $confidence,
                            r.source_doc_ids = $evidence,
                            r.source_doc_id = $single,
                            r.case_id = $case_id,
                            r.dataset_id = $dataset_id,
                            r.display_type = $display
                    """
                    neo_session.run(
                        query,
                        source=rel["source"],
                        target=rel["target"],
                        key=f"{rel['source']}-{rel['rel_type']}-{rel['target']}-{rel['case_id']}",
                        confidence=rel.get("confidence", 0.85),
                        evidence=doc_ids_mapped,
                        single=source_doc,
                        case_id=rel["case_id"],
                        dataset_id=DEMO_DATASET_ID,
                        display=rel.get("properties", {}).get("display_type", rel["rel_type"])
                    )
                    print(f"  Merged relationship {rel['source']} -{rel['rel_type']}-> {rel['target']}")

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
    print(f"Demo root: {DEMO_ROOT}")
    ensure_demo_dataset_files()
    pg_ok = seed_postgres()
    minio_ok = seed_minio()
    neo4j_ok = seed_neo4j()
    print("\n" + "="*60)
    print(f"Seed results: PostgreSQL={pg_ok}, MinIO={minio_ok}, Neo4j={neo4j_ok}")
    if pg_ok and minio_ok and neo4j_ok:
        print("All demo data seeded successfully — evaluator-ready")
        print("\nDataset Summary:")
        demo_root = ensure_demo_dataset_files()
        manifest = load_json(demo_root / "manifest.json")
        if manifest:
            print(f"  Cases: {manifest['counts']['cases']}")
            print(f"  People: {manifest['counts']['people']}")
            print(f"  Relationships: {manifest['counts']['relationships']}")
            print(f"  Evidence: {manifest['counts']['evidence']}")
            print(f"  Sources: {manifest['counts']['sources']}")
            print(f"  Timeline: {manifest['counts']['timeline_events']}")
            print(f"  Investigations: {manifest['counts']['investigations']}")
        print("\nDemo Access:")
        print("  Admin: DEMO-ADMIN / DemoAdmin@2026")
        print("  Investigator: DEMO-INVESTIGATOR / DemoInvestigator@2026")
        print("  Viewer: DEMO-VIEWER / DemoViewer@2026")
        return 0
    else:
        print("Some seeding failed — check logs")
        return 1

if __name__ == "__main__":
    sys.exit(main())
