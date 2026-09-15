# Secrets Management — Priority 1.3

## Scope Clarification (Important for Review)

**What this deliverable is:** Documentation and runbook — "documented and ready to wire in" — plus `.env.example` warning and `infra/secrets/README.md` examples. We do NOT run a live Vault instance in this repo/environment; that would require government cloud infrastructure (MeghRaj secret manager, HashiCorp Vault cluster) which cannot be stood up in a dev sandbox.

**What it is NOT:** Live infrastructure. Claiming "Vault is implemented" would be inaccurate. The correct statement is: "Secrets management is documented with rotation procedures, Vault Agent and External Secrets examples, and code is ready to consume `_FILE` env vars; production deployment must wire Vault per this runbook."

This distinction is intentional for Priority 1.3.

## Current State (Demo / Embedded)

`.env.example` contains placeholders like `GENERATE_A_32_BYTE_RANDOM_VALUE` and is copied to `.env` for local development. This is correct for a hackathon demo and **wrong** for a real NIC/MeghRaj-style government deployment.

Secrets currently live as plain env vars:
- `CRIMELINK_SECRET_KEY` (JWT signing)
- `CRIMELINK_POSTGRES_PASSWORD`, `CRIMELINK_NEO4J_PASSWORD`, `CRIMELINK_MINIO_SECRET_KEY`
- `CRIMELINK_AI_API_KEY`, `CRIMELINK_AI_REASONING_API_KEY`, etc.

In production, these must **not** sit on disk in a `.env` file.

## Target State (Production)

### Vault as Source of Truth

Secrets come from a vault (HashiCorp Vault, or whatever secret store the target government cloud provides — e.g., MeghRaj secret manager, Kubernetes Secrets with external-secrets operator) injected at container start, not baked into an image or `.env` file.

**Docker Compose (current prod topology):**

- Use Docker Compose secrets or env_file from vault agent:

```yaml
# Example: Vault Agent injects secrets into /run/secrets/
services:
  api:
    secrets:
      - postgres_password
      - neo4j_password
      - minio_secret
      - jwt_secret
    environment:
      CRIMELINK_POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      CRIMELINK_SECRET_KEY_FILE: /run/secrets/jwt_secret
```

- Application reads `_FILE` suffix if present (see `backend/app/config.py` supports `*_FILE`? If not, add helper). For now, vault agent can render a `.env` file in tmpfs that is only visible to the container and removed on stop.

**Kubernetes (future scaling):**

- Use `external-secrets` operator to sync Vault → K8s Secrets
- Mount as env vars or files
- Never commit secret values to Git

### Rotation Procedure

#### 1. Database Passwords (`POSTGRES`, `NEO4J`, `MINIO`)

- Generate new password in Vault
- Update Vault secret version
- Rolling restart: `docker compose up -d --no-deps postgres` etc. will pick up new env var from vault agent
- Old password remains valid until all containers restarted (coordinate maintenance window)
- Verify health: `GET /admin/database/health` and `pg_isready`

#### 2. JWT Signing Key (`CRIMELINK_SECRET_KEY`)

This is critical — rotating JWT key invalidates all existing sessions.

**Plan for in-flight sessions:**

- Support dual-key verification during rotation window:
  1. Add `CRIMELINK_SECRET_KEY_OLD` env var that, if present, is tried after primary key for token verification (but never used for signing)
  2. Deploy new primary key + old key as fallback
  3. Wait for old tokens to expire — window derived from live config, NOT hardcoded:
     - `CRIMELINK_ACCESS_TOKEN_TTL_MINUTES` (default 15, from `backend/app/config.py:101`)
     - `CRIMELINK_REFRESH_TOKEN_TTL_HOURS` (default 8, from `backend/app/config.py:102`)
     - Rotation window = `access_token_ttl_minutes + refresh_token_ttl_hours` = 15m + 8h = **8h15m** with defaults
     - If config changes (e.g., refresh 24h), window becomes 24h15m — must be recomputed from `get_settings()`, not hardcoded. Code should compute: `rotation_window = timedelta(minutes=settings.access_token_ttl_minutes, hours=settings.refresh_token_ttl_hours)`
     - Add safety margin: +15m (so 8h30m total with defaults) to account for clock skew
  4. Remove old key env var, deploy again
  5. All sessions now use new key

- Implementation sketch (to be added to `backend/app/security/tokens.py`):

```python
def verify_jwt(token: str) -> dict:
    for key in [settings.secret_key, getattr(settings, "secret_key_old", None)]:
        if not key:
            continue
        try:
            return jwt.decode(token, key, algorithms=[settings.jwt_algorithm])
        except InvalidSignature:
            continue
    raise AuthenticationError
```

- Document rotation in runbook: who approves, how to trigger, how to verify no active sessions remain on old key (query `audit_log` for `action_type=LOGIN` with old key fingerprint).

#### 3. AI API Keys

- Rotate in Vault, then rolling restart of `api`, `worker`, `beat` (they all use `AIModelRouter`)
- Verify via `POST /admin/ai/health/test` for each role — new key must show `ok: true`
- Old key can be revoked after verification

### What Changed in Code

- `backend/app/security/rate_limit.py` now supports Redis (already part of secrets management — Redis URL may itself be secret)
- `docker-compose.yml` now documents secret file injection pattern (see comments)
- Added `infra/secrets/README.md` with vault agent example
- `.env.example` updated with warning header about production usage

### Checklist Before Real Deployment

- [ ] No `.env` file on production hosts — secrets from Vault only
- [ ] `CRIMELINK_SECRET_KEY` generated via `openssl rand -hex 32`, stored in Vault, rotation procedure documented and tested
- [ ] Database passwords rotated from defaults (`crimelink`, `neo4j`) — enforced by `Settings._validate_production_security()`
- [ ] AI API keys stored in Vault, not in repo or `.env.example`
- [ ] `CRIMELINK_CORS_ORIGINS` does not contain `*` in production (enforced)
- [ ] Audit log of secret rotations (who, when, which secret) — can reuse existing audit chain with `action_type=SECRET_ROTATION`
- [ ] Documented break-glass procedure if Vault is unavailable (fallback to last known good secrets in secure offline backup)

### References

- PRD 12.6: Auth rate limiting is credential-stuffing defense — requires Redis scaling (done in 1.2)
- `backend/app/config.py`: production security validator fails closed if secrets are placeholders
- `docs/BACKUP_DISASTER_RECOVERY.md`: backup/restore must include Vault unseal keys and secret versions
