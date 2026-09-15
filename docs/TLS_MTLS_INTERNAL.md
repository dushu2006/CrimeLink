# TLS & mTLS Internal — Priority 2.4

## External TLS (Current)

`web` container (nginx) serves React console and proxies `/api` to `api:8000`. TLS is enabled by dropping `fullchain.pem` and `privkey.pem` into `infra/tls/` (mounted read-only to web).

`docker-compose.yml` comment says uncomment `443:443` after placing certs. Nginx config (in `frontend/Dockerfile` or nginx.conf) should:

- Listen 80 → redirect to 443 when certs present
- Listen 443 ssl with `ssl_certificate /etc/nginx/tls/fullchain.pem`
- Use TLS 1.2+ only, strong ciphers, HSTS

This is sufficient for external traffic.

## Internal mTLS (Future, Priority 2.4)

Goal: encrypt and authenticate traffic inside Docker network (or K8s) so compromised container cannot sniff or impersonate.

### Threat Model

- Attacker gains shell in `web` container — should NOT be able to query postgres directly without client cert
- Attacker on host network — should NOT see plaintext postgres password, neo4j traffic, redis commands

### Design Options

#### Option A: Simple TLS (no client auth) — Minimal

- Enable TLS on postgres (`ssl=on`), neo4j (`dbms.ssl.policy.bolt.enabled=true`), redis (`tls-port`), minio (already supports TLS)
- Mount CA + server certs via Docker secrets or volume
- Api/worker/beat use `sslmode=require` or equivalent
- Pros: easy, encrypts traffic
- Cons: no mutual auth, still need password

#### Option B: mTLS with Client Certs — Recommended for Government

- Internal CA (private) issues certs for each service
- Each service presents client cert when connecting
- Server verifies client cert against CA
- Example for postgres: `ssl_ca_file`, `ssl_cert_file`, `ssl_key_file`, `ssl_crl_file`, `hostssl` in pg_hba.conf with `clientcert=verify-full`
- For redis: `tls-ca-cert-file`, `tls-cert-file`, `tls-key-file`, `tls-auth-clients yes`
- For neo4j: `dbms.ssl.policy.bolt.client_auth=REQUIRE`
- Application: mount client cert + key, configure DSN with `sslcert`/`sslkey` or env vars

#### Option C: Service Mesh (Istio/Linkerd) — Future K8s

- Explicitly out of scope per instructions ("K8s/service mesh" left alone)
- When K8s is adopted, mesh can provide mTLS automatically without app changes
- Tracked as Priority 4+ future

### Implementation Plan (Doc Only, No Code Now)

1. **Generate internal CA** (offline, stored in Vault):
   ```bash
   openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 3650 -subj "/CN=crimelink-internal-ca"
   ```

2. **Issue certs per service** (api, worker, beat, postgres, neo4j, redis, minio):
   ```bash
   openssl req -newkey rsa:2048 -keyout api.key -out api.csr -subj "/CN=api"
   openssl x509 -req -in api.csr -CA ca.crt -CAkey ca.key -out api.crt -days 365
   ```

3. **Distribute via Vault** — never commit private keys. Vault Agent renders to `/run/secrets/` with 0400 perms.

4. **Configure services**:
   - Postgres: `postgresql.conf` ssl on, `pg_hba.conf` hostssl with clientcert
   - Redis: `redis.conf` tls-* directives
   - Neo4j: `neo4j.conf` ssl policy
   - MinIO: `MINIO_CERTS_DIR`

5. **Configure clients** (api/worker/beat):
   - `CRIMELINK_POSTGRES_DSN` includes `?sslmode=verify-full&sslcert=/run/secrets/pg_client.crt&sslkey=/run/secrets/pg_client.key&sslrootcert=/run/secrets/ca.crt`
   - Similar for redis, neo4j, minio

6. **Rotate**: certs 90-day expiry, automated via Vault PKI, rolling restart

### Current State

- External TLS documented, internal plaintext (acceptable for single-host docker-compose behind firewall, NOT for multi-host or K8s)
- This doc satisfies Priority 2.4 planning — no code change now

### Checklist Before Prod with mTLS

- [ ] CA generated offline, stored in Vault, backup in secure safe
- [ ] Certs issued per service, 90-day expiry, auto-renewal
- [ ] Services configured to require client cert
- [ ] Clients configured with certs from Vault
- [ ] Monitoring: alert on cert expiry <30 days (`x509_cert_expiry` metric or blackbox exporter)
- [ ] Documented break-glass: how to disable mTLS if CA unavailable (emergency env var)
