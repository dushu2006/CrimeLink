# Secrets Injection — Vault Example (Priority 1.3)

This directory documents how to inject secrets from Vault in production, instead of using `.env` on disk.

## HashiCorp Vault Agent Example

`vault-agent.hcl`:

```hcl
auto_auth {
  method "approle" {
    config = {
      role_id_file_path = "/etc/vault/role-id"
      secret_id_file_path = "/etc/vault/secret-id"
    }
  }
}

template {
  source      = "/etc/vault/templates/crimelink.env.ctmpl"
  destination = "/run/crimelink/secrets.env"
  perms       = 0400
}

# Optional: render individual secret files for _FILE support
template {
  source      = "/etc/vault/templates/postgres_password.ctmpl"
  destination = "/run/secrets/postgres_password"
  perms       = 0400
}
```

`crimelink.env.ctmpl`:

```
CRIMELINK_POSTGRES_PASSWORD={{ with secret "secret/crimelink/postgres" }}{{ .Data.data.password }}{{ end }}
CRIMELINK_NEO4J_PASSWORD={{ with secret "secret/crimelink/neo4j" }}{{ .Data.data.password }}{{ end }}
CRIMELINK_MINIO_SECRET_KEY={{ with secret "secret/crimelink/minio" }}{{ .Data.data.secret_key }}{{ end }}
CRIMELINK_SECRET_KEY={{ with secret "secret/crimelink/jwt" }}{{ .Data.data.secret_key }}{{ end }}
CRIMELINK_AI_API_KEY={{ with secret "secret/crimelink/ai" }}{{ .Data.data.api_key }}{{ end }}
```

Docker Compose usage:

```yaml
services:
  api:
    env_file:
      - /run/crimelink/secrets.env
    # Or use _FILE pattern:
    environment:
      CRIMELINK_POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
```

## Kubernetes External Secrets Example

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: crimelink-secrets
spec:
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: crimelink-secrets
    creationPolicy: Owner
  data:
    - secretKey: postgres-password
      remoteRef:
        key: secret/crimelink/postgres
        property: password
    - secretKey: jwt-secret
      remoteRef:
        key: secret/crimelink/jwt
        property: secret_key
```

Then mount as env vars in Deployment.

## Rotation

See `docs/SECRETS_MANAGEMENT.md` for rotation procedure, including dual-key JWT verification window.

## Local Dev

For local dev (`CRIMELINK_PROFILE=embedded`), no Vault is needed. `python run.py` works with zero config. Only production needs Vault.
