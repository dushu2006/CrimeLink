# Data Retention & Legal Hold — Priority 2.3

## Requirement

`CRIMELINK_RETENTION_DAYS_AFTER_CLOSURE=90` exists in config but no job enforces it yet. Need a job that deletes or archives data after case closure + retention period, with legal hold override.

## Design (No Core Domain Changes)

### Data Model Additions (Future)

- `cases.legal_hold` boolean, default false, settable only by admin or investigator with justification
- `cases.closed_at` timestamp, set when case status → closed
- `cases.retention_expires_at` = closed_at + retention_days, recomputed on closure
- Audit log entry for legal hold set/unset with `action_type=LEGAL_HOLD` and actor

### Job: `retention_enforcement`

Celery beat schedule: daily 02:00 IST (nightly, like pattern detection).

Pseudocode:

```python
@celery_app.task
def retention_enforcement():
    now = utcnow()
    cases = db.query(Case).filter(
        Case.status == "closed",
        Case.legal_hold == False,
        Case.retention_expires_at < now,
    ).all()
    for case in cases:
        if has_active_legal_hold(case.id):
            continue
        # Archive to cold storage (MinIO bucket with Glacier or separate bucket)
        archive_case(case.id)
        # Delete operational data: documents, graph nodes, jobs
        # Keep audit log (never delete audit chain)
        soft_delete_case_data(case.id)
        audit(action_type="RETENTION_ENFORCEMENT", case_id=case.id, actor="system")
```

### Legal Hold

- When `legal_hold=True`, retention job skips case regardless of expiry
- UI: case detail shows legal hold banner, admin can set/unset with reason
- API: `POST /cases/{id}/legal-hold` {hold: bool, reason: str} — admin only
- Audit: every hold/unhold is audited with reason
- Search: filter for legal hold cases in admin dashboard

### Archive vs Delete

- **Archive**: copy documents from MinIO `documents` bucket to `archive` bucket (write-once, versioned, cheaper storage), export graph subgraph as JSON, dump case metadata
- **Delete**: remove from operational tables, but keep audit log rows (audit chain must never lose entries)
- Config: `CRIMELINK_RETENTION_ARCHIVE_ENABLED=true` to archive before delete, `CRIMELINK_RETENTION_ARCHIVE_BUCKET=archive`

### Compliance Notes

- Indian Evidence Act: electronic evidence must be preserved if under legal hold or active investigation
- Never delete audit log — it is the chain of custody
- Backup retention must be >= operational retention (see BACKUP_DISASTER_RECOVERY.md)
- Document retention policy in user-facing help: "Cases closed >90 days ago are archived unless legal hold"

### Implementation Steps (Priority 2, not now)

1. Add migration for `legal_hold`, `closed_at`, `retention_expires_at` on cases table
2. Add Celery beat task `retention_enforcement` with dry-run mode
3. Add admin API for legal hold
4. Add UI banner
5. Add audit action type
6. Add tests: retention skips legal hold, retention archives then deletes, audit entry created
7. Document in runbook

### Current State

- Config exists, job does not — tracked as future work
- No data is auto-deleted today (safe default)
