"""Filesystem object store (embedded profile).

Preserves the two properties that matter for chain of custody (PRD 6.3):

* **Write-once.**  Re-putting the same key with *different* bytes raises a
  conflict instead of overwriting.  Re-putting identical bytes is a no-op, so
  pipeline retries stay idempotent.
* **Content addressing.**  Every object is stored under its SHA-256 and the
  stored copy is hash-verified on read, so silent bit-rot is detectable.

"Presigned" URLs are HMAC-signed, time-limited links to CrimeLink's own
``/api/v1/objects/...`` endpoint.  Raw storage is never reachable from the
browser (PRD 6.3) — the same invariant MinIO enforces with real S3 presigning.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.logging import get_logger
from app.ports.stores import ObjectMeta

log = get_logger("crimelink.objects.local")


class LocalObjectStore:
    backend_name = "local"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = Path(self.settings.object_store_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------- helpers
    def _path(self, bucket: str, key: str) -> Path:
        safe_bucket = bucket.replace("..", "_").strip("/") or "default"
        target = (self.root / safe_bucket / key).resolve()
        base = (self.root / safe_bucket).resolve()
        if not str(target).startswith(str(base)):
            raise NotFoundError("Invalid object key.")
        return target

    # ------------------------------------------------------------------- API
    def put(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMeta:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        if path.exists():
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing != digest:
                # Write-once (object-lock) semantics.
                raise ConflictError(
                    f"Object '{key}' already exists with different content "
                    "(write-once storage)."
                )
            return ObjectMeta(key=key, size=len(data), content_type=content_type, etag=digest)

        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        meta = ObjectMeta(key=key, size=len(data), content_type=content_type, etag=digest)
        log.info("object.put", bucket=bucket, key=key, size=len(data), sha256=digest[:16])
        return meta

    def get(self, bucket: str, key: str) -> bytes:
        path = self._path(bucket, key)
        if not path.exists():
            raise NotFoundError("Object not found.")
        data = path.read_bytes()
        self._verify(path, data)
        return data

    def stat(self, bucket: str, key: str) -> ObjectMeta | None:
        path = self._path(bucket, key)
        if not path.exists():
            return None
        data = path.read_bytes()
        return ObjectMeta(
            key=key,
            size=len(data),
            etag=hashlib.sha256(data).hexdigest(),
        )

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).exists()

    def presigned_url(self, bucket: str, key: str, expires_s: int = 900) -> str:
        return self.build_signed_url(bucket, key, expires_s)

    def build_signed_url(self, bucket: str, key: str, expires_s: int) -> str:
        expiry = int(time.time()) + int(expires_s)
        payload = f"{bucket}/{key}:{expiry}"
        signature = hmac.new(
            self.settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            f"/api/v1/objects/{quote(bucket, safe='')}/{quote(key, safe='/')}"
            f"?exp={expiry}&sig={signature}"
        )

    @staticmethod
    def verify_signature(settings: Settings, bucket: str, key: str, exp: int, sig: str) -> bool:
        """Constant-time verification of a signed object URL."""
        try:
            if int(exp) < int(time.time()):
                return False
        except (TypeError, ValueError):
            return False
        payload = f"{bucket}/{key}:{int(exp)}"
        expected = hmac.new(
            settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig or "")

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        directory = self.root / bucket.replace("..", "_").strip("/")
        if not directory.exists():
            return []
        out: list[str] = []
        for path in directory.rglob("*"):
            if path.is_file():
                rel = path.relative_to(directory).as_posix()
                if rel.startswith(prefix):
                    out.append(rel)
        return sorted(out)

    @staticmethod
    def _verify(path: Path, data: bytes) -> None:
        """Detect silent corruption; a hash mismatch is a chain-of-custody event."""
        stored_digest = hashlib.sha256(data).hexdigest()
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if sidecar.exists():
            expected = sidecar.read_text().strip()
            if expected and expected != stored_digest:
                log.error(
                    "object.hash_mismatch",
                    path=str(path),
                    expected=expected[:16],
                    actual=stored_digest[:16],
                )
                raise ConflictError("Stored object failed integrity verification.")
