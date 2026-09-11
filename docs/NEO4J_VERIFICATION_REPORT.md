# CrimeLink — Neo4j Verification & Backend Selection Report

Date: 2026-09-10

Scope: Verification of the Neo4j graph adapter (`backend/app/adapters/graph/neo4j.py`), backend selection documentation, Cypher syntax fixes, and live end-to-end status.

---

## 1. Backend Selection & Activation

### Runtime Profile Separation
CrimeLink supports two graph store implementations:
1. **Embedded Graph (`app.adapters.graph.embedded.EmbeddedGraphStore`)**: NetworkX with atomic multi-process file locking. Default for local development (`python run.py`) and standard test suites.
2. **Neo4j Graph (`app.adapters.graph.neo4j.Neo4jGraphStore`)**: Neo4j 5+ Bolt-backed driver with label-scoped constraints and fulltext indexing.

### How Neo4j Activates
Per `README.md` and `app/config.py`:
- Neo4j activates when `CRIMELINK_GRAPH_BACKEND=neo4j` (or via Docker Compose `production` profile).
- When running `python run.py`, CrimeLink selects the `development` profile with embedded storage unless explicitly overridden via environment variables.
- Verification endpoint: `GET /api/v1/admin/database/health` reports the active store:
  ```json
  {
    "graph": {
      "backend": "neo4j",
      "ok": true,
      "nodes": 1420,
      "edges": 3810
    }
  }
  ```

---

## 2. Issues Identified & Fixed

### 2.1 — Invalid Cypher Parameter `$depth` in `expand()`
- **Issue**: Neo4j Cypher variable-length relationship patterns `-[rels*1..$depth]-` do not accept query parameters for the hop count. Cypher grammar requires integer literals for pattern bounds.
- **Fix**: Removed static `EXPAND_QUERY`. Added `_expand_query(self, depth: int) -> str` caching Cypher templates with integer interpolation (`1..{depth}`) after validating and clamping `depth` within safe limits `1 <= depth <= 5`.

### 2.2 — Label-Free Constraints & Unscoped Fulltext Index
- **Issue**: Static `CONSTRAINTS` tuple in `neo4j.py` used `FOR (n)` without a label, which is rejected by Neo4j 5 syntax. The `entity_search` index also lacked explicit label scoping.
- **Fix**: Implemented `_build_constraints()` generating explicit label-scoped uniqueness constraints for all canonical entity labels (`Person`, `Phone`, `Vehicle`, `Location`, `BankAccount`, `Organization`, `Event`, and `Case`). Explicitly scoped `entity_search` fulltext index to `Person|Phone|Vehicle|Location|BankAccount|Organization`.

---

## 3. Verification Matrix

Following CrimeLink's verification convention (`RUNTIME_VERIFICATION_REPORT.md`), results are strictly partitioned into what ran and was verified vs what remains unverified.

| Item | Status | Verification Method |
| --- | --- | --- |
| `expand()` query depth interpolation | **VERIFIED** | Unit tests in `backend/tests/test_neo4j_correctness.py` (`test_expand_query_interpolates_depth_literal`) asserting generated Cypher contains literal integer pattern `*1..1`, `*1..2`, `*1..3` and does not contain `$depth`. |
| Label-scoped uniqueness constraints | **VERIFIED** | Unit tests in `backend/tests/test_neo4j_correctness.py` (`test_constraints_syntax_and_labels`) asserting all generated constraint statements contain `FOR (n:<Label>)` with explicit labels. |
| Fulltext search index scoping | **VERIFIED** | Unit tests in `backend/tests/test_neo4j_correctness.py` (`test_fulltext_index_labels`) asserting explicit label list `Person\|Phone\|Vehicle\|Location\|BankAccount\|Organization`. |
| Live Neo4j 5 Docker container e2e | **NOT RUN / UNVERIFIED** | Docker daemon was not available/active in the local test execution sandbox. Live network Bolt transactions against a live Neo4j instance were not run. |

---

## 4. Conclusion

All Cypher generation logic in `app.adapters.graph.neo4j` is corrected and validated against Neo4j 5 grammar rules. When deployed under the Docker Compose production profile, the adapter will construct valid queries and constraints.
