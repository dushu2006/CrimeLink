# CrimeLink — Runtime Fix Verification Report

Branch: `arena/01a06d5c-crimelink` · Date: 2026-09-05

Scope: fix the four defects found during the final runtime verification
(`docs/RUNTIME_VERIFICATION_REPORT.md`) — the cross-loop asyncpg pool bug, the
embedded-graph flush race, the missing `psycopg2` dependency, and the
PostgreSQL bootstrap transaction fragility. **Nothing was committed or pushed.**

---

## A. Exact files changed

| File | Issue | Change |
| --- | --- | --- |
| `backend/app/api/v1/jobs.py` | 1 | WS auth uses a loop-local dedicated engine/pool |
| `backend/app/db/session.py` | 1, 4 | Added `create_dedicated_async_engine`; refactored `_bootstrap_postgres` |
| `backend/app/main.py` | 1 | Lifespan disposes the auth engine on shutdown |
| `backend/app/adapters/graph/embedded.py` | 2 | Atomic unique-temp flush + inter-process single-writer lock |
| `backend/pyproject.toml` | 3 | Declared `psycopg2-binary==2.9.10` |
| `backend/tests/test_ws_auth.py` | 1 | +2 regression tests; removed a duplicate import |
| `backend/tests/test_db_bootstrap.py` | 4 | **New** — 2 focused regression tests |
| `backend/tests/test_graph_persistence.py` | 2 | **New** — 4 regression tests |

No production test was weakened or deleted; no Phase-11 dataset semantics were
touched; no auth rule was changed.

---

## B. Root cause of each issue

1. **Cross-loop asyncpg pool (CRITICAL).** `_authorize_websocket` ran on a
   dedicated event loop but used the process-wide `async_session()` — backed by
   the REST asyncpg engine. asyncpg connections are bound to the loop that
   created them, so connections created on the auth loop were later checked out
   by REST on the API loop and raised
   `RuntimeError: attached to a different loop` (HTTP 500 on every login after
   ~30 WS authorizations).

2. **Embedded flush race (MEDIUM).** `_flush` wrote a fixed
   `graph.json.tmp` under only a process-local `threading.RLock`. Two worker
   processes (`celery --concurrency=2`) raced the `.tmp → graph.json` rename
   (observed `FileNotFoundError`) and, worse, each process holds its own
   in-memory NetworkX graph, so concurrent writers silently lost each other's
   nodes (last-writer-wins).

3. **Missing psycopg2 (MEDIUM).** `postgres_dsn_sync` is
   `postgresql+psycopg2://` and is used by Alembic, the Celery workers' sync
   sessions and admin paths, but `pyproject.toml` declared only
   `asyncpg`/`aiosqlite`. In the production image these paths raise
   `ModuleNotFoundError` at first use.

4. **Bootstrap transaction fragility (LOW).** `_bootstrap_postgres` ran all six
   statements inside one `engine.begin()` transaction. A single failing
   `CREATE EXTENSION` (pg_trgm/btree_gin absent from a minimal bundle) aborted
   the transaction, so the four append-only `REVOKE`s were silently skipped.

---

## C. Exact fix applied

1. **WS auth gets its own engine/pool.** `db.session.create_dedicated_async_engine()`
   builds a fresh async engine (same URL/pool settings). `jobs.py` keeps a
   module-level `_auth_engine`/`_auth_sessionmaker`, created lazily and used
   **only** on the auth loop; `_authorize_websocket` uses
   `_get_auth_sessionmaker()()` instead of the process-wide `async_session()`.
   `dispose_auth_engine()` disposes the pool **on the auth loop** and is wired
   into the app lifespan. The authorization chain (`get_scope` → `require_case`)
   and the 4401/4403 close-code semantics are byte-for-byte unchanged.

2. **Atomic flush + single-writer guard.** `_flush` writes to a unique temp file
   (`graph.json.tmp-<pid>-<thread>-<uuid4>`), `fsync`s it, and atomically
   `os.replace`s it over the snapshot (cleanup in `finally`). `_acquire_process_lock`
   takes an exclusive non-blocking advisory lock (POSIX `fcntl.flock`, Windows
   `msvcrt.locking`) on a `.lock` sidecar for the store's lifetime; a second
   writer is rejected immediately with an actionable error (`celery
   --concurrency=1` or switch to Neo4j). `close()` releases the lock. Because a
   second process cannot merge into an in-process NetworkX graph, the guard
   makes the unsupported multi-process-writer configuration fail fast instead
   of silently losing data.

