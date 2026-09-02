# Verification

How to prove, from a fresh checkout, that CrimeLink does what it claims. Every
item is checkable with a command — no item is satisfied by inspection alone.

```bash
python3.11 -m venv .venv && .venv/bin/pip install ./backend
export PYTHONPATH=backend CRIMELINK_PROFILE=embedded
export CRIMELINK_DATA_DIR=$PWD/var/data CRIMELINK_OBJECT_STORE_DIR=$PWD/var/objects
.venv/bin/python backend/scripts/seed_demo.py
```

---

## 1. The three guarantees

### G1 — Everything is evidenced

*Claim: no node or edge can exist in the graph without a document behind it.*

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_guarantees.py -q
# 14 passed
```

The three G1 tests are `test_injector_refuses_a_node_without_a_source_document`,
`test_the_public_injection_entry_point_requires_a_document_id` and
`test_validation_rejects_an_unevidenced_edge`.

What the tests assert:

* `GraphNode` / `GraphEdge` raise `UnevidencedGraphWriteError` when
  `properties["source_doc_id"]` is missing — including the meta-relationship
  types (`POTENTIAL_ALIAS`, `SIMILARITY_REJECTED`) that a naive implementation
  would exempt.
* `app/adapters/graph/injector.py` is the only module that issues Cypher writes;
  its validation rejects an unevidenced edge before a transaction opens.

Runtime check on the seeded case:

```bash
.venv/bin/python - <<'PY'
from app.config import reload_settings, Settings
reload_settings()
from app.container import Container, set_container
c = Container(Settings()); set_container(c)
snap = c.graph_store.snapshot("<case-id>", include_staging=True)
missing = [n.provenance_key for n in snap.nodes.values()
           if not n.properties.get("source_doc_ids")]
print("nodes without evidence:", len(missing))
PY
```

### G2 — Nothing serious happens without a human

*Claim: fuzzy matches are never auto-merged; findings are never auto-confirmed;
nothing is deleted.*

| Behaviour | Where | Verified by |
|---|---|---|
| Fuzzy match (≥ 0.85) creates a queue item, never a merge | `app/pipeline/entity_resolution.py::_propose_aliases` | `test_pipeline.py`, plus the seeded case: 16 `PENDING` items, zero auto-merges |
| Merge requires INVESTIGATOR+ **and** a written note | `app/api/v1/resolution.py` | `test_guarantees.py::test_a_merge_requires_a_written_rationale`, `::test_a_viewer_cannot_merge` |
| A rejected pair is tombstoned and never re-proposed | `tombstone_reject` + `has_tombstone` | `test_guarantees.py::test_a_rejected_pair_never_comes_back` |
| Patterns are created `NEW` and stay `NEW` | `app/analytics/patterns.py` | `test_guarantees.py::test_patterns_are_never_confirmed_automatically` |
| No `DELETE` / `PUT` anywhere in the API | — | `curl -s localhost:8000/api/openapi.json \| grep -c '"delete"'` → `0` |
| Merges are reversible | `POST /resolution/{id}/unmerge` | `test_guarantees.py::test_a_merge_is_audited_and_reversible` |

Runtime: after seeding, `GET /api/v1/resolution?case_id=…` returns 16 `PENDING`
items and `GET /api/v1/patterns?case_id=…` returns two `NEW` findings. Nothing
in the system changes those states on its own.

### G3 — Everything is auditable and tamper-evident

*Claim: the audit log cannot be edited without detection; documents cannot be
altered after ingestion.*

```bash
# Chain verification (as an ADMIN)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/admin/audit/verify
# {"valid":true,"checked":N,"first_tampered_id":null,"head_hash":"…"}
```

`test_guarantees.py::test_tampering_with_an_audit_row_is_detected` and
`::test_deleting_an_audit_row_is_detected` prove the negative case: they modify a
row in place, delete a row, and assert that `verify` reports the **first tampered
entry** rather than merely "invalid" — so an administrator knows where to start
looking.

Documents are addressed by SHA-256 and the object store refuses to overwrite an
object whose content differs (`ConflictError`). Re-uploading a byte-identical
file is idempotent; re-uploading a *modified* file is a new document.

---

## 2. Functional checks

| Area | Command | Expected |
|---|---|---|
| Full test suite | `pytest tests/ -q` | 80 passed (27 domain, 11 pipeline, 19 API, 14 guarantees, 9 analytics) |
| Seven source adapters | `pytest tests/test_pipeline.py -q` | 11 passed |
| Cross-script entity resolution | seed, then `GET /resolution?case_id=…` | `सुरेश मेहता` ↔ `Suresh Mehta` at 1.00 |
| Deterministic extraction confidence | any FIR node | 0.95 for regex matches |
| NLP confidence cap | `CRIMELINK_NLP_MAX_CONFIDENCE` | every model-produced node ≤ 0.80 |
| Call aggregation | `GET /graph/cases/{id}` | `CALLED` edges carry `call_count`, `first_ts`, `last_ts` — one edge per pair, not per call |
| Expand depth cap | `GET /graph/nodes/{key}/expand?depth=9` | clamped to 2 |
| Role enforcement | `POST /cases` as VIEWER | 403 |
| Jurisdiction scoping | `GET /cases/{other-district-id}` | 404, identical to a non-existent id |
| Rate limiting | 11 logins in a minute | 429 on the 11th |
| Account lockout | 6 wrong passwords | 401 with a lockout, reset on success |
| Refresh-token reuse | replay a rotated refresh token | 401 **and** the whole token family is revoked |
| Watermarked export | `GET /cases/{id}/export` | PDF, watermark on every page, SHA-256 per source document |
| Quarantine | upload a malformed CSV | document is quarantined with the reason, never silently accepted |

---

## 3. Console (React) checks

```bash
cd frontend && npm install && npm run smoke   # builds, then renders the bundle
```

The smoke test runs the **production bundle** in jsdom against the **live API**
and asserts that the case list, case detail (documents, processing column,
timeline), review queue and graph route all mount and render real data. It exits
non-zero on any unexpected console error.

---

## 4. Deployment checks

```bash
cp .env.example .env                  # set CRIMELINK_SECRET_KEY
docker compose up -d --build          # 8 containers
docker compose ps                     # all healthy
docker compose run --rm api python scripts/seed_demo.py
curl -s localhost/api/v1/health/ready | python3 -m json.tool
#   database, graph, object_store, broker all "ok"
docker compose logs worker | grep pipeline   # six stages per document
```

`GET /api/v1/health/ready` reports each dependency separately, so a partially
degraded stack is visible instead of a single green light.
