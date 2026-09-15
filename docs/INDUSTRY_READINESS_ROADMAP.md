# CrimeLink Industry-Readiness Roadmap — Status

Derived from user request 2026-09-15. This document tracks operational maturity work without touching domain/pipeline/analytics core (blockchain audit hash-chain, GraphInjector, entity_resolution.py are left alone).

## Priority 1 — Foundation (CI/CD, Rate Limiting, Secrets, Monitoring)

| Item | Status | Evidence |
|------|--------|----------|
| **1.1 CI/CD** | ✅ Done | `.github/workflows/ci.yml` with 6 jobs: backend-lint (ruff check+format 0.11.2), backend-test (pip install -e backend[dev] + pytest), frontend-typecheck (tsc), frontend-test (npm test), frontend-build (vite build + artifact), docker-build (buildx backend/frontend SHA tags). Concurrency cancel-in-progress, pip/npm/gha cache. |
| **1.2 Redis Rate Limiting** | ✅ Done | `backend/app/security/rate_limit.py` rewritten from pure deque to Redis sorted-set sliding window `crimelink:ratelimit:{key}` with ZREMRANGEBYSCORE, ZCARD, ZADD, EXPIRE 2×window. In-process fallback when `effective_broker_backend==inline` or Redis down. 5s circuit breaker. `reset()` clears both memory and Redis via scan_iter. Call site unchanged. Existing auth tests still pass. |
| **1.3 Secrets Management** | ✅ Done docs, 🔧 infra pending prod | `docs/SECRETS_MANAGEMENT.md` + `infra/secrets/README.md` document Vault Agent and External Secrets patterns, rotation procedure for `CRIMELINK_SECRET_KEY` with dual-key verification window, DB passwords, AI keys. `.env.example` updated with production warning. Code change still needed: support `*_FILE` env vars in `config.py` (future). |
| **1.4 Monitoring/Alerting** | ✅ Done | `infra/monitoring/prometheus.yml` scrapes `api:8000/api/v1/health/metrics` 15s, `infra/monitoring/alert_rules.yml` 5 groups: pipeline (queue growing, quarantine spike, pattern backlog), er_sla (48h critical), api (5xx>5%, p95>2s, 429>20), audit (chain verification failed critical), infra (DB unhealthy, graph nodes drop). `docker-compose.yml` now includes prometheus `prom/prometheus:v2.53.2` and grafana `grafana/grafana:11.2.2` with volumes for configs, data volumes, ports 9090/3000. Grafana provisioning datasource + dashboard provider, `crimelink.json` dashboard with 8 panels. `docs/MONITORING.md` runbook. |

## Priority 2 — Hardening (Scanning, Pentest, Retention, TLS)

| Item | Status | Evidence |
|------|--------|----------|
| **2.1 Dependency/Container CVE Scanning** | ✅ Done | `.github/dependabot.yml` weekly for pip, npm, docker backend/frontend, github-actions. `.github/workflows/security.yml` with pip-audit, npm-audit, Trivy fs + image scans, SARIF upload via `codeql-action/upload-sarif@v3`, weekly cron. |
| **2.2 Pentest / CERT-In Note** | 📝 Doc needed | See `docs/PENTEST_CERTIN_NOTE.md` — outlines what a CERT-In empaneled audit would check, current posture, gaps. No code change needed now. |
| **2.3 Data Retention / Legal Hold** | 📝 Doc needed | See `docs/DATA_RETENTION_LEGAL_HOLD.md` — retention job design, legal hold flag, `CRIMELINK_RETENTION_DAYS_AFTER_CLOSURE` usage. |
| **2.4 TLS mTLS Internal** | 📝 Doc needed | See `docs/TLS_MTLS_INTERNAL.md` — nginx terminates external TLS, internal mTLS between api↔postgres, api↔neo4j, api↔redis, worker↔broker via sidecar or service mesh later (explicitly out of scope for now, doc only). |

## Priority 3 — Scaling & Resilience

| Item | Status | Evidence |
|------|--------|----------|
| **3.1 Load Testing** | 📝 Doc needed | See `docs/LOAD_TESTING.md` — k6/Locust scenarios for pipeline throughput, AI gateway latency, ER queue SLA. |
| **3.2 DB Scaling Triggers** | 📝 Doc needed | See `docs/DB_SCALING_TRIGGERS.md` — when to add read replicas, connection pool tuning, Neo4j heap/pagecache sizing. |
| **3.3 Backup/Restore Drill** | ✅ Partial | `docs/BACKUP_DISASTER_RECOVERY.md` exists; needs drill runbook added. |

## Priority 4 — Experience (Out of scope for now)

- Multi-lang UI, accessibility, onboarding tutorials — tracked but not started.

## Explicitly Left Alone (Per Instructions)

- Blockchain audit hash-chain — correct, no changes
- K8s/service mesh — not now
- Vector search — only if Phase 1-3 AI latency insufficient (it was sufficient)
- GraphInjector, entity_resolution.py — no changes
- PII stripping in AI Gateway — must never send raw doc identifiers to model

## Next Steps for Priority 1 Closure

- [x] Add prometheus+grafana to compose
- [x] Create grafana dashboard json
- [x] Create SECRETS_MANAGEMENT.md and MONITORING.md
- [x] Verify rate_limit change doesn't break auth tests (`test_client_ip.py`, `test_api_contract.py` passed)
- [ ] Run full pytest suite (backend) — 1 job in CI will do this
- [ ] Commit/push
- [ ] Manual verification: `docker compose up -d prometheus grafana` and check targets
