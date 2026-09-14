# Backup and disaster-recovery runbook

This is the operational runbook for a deployment team. It is a design and
procedure document, not a claim that restoration has been production-tested in
this checkout.

## Targets

- **RPO:** 15 minutes for PostgreSQL WAL/PITR where the operator enables WAL
  archiving; otherwise the last successful scheduled backup.
- **RTO:** 4 hours for a complete service restore, subject to storage and
  database availability.
- **Audit:** audit-chain head and an external anchor must be recoverable with
  the evidence store.

## PostgreSQL

1. Enable encrypted base backups and WAL archiving to a separate protected
   bucket/host.
2. Take a daily full backup and verify its checksum.
3. Retain daily backups for the configured legal-retention period and keep
   immutable copies for the audit retention period.
4. Record backup completion, source LSN, checksum, and operator in an
   operational log outside the application database.
5. For restore: stop API/workers, restore the base backup, replay WAL to the
   target timestamp, run `alembic upgrade head`, then run the application
   health/readiness checks.
6. Verify row counts, the audit chain (`GET /api/v1/admin/audit/verify`), and
   a representative evidence SHA-256 before reopening access.

## Neo4j

Use Neo4j's supported backup/snapshot mechanism for the deployed edition.
Back up the database and transaction logs on a schedule consistent with the
PostgreSQL RPO. Restore Neo4j before enabling graph-dependent workflows, then
rebuild/compare the active dataset projection if the graph snapshot and
relational metadata disagree. Never silently accept a graph that contains a
second dataset.

## Object storage

Enable versioning, server-side encryption, replication to a separate failure
domain, and object-lock/immutability for original evidence and audit anchors
where the deployment policy permits. Do not overwrite an original object key.
The local embedded object store is write-once and hash-verifies reads, but is
not a substitute for replicated production storage.

## Audit anchors

The scheduled anchor job writes the audit-chain head to the separate audit
anchor bucket. Back up that bucket independently. During restore, compare the
latest restored `AuditChainHead` with the latest external anchor; any mismatch
is a security incident requiring investigation, not an automatic repair.

## Restore verification checklist

- [ ] Application starts with production security settings and no placeholders.
- [ ] PostgreSQL, Neo4j, Redis and MinIO readiness checks pass.
- [ ] Active dataset ID is the expected one.
- [ ] Case classification and jurisdiction filtering work for a test user.
- [ ] Evidence object hashes match their `CaseDocument` hashes.
- [ ] Custody events are present and append-only.
- [ ] Audit chain validates and external anchor matches.
- [ ] A report draft's evidence index contains hashes and does not approve itself.
- [ ] A supervisor can approve a report only through the approval workflow.
- [ ] The restored environment is isolated from real operational systems until
      the verification record is signed off.

## Legal hold and retention

Retention automation must query the legal-hold source before any deletion
approval. Sealed cases and held evidence are never eligible for automatic
removal. Deletion (where policy permits it) must be an approved, audited,
versioned operation; this repository deliberately has no general DELETE API.