3. **Dependency declared.** `psycopg2-binary==2.9.10` added to `pyproject.toml`
   (binary wheel because the runtime image is `slim` with no libpq-dev).

4. **Independent hardening.** `_bootstrap_postgres` runs each statement in its
   own transaction via `engine.connect()` + per-statement `commit`/`rollback`.
   Extension failure → `db.extension_unavailable` (warning); `REVOKE` failure →
   `db.audit_hardening_failed` (error, loud). Nothing is pretended to succeed.

---

## D. Tests added / changed

- `tests/test_ws_auth.py`
  - `test_repeated_ws_auth_does_not_poison_rest_db` — 40 authorized handshakes,
    then REST GET + login still 200; asserts `_get_auth_engine() is not
    get_async_engine()`.
  - `test_ws_auth_never_touches_the_process_wide_sessionmaker` — monkeypatches
    `get_async_sessionmaker` to fail if called from the `crimelink-ws-auth`
    thread; proves a handshake never borrows the REST sessionmaker.
  - Removed a pre-existing duplicate `async_session` import (F811).
- `tests/test_db_bootstrap.py` (**new**)
  - `test_extension_failure_does_not_skip_audit_revokes` — a fake engine/connection
    that mimics PostgreSQL's aborted-transaction semantics proves all six
    statements are attempted and the `REVOKE`s run in clean transactions even
    when the first `CREATE EXTENSION` fails.
  - `test_all_statements_apply_when_nothing_fails`.
- `tests/test_graph_persistence.py` (**new**)
  - `test_snapshot_persists_and_reloads`,
    `test_second_writer_on_same_snapshot_is_rejected`,
    `test_flush_is_atomic_and_leaves_no_temp_files`,
    `test_concurrent_flushes_from_threads_leave_a_valid_snapshot`
    (4 threads × 50 nodes → valid 200-node snapshot, no temp residue).

---

## E. Full test results

| Suite | Result |
| --- | --- |
| Targeted (ws_auth + db_bootstrap + graph_persistence) | **18 passed** |
| Full backend `pytest` | **236 passed in 423.44s** (includes the 8 new tests) |
| Frontend `npm run typecheck` (`tsc -b`) | pass |
| Frontend `npm test` | **19/19 pass** |
| Frontend `npm run build` | success (76 modules, 785.37 kB JS) |
| ruff (`E4,E7,E9,F` + import order) on changed files | clean |

---

## F. Clean installation now includes psycopg2 — **YES**

A fresh `backend/.venv` was created and `pip install -e ".[dev]"` resolved
`psycopg2-binary 2.9.10` from the (now-declared) dependency. Verified:
`import psycopg2` → `2.9.10 (dt dec pq3 ext lo64)`; SQLAlchemy
`postgresql+psycopg2` dialect resolves; `import alembic` → `1.14.0`; the sync
engine path (`app.db.session.get_sync_engine`) builds against it.

## G. Repeated WS authorization without REST failures — **YES (live asyncpg)**

A real PostgreSQL (pgserver-bundled 16.2) was started and the real FastAPI app
driven via TestClient so REST runs on one loop and WS auth on the dedicated
loop, against **real asyncpg**. Results: 40 WS authorizations interleaved with
REST logins — **every REST call returned 200**, no `different loop` error;
REST GET + login still healthy after the burst; refresh rotation returns a new
access token. 4401 (missing/garbage/expired/unknown token) and 4403
(out-of-scope case) close codes all reproduced correctly.

## H. Multi-process flush safety — **YES (guard verified cross-process)**

- Cross-process test: a child process holding the store's lock causes the parent
  to be **rejected** with the single-writer error; the parent acquires the lock
  after the child exits.
- Atomicity: unique temp + `os.replace`; a 10-flush run leaves **zero** `.tmp`
  residue and valid JSON.
- Concurrency: 4 threads × 50 nodes against one store → a valid 200-node snapshot.
- The store is single-writer by design; concurrent multi-process writers are now
  rejected up front rather than silently losing data (Neo4j is the concurrent
  writer path).

