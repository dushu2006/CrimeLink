# Monitoring & Alerting — Priority 1.4

## Overview

CrimeLink now exposes Prometheus metrics and ships with a Prometheus + Grafana stack. The real registry is `backend/app/services/metrics.py` (not `app/monitoring/metrics.py` — that path was a guess in early docs). The production compose now actually scrapes it.

**Verification checklist (critical):** Every alert in `alert_rules.yml` must reference a metric that is actually emitted. Before this fix, two alerts (`AuditChainVerificationFailed`, `DatabaseHealthUnhealthy`) referenced metrics that didn't exist — they would silently never fire. This has been fixed (see Verification section below).

### What is scraped

`infra/monitoring/prometheus.yml`:

- Global scrape interval 15s
- `api:8000/api/v1/metrics` — **real endpoint** (see `backend/app/api/v1/health.py` `GET /metrics` and `main.py` prefix `/api/v1`). Earlier doc said `/health/metrics` which was wrong — fixed to `/metrics`.
- Rule file `alert_rules.yml`

### Metrics Available — Verified Against Code

All metrics below are defined in `backend/app/services/metrics.py` REGISTRY and actually emitted:

- `crimelink_documents_processed_total` — Counter, label `document_type`, incremented in pipeline
- `crimelink_documents_failed_total` — Counter, label `document_type`
- `crimelink_quarantine_documents` — Gauge, docs that failed parsing, set in `refresh_gauges()` querying `CaseDocument.quarantined`
- `crimelink_pattern_queue_new` — Gauge, patterns needing review, set in `refresh_gauges()` querying `DetectedPattern status NEW`
- `crimelink_er_queue_pending` — Gauge, entity resolution queue depth, `EntityResolutionItem PENDING` count
- `crimelink_er_queue_oldest_item_hours` — Gauge, oldest item age (SLA = 48h), computed in `refresh_gauges()`
- `crimelink_graph_nodes`, `crimelink_graph_edges` — Gauge, from `graph_store.stats()`
- `crimelink_api_requests_total{method, path, status}` — Counter, emitted in `main.py` middleware `trace_and_metrics`
- `crimelink_api_request_duration_seconds_bucket` — Histogram, same middleware, `_bucket` series auto-generated
- `crimelink_audit_rows` — Gauge, `AuditLog` count, set in `refresh_gauges()`
- `crimelink_audit_verification_failures_total` — Counter, **NEWLY ADDED**, incremented in `audit/service.py` `verify()` and `verify_async()` on failure (should always be 0)
- `crimelink_db_health_ok` — Gauge 0/1, **NEWLY ADDED**, set in `refresh_gauges()` based on `graph_ok && db_ok` (1 if both succeed, 0 otherwise)
- Rate limiting: 429 responses are visible via `api_requests_total{status="429"}` because `RateLimitError` returns HTTP 429 (verified in `errors.py`)

### Alert Rules

`infra/monitoring/alert_rules.yml` groups:

#### `crimelink.pipeline`
- **CeleryQueueGrowing** — `crimelink_er_queue_pending > 100` for 10m → warning. Docs stuck in pipeline.
- **QuarantineSpike** — `crimelink_quarantine_documents > 20` for 10m → warning. Parsing failures.
- **PatternQueueBacklog** — `crimelink_pattern_queue_new > 50` for 30m → warning. Patterns not reviewed.

#### `crimelink.er_sla`
- **EntityResolutionSLABreach** — `crimelink_er_queue_oldest_item_hours > 48` for 15m → critical. ER queue SLA breach (PRD 7.3). Needs manual triage or scaling worker concurrency.
- **ERQueueHigh** — `crimelink_er_queue_pending > 50` for 15m → warning.

#### `crimelink.api`
- **HighAPIErrorRate** — `rate(crimelink_api_requests_total{status=~"5.."}[5m]) > 0.05` → critical.
- **APILatencyHigh** — p95 latency `histogram_quantile(0.95, rate(crimelink_api_request_duration_seconds_bucket[5m])) > 2` seconds for 10m → warning.
- **RateLimitingHigh** — `rate(crimelink_api_requests_total{status="429"}[5m]) > 20` → warning. Possible credential stuffing or misconfigured client.

#### `crimelink.audit`
- **AuditChainVerificationFailed** — `increase(crimelink_audit_verification_failures_total[5m]) > 0` → critical. G3 audit chain broken — stop all writes, investigate.
- **AuditLogGrowthStalled** — if metrics endpoint up but no audit growth for 1h (placeholder, to be refined when audit counter metric exists).

#### `crimelink.infra`
- **DatabaseHealthUnhealthy** — `crimelink_db_health_ok == 0` for 5m → critical.
- **GraphNodesUnusualDrop** — `crimelink_graph_nodes` drops >50% in 10m → critical. Possible accidental wipe or corruption.

### Grafana

`docker-compose.yml` now includes:

- `prometheus` service: `prom/prometheus:v2.53.2`, volumes for config + rules, data volume `prometheus-data`, port `${CRIMELINK_PROMETHEUS_PORT:-9090}`
- `grafana` service: `grafana/grafana:11.2.2`, provisioned datasource `Prometheus` pointing to `http://prometheus:9090`, dashboard provider from `infra/monitoring/grafana/dashboards/`, port `${CRIMELINK_GRAFANA_PORT:-3000}`

Provisioning:
- `infra/monitoring/grafana/datasources/datasource.yml` — Prometheus datasource
- `infra/monitoring/grafana/dashboards/dashboard.yml` — file provider
- `infra/monitoring/grafana/dashboards/crimelink.json` — operational overview dashboard with:
  - Documents processed/failed rate
  - ER queue depth + oldest item
  - API latency p95
  - API requests by status
  - Graph nodes/edges
  - Pattern queue + quarantine

Access: `http://localhost:3000` (admin/admin by default, change via `CRIMELINK_GRAFANA_PASSWORD`)

### Running

```bash
docker compose up -d prometheus grafana
# Check Prometheus targets: http://localhost:9090/targets
# Check Grafana: http://localhost:3000
```

### Future Improvements (Priority 2/3)

- Add `crimelink_celery_queue_length` metric per queue (pipeline, analytics, maintenance) — requires instrumenting Celery broker
- Add `crimelink_audit_log_total` counter to detect growth stall
- Add alert for backup failures (when backup job exists)
- Add alert for TLS cert expiry (when mTLS internal is implemented)
- Wire Alertmanager for Slack/email/PagerDuty — currently rules are evaluated but no notifier; add `alertmanager` service to compose
- Add SLO dashboard: 99.9% availability, p95 latency <500ms for non-AI routes, AI routes <30s

### Relationship to Other Priorities

- **1.2 Rate limiting**: Redis-backed sliding window prevents credential stuffing; monitoring of 429 rate catches abuse
- **1.3 Secrets**: Prometheus/Grafana credentials should also come from Vault in production
- **2.1 Scanning**: Trivy image scans run in CI, but runtime CVE monitoring could be added via Grafana annotation of image SHA
- **3.1 Load testing**: k6/Locust results can be pushed to Prometheus or visualized in Grafana
