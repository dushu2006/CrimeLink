# DB Scaling Triggers — Priority 3.2

## Postgres

Current: single `postgres:15.6-alpine` container, `max_connections=200`, pool `10 + 20 overflow` per api/worker.

**Triggers to scale:**

- `pg_stat_activity count > 150` for 10m → increase `max_connections` to 300, pool to 20+30, or add PgBouncer
- `crimelink_api_request_duration_seconds` p95 >1s correlated with `pg_stat_database.xact_commit` low → DB CPU bound, vertical scale host or add read replica for analytics queries
- Disk >80% (`postgres-data` volume) → extend volume, archive old audit anchors to MinIO, or implement retention job
- WAL growth >10GB → check `synchronous_commit=on` still required, but add archive to MinIO

**Read Replica Plan (Future K8s):**

- One primary, one replica for reporting (`/analytics`, `/admin/database/health`)
- Application: `CRIMELINK_POSTGRES_DSN_REPLICA` for read-only queries, fallback to primary if replica lag >5s
- Replication lag metric: `pg_replication_lag_seconds` → alert if >10s

## Neo4j

Current: `neo4j:5.26.0-community`, heap 2G, pagecache 1G, single instance.

**Triggers:**

- `crimelink_graph_nodes > 1M` or `crimelink_graph_edges > 5M` → heap 2G→4G, pagecache 1G→2G, monitor GC pauses
- `crimelink_graph_nodes` unusual drop >50% → critical alert (possible corruption, already in alert_rules.yml)
- Query latency `MATCH (p:Person)-[*1..2]->()` p95 >2s → add index on frequently filtered props, or consider Enterprise + GDS for centrality (currently computed in Python)
- Disk >80% (`neo4j-data`) → compact, or add volume

**Scaling Options:**

- Community is single-instance only — for HA, need Enterprise causal cluster (3 core servers) — out of scope now, doc only
- Sharding by case: keep each case subgraph in separate DB (Neo4j multi-database) when >10M nodes

## Redis

Current: `redis:7.2.5-alpine`, `--appendonly no`, `maxmemory-policy noeviction`, single instance.

**Triggers:**

- Memory >80% of container limit → increase limit, or set `maxmemory 2gb` + `allkeys-lru` for rate limiting keys (they have TTL, safe to evict)
- `crimelink_er_queue_pending > 200` for 30m → Redis not bottleneck, but worker concurrency 2 may be low → increase to 4, or add second worker container with same queues
- Rate limiting: Redis latency >10ms (measured via `redis-cli --latency`) → check host CPU, or add dedicated Redis for rate limiting vs Celery broker (currently broker 1, backend 2, rate limiting uses 0 — separate DBs already)

**HA:**

- Redis Sentinel or Cluster for broker HA — not now, but Celery tasks are re-dispatchable from jobs table (doc in compose comments)

## MinIO

Current: single `minio:RELEASE.2024-11-07` container, single disk.

**Triggers:**

- Disk >80% → add volume, or lifecycle policy to archive to cold storage
- Upload failures >5% → check `CRIMELINK_UPLOAD_MAX_BYTES`, network, MinIO logs
- For prod: MinIO distributed mode (4 nodes, erasure coding) for durability — out of scope for single-host compose

## Celery Worker

Current: concurrency 2, queues `pipeline,analytics,maintenance`, `max-tasks-per-child=50`

**Triggers:**

- `crimelink_er_queue_pending` growing (alert CeleryQueueGrowing) → increase concurrency 2→4, or add second worker container
- Worker OOMKilled → reduce concurrency, increase memory limit, check for large doc parsing (PDF with 1000 pages)
- Task latency `pipeline.*` >5m → profile NLP provider, check `CRIMELINK_NLP_PROVIDER` and `CRIMELINK_NIM_CONCURRENCY`

## When to Add K8s

Per instructions, K8s/service mesh is explicitly left alone for now. Triggers to reconsider:

- Single host CPU >80% sustained, or need >1 worker host
- Need zero-downtime deploy (rolling update)
- Need autoscaling based on queue depth (KEDA)
- Government cloud mandates K8s

Then: move from compose to Helm chart, add HPA on `crimelink_er_queue_pending`, use external-secrets for Vault, service mesh for mTLS.

## Metrics to Watch (Already in Grafana)

- `crimelink_documents_processed_total` rate
- `crimelink_er_queue_pending`, `crimelink_er_queue_oldest_item_hours`
- `crimelink_pattern_queue_new`, `crimelink_quarantine_documents`
- `crimelink_graph_nodes`, `crimelink_graph_edges`
- `crimelink_api_request_duration_seconds_bucket` p95
- `crimelink_db_health_ok`
