# CrimeLink Production Demo — Final Acceptance Checklist

## Deliverables
- Canonical demo_dataset package at `demo_dataset/` and `backend/demo_dataset/`:
  - manifest.json (20 cases, 100 people, 216 relationships, 300 evidence, 120 sources, 258 timeline, 10 investigations)
  - cases/CR-1024..CR-1043/case.json + people/relationships/evidence/timeline/investigations.json
  - evidence/E-*/original.pdf|csv (real valid PDFs via reportlab, CSVs with unique nonce per evidence)
  - sources/S-*/source-document.pdf (real PDFs)
  - people/people.json, relationships.json, timeline.json, investigations.json
  - SHA-256 verifiable, deterministic keys evidence/E-042/original.pdf
- Seed scripts:
  - `backend/scripts/generate_demo_dataset.py` — deterministic generator covering all required patterns
  - `backend/scripts/seed_demo.py` — production-grade idempotent seeder (missing->create, identical->skip, inconsistent->fail)
  - `backend/scripts/reset_demo.py` — safe reset with CRIMELINK_ALLOW_DEMO_RESET gate, scoped deletes
  - `backend/scripts/validate_demo.py` — comprehensive cross-store validation (PG/Neo4j/MinIO/API/Viewer 403)
- Fixed services:
  - `app/services/source_viewer.py` — MinIO-aware (tries MinIO first in prod, filesystem fallback, fails loudly if MinIO mandatory but unavailable)
  - `app/api/v1/sources.py` — MinIO-aware raw/preview/file endpoints, dual-path auth, range support, storage field
  - `app/api/v1/investigator_activity.py` — real DB query from InvestigationFinding/Session, no hardcoded DEMO_ACTIVITY
  - Frontend `InvestigatorActivity.tsx` + `InvestigatorActivityPage.tsx` — no fake fallback, shows Unavailable not fake
- Docker Compose: postgres -> neo4j -> minio -> redis -> api healthy -> seed (service_completed_successfully) -> validate -> web
  - No auto-seed on API restart (seed is separate job)
  - MinIO mandatory in prod (seed and source_viewer fail if unavailable, no silent Local fallback)

## Case Coverage (20 cases)
- CR-1024 (CASE-001): Hero — direct communication PERSON-001↔002, repeated CALLED, E-042
- CR-1025: Shared resource SHARED_ACCOUNT, financial TRANSFER_TO
- CR-1026: Co-location LOCATED_AT, shared event PARTICIPATED_IN
- CR-1027: Common contact ASSOCIATE_OF
- CR-1028: Repeated communication CALLED x5
- CR-1029: Cross-case person PERSON-001 overlap
- CR-1030: Timeline-heavy 40 events
- CR-1031: Mixed relationship types
- CR-1032: No connection (negative control, 0 relationships, 2 people isolated)
- CR-1033: Financial association
- CR-1034: 2-hop chain
- CR-1035: 3-hop chain
- CR-1036: Sparse network 2 relationships
- CR-1037: Dense network 30 relationships
- CR-1038: Conflicting evidence
- CR-1039: Weak evidence / inference
- CR-1040: Missing provenance
- CR-1041: 4-hop chain
- CR-1042: Completed Viewer review
- CR-1043: Cross-jurisdiction link

All REL_TYPES from whitelist used: CALLED, PARTICIPATED_IN, USES_PHONE, OWNS_VEHICLE, OWNS_ACCOUNT, MEMBER_OF, ASSOCIATE_OF, RELATIVE_OF, ARRESTED_WITH, NAMED_ACCOMPLICE_OF, TRANSFER_TO, SHARED_PHONE, SHARED_ACCOUNT, SHARED_VEHICLE, SHARED_LOCATION, LOCATED_AT

## Hero Workflow Verified
CR-1024 -> PERSON-001/B -> COMMUNICATION (CALLED, E-042) -> Why? -> E-042 (real PDF %PDF-1.4) -> Source S-001 -> Timeline 8 events -> Finding INV-0042 (HIGH/FACT, 2026-09-15) -> Activity

## Real Files Check
- PDFs start with %PDF-, parseable via pypdf
- CSVs have evidence_id + case_id + unique_nonce to ensure unique SHA-256 per (case_id, content_hash) constraint
- Size_bytes matches actual file size, SHA-256 matches
- MinIO objects stat + get verified

## Cross-Store Validation
- PostgreSQL: users 3, cases 20, evidence 419 (300 evidence + 120 sources - 1 duplicate S-001 deduped to 419), dataset DEMO-DATASET-001 active, INV-0042 persisted
- MinIO: 419 objects, size/hash match, PDF validity
- Neo4j: embedded 100 people + 258 events = 358 nodes, 607 relationships (216 base + 391 timeline PARTICIPATED_IN + overlaps), case_ids and source_doc_ids present, 1-hop..4-hop traversable, cross-case 56 people
- API: login for all 3 demo users, case/evidence/timeline access, investigator activity real DB, Viewer 403 for investigate/jobs/ai/cases/*/ask/cases POST

## RBAC
- DEMO-ADMIN/INVESTIGATOR/VIEWER same data scope (20 cases), different operation permissions
- JurisdictionScope respected
- Viewer read-only + Investigator Activity, no investigation controls

## Security
- No Graph-RAG/embeddings terminology in UI (grep -i embedding = 0)
- No fake success — seed fails loudly in prod if MinIO unavailable
- No hardcoded DEMO_ACTIVITY fallback — frontend/backend show Unavailable
- Reset requires CRIMELINK_ALLOW_DEMO_RESET or DEMO_MODE flag, refuses prod without explicit flag
- Source paths must be relative, no .. traversal, HMAC-signed raw URLs

## Idempotency
- seed_demo.py: missing->create, identical->skip, inconsistent->fail (ValueError)
- Tested reset->seed->validate twice, no duplicates, counts stable

## Performance
- Seed: <3s for PG (419 docs), MinIO 419 objects, Neo4j 358 nodes 607 edges
- Validate: <5s including API login checks

## Tests
- test_rbac_investigator_viewer.py: 27 passed
- test_sources_rendering.py: 18 passed
- TS build: tsc -b 0 errors

## Docker Compose
- Order: postgres (healthy) -> neo4j (healthy) -> minio (healthy) -> redis (healthy) -> api (healthy) -> seed (completed_successfully) -> validate (completed_successfully) -> web
- Seed job: python scripts/seed_demo.py, restart no, env production
- Validate job: python scripts/validate_demo.py, depends_on seed completed_successfully
- Web depends_on validate completed_successfully

## Judge Test (2-3 min)
1. Login as Investigator (DEMO-INVESTIGATOR / DemoInvestigator@2026) -> Cases -> CR-1024 -> People -> Relationships -> PERSON-001 ↔ PERSON-002 -> Communication -> Why? -> E-042 -> Source -> Timeline -> Investigation Activity -> INV-0042
2. Login as Viewer (DEMO-VIEWER / DemoViewer@2026) -> CR-1024 -> Investigator Activity -> INV-0042 -> verify read-only, no investigation controls, same data scope

No download/upload/import/run scripts required — data auto-populated on deployment via seed job.
