"""MinIO / S3-compatible object store (production profile).

Buckets are created at boot with the required posture:

* ``documents``         — original uploads, never overwritten.
* ``documents-derived`` — extracted text, linked to the parent hash.
* ``audit-anchor``      — nightly audit head hashes, separate write credential.

**How write-once is enforced.** The application refuses to write an object whose
content differs from an existing one (``ConflictError`` raised from
:meth:`put`), and every document row stores the SHA-256 it was admitted under.
That is a real guarantee, and it is the one the tests exercise.

It is *not* yet backed by S3 object lock or a bucket retention policy: this
client does not call ``put_object_lock_configuration``, and no default retention
period is set on the bucket.  Object lock is the stronger control (it survives a
compromised application credential) and belongs in the deployment runbook — see
``docs/DEPLOYMENT.md`` for the MinIO ``mc`` commands to turn it on.

Investigator-facing links are 15-minute presigned URLs; the raw object store is
never reachable from the browser (PRD 6.3).
"""

from __future__ import annotations

import hashlib
import io

from app.config import Settings, get_settings
from app.errors import ConflictError, DependencyUnavailableError, NotFoundError
from app.logging import get_logger
from app.ports.stores import ObjectMeta

log = get_logger("crimelink.objects.minio")

try:
    from minio import Minio  # type: ignore
    from minio.error import S3Error  # type: ignore
except ImportError:  # pragma: no cover
    Minio = None  # type: ignore
    S3Error = Exception  # type: ignore


class MinioObjectStore:
    backend_name = "minio"

    def __init__(self, settings: Settings | None = None) -> None:
        if Minio is None:  # pragma: no cover
            raise DependencyUnavailableError("minio client is not installed")
        self.settings = settings or get_settings()
        # Bounded timeouts: on serverless platforms a black-holed S3 endpoint
        # must not pin the invocation until the platform kills it.  The
        # connect timeout keeps cold starts and health checks snappy; the read
        # timeout covers object listings and presign calls.
        self._client = Minio(
            self.settings.minio_endpoint,
            access_key=self.settings.minio_access_key,
            secret_key=self.settings.minio_secret_key,
            secure=self.settings.minio_secure,
            http_client=self._build_http_client(),
        )

    @staticmethod
    def _build_http_client():
        try:
            import urllib3

            timeout = urllib3.Timeout(connect=5.0, read=20.0)
            return urllib3.PoolManager(timeout=timeout, maxsize=4, retries=False)
        except ImportError:  # pragma: no cover - minio requires urllib3
            return None
        self._buckets = (
            self.settings.minio_bucket_documents,
            self.settings.minio_bucket_derived,
            self.settings.minio_bucket_audit_anchor,
        )

    def ensure_buckets(self) -> None:
        for bucket in self._buckets:
            try:
                if not self._client.bucket_exists(bucket):
                    self._client.make_bucket(bucket)
                    log.info("object.bucket_created", bucket=bucket)
            except S3Error as exc:  # pragma: no cover - infra dependent
                log.error("object.bucket_error", bucket=bucket, error=str(exc))
                raise DependencyUnavailableError("Object storage is unavailable.") from exc

    def put(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMeta:
        digest = hashlib.sha256(data).hexdigest()
        # Write-once guard: an existing object with different content is a bug or
        # a tamper attempt, never something to overwrite.
        existing = self.stat(bucket, key)
        if existing and existing.etag and existing.etag != digest:
            raise ConflictError(
                f"Object '{key}' already exists with different content (write-once storage)."
            )
        self._client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
            metadata={"sha256": digest},
        )
        return ObjectMeta(key=key, size=len(data), content_type=content_type, etag=digest)

    def get(self, bucket: str, key: str) -> bytes:
        try:
            response = self._client.get_object(bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            raise NotFoundError("Object not found.") from exc

    def stat(self, bucket: str, key: str) -> ObjectMeta | None:
        try:
            info = self._client.stat_object(bucket, key)
        except S3Error:
            return None
        return ObjectMeta(
            key=key,
            size=int(info.size or 0),
            content_type=info.content_type or "application/octet-stream",
            etag=(info.metadata or {}).get("x-amz-meta-sha256", info.etag or ""),
        )

    def exists(self, bucket: str, key: str) -> bool:
        return self.stat(bucket, key) is not None

    def presigned_url(self, bucket: str, key: str, expires_s: int = 900) -> str:
        from datetime import timedelta

        return self._client.presigned_get_object(bucket, key, expires=timedelta(seconds=expires_s))

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        try:
            return [obj.object_name for obj in self._client.list_objects(bucket, prefix=prefix)]
        except S3Error:  # pragma: no cover
            return []

    # --------------------------------------------------------------- health
    def health_check(self) -> tuple[bool, str, str | None]:
        """Honest reachability probe: ``(ok, detail, error)``.

        Actually contacts the object store (``bucket_exists`` on the documents
        bucket) and distinguishes the three failure classes that operators
        need to act on differently:

        * **config**      — the endpoint answered but the bucket is missing;
        * **auth**        — the endpoint answered but rejected the credential;
        * **connectivity** — the endpoint could not be reached at all (DNS,
          refused, timeout).  This is the class that surfaces when a compose
          hostname (``minio:9000``) is used from a serverless runtime.

        ``detail``/``error`` are sanitized: the endpoint host is useful for
        debugging, credentials never are.
        """
        from urllib.parse import urlsplit

        endpoint = self.settings.minio_endpoint
        try:
            host = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}").netloc or endpoint
        except ValueError:  # pragma: no cover
            host = endpoint
        detail = f"bucket '{self.settings.minio_bucket_documents}' @ {host}"
        try:
            if not self._client.bucket_exists(self.settings.minio_bucket_documents):
                return (
                    False,
                    detail,
                    "object store reachable but the configured bucket does not exist "
                    "(config) — create the bucket or correct CRIMELINK_MINIO_BUCKET_DOCUMENTS",
                )
            return True, detail, None
        except S3Error as exc:
            return False, detail, self._describe_s3_error(exc)
        except Exception as exc:  # network layer (urllib3 wraps DNS/timeout/refused)
            return False, detail, self._describe_network_error(host, exc)

    @staticmethod
    def _describe_s3_error(exc: Exception) -> str:
        code = str(getattr(exc, "code", "") or "")
        if code in {"NoSuchBucket", "NoSuchKey"}:
            return "object store reachable but the bucket was not found (config)"
        if code in {
            "AccessDenied",
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
            "InvalidClientTokenId",
            "AuthFailure",
        }:
            return "object store rejected the credentials (auth) — check the S3/MinIO access key and secret"
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else code or "unknown"
        return f"object store error (auth/config) {message[:200]}"

    @staticmethod
    def _describe_network_error(host: str, exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "getaddrinfo" in lowered or "name or service not known" in lowered or "nodename nor servname" in lowered:
            return (
                f"object store unreachable (connectivity) — host '{host}' does not resolve from this runtime. "
                "Compose service names only exist on the Compose network; a serverless deployment needs the "
                "public S3/MinIO endpoint URL"
            )
        if "refused" in lowered:
            return f"object store unreachable (connectivity) — connection refused by '{host}'"
        if "timed out" in lowered or "timeout" in lowered:
            return f"object store unreachable (connectivity) — '{host}' timed out"
        message = text.strip().splitlines()[0] if text.strip() else exc.__class__.__name__
        return f"object store unreachable (connectivity) — {message[:200]}"