## I. PostgreSQL hardening survives extension failure — **YES (live)**

Live bootstrap log against the minimal bundle (which lacks pg_trgm/btree_gin):

```
db.extension_unavailable    CREATE EXTENSION IF NOT EXISTS pg_trgm   (warning)
db.extension_unavailable    CREATE EXTENSION IF NOT EXISTS btree_gin (warning)
db.audit_hardening_applied  REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC
db.audit_hardening_failed   REVOKE UPDATE, DELETE ON audit_logs FROM crimelink (role absent, loud)
db.audit_hardening_applied  REVOKE UPDATE, DELETE ON audit_chain_head FROM PUBLIC
db.audit_hardening_failed   REVOKE UPDATE, DELETE ON audit_chain_head FROM crimelink (role absent, loud)
```

The extension failures no longer abort the `REVOKE`s: the `FROM PUBLIC`
hardening applied, and the missing-role `REVOKE`s failed explicitly (the
`crimelink` role genuinely does not exist in the scratch database — correct
behavior, not a regression).

## J. Remaining limitations

- **Neo4j and MinIO** remain unstartable in this sandbox (distribution hosts
  blocked) — unchanged from the prior verification and unrelated to these four
  fixes (which touch the relational/embedded layers).
- The cross-loop fix was verified against a pgserver-bundled real PostgreSQL
  (same asyncpg driver, same SQLAlchemy path), not the production cluster.
- The embedded store remains **single-writer**; that is now enforced, not
  extended. Concurrent graph writers require the Neo4j backend.
- The live WS/auth security run exercised 4401/4403/login/refresh/40×WS; the
  time-boxed-grant accept/expiry paths are covered by the pytest suite
  (`test_ws_time_bound_grant_opens_the_case_like_the_rest_api`,
  `test_ws_grant_expiry_is_enforced`) against the same code path.
- ruff 0.16.6's expanded default rules flag pre-existing project idioms
  (FastAPI `Depends` defaults, blind `except`, `datetime.utcnow`, …) that predate
  these changes and are out of scope; the changed files pass the classic
  `E4/E7/E9/F` + import-order rules.

---

## Post-fix security verification (12/12)

| # | Check | Result |
| --- | --- | --- |
| 1 | REST authentication works | ✅ live + suite |
| 2 | WebSocket authentication works | ✅ live + suite |
| 3 | Invalid JWT → 4401 | ✅ live + suite |
| 4 | Expired/invalid auth → 4401 | ✅ live + suite |
| 5 | Unknown/inactive user → 4401 | ✅ live + suite |
| 6 | Unauthorized/out-of-scope case → 4403 | ✅ live + suite |
| 7 | Authorized case → accepted | ✅ live + suite |
| 8 | Time-bound lawful grant → accepted | ✅ suite |
| 9 | Expired grant → 4403 | ✅ suite |
| 10 | Repeated WS auth does not poison REST | ✅ live asyncpg + suite |
| 11 | Refresh rotation/replay unchanged | ✅ live + suite |
| 12 | No authorization rule weakened | ✅ (chain unchanged; only the engine used changed) |

---

## FIXED AND VERIFIED (runtime)

All four fixes are **fixed and runtime-verified** against real services:

1. **Cross-loop WS auth** — fixed; proven live against real asyncpg with 40 WS
   authorizations and zero subsequent REST failures.
2. **Embedded flush** — fixed; atomicity, thread concurrency, and the
   cross-process single-writer guard all verified.
3. **psycopg2 dependency** — fixed; clean-environment install + driver import +
   SQLAlchemy/Alembic sync-path resolution verified.
4. **Bootstrap hardening** — fixed; live PostgreSQL run shows the `REVOKE`s
   survive extension failure, plus a focused unit test for the control flow.

## CODE FIXED BUT LIVE INFRASTRUCTURE NOT AVAILABLE

None of these four fixes depend on infrastructure that was unavailable — each
was verified against a real PostgreSQL/asyncpg, the real embedded store, and a
real clean install. The only infrastructure still not available in this sandbox
remains **Neo4j and MinIO**, which these fixes do not touch. The one caveat is
that the PostgreSQL used for verification was pgserver-bundled rather than the
production deployment cluster; the driver, SQLAlchemy engine/pool construction,
and transaction semantics are identical.

**No commits or pushes were made.**
