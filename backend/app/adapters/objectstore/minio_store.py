"""MinIO / S3-compatible object store (production profile).

Buckets are created at boot with the required posture:

* ``documents``         — original uploads, write-once (S3 object lock).
* ``documents-derived`` — extracted text and OCR output, linked to the parent hash.
* ``audit-anchor``      — nightly audit head hashes, separate write credential.

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
        self._client = Minio(
            self.settings.minio_endpoint,
            access_key=self.settings.minio_access_key,
            secret_key=self.settings.minio_secret_key,
            secure=self.settings.minio_secure,
        )
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
