"""Signed object delivery for the embedded object store (PRD 6.3).

With MinIO, ``presigned_url`` returns a genuine S3 pre-signed link.  With the
local filesystem store there is no S3 to sign against, so CrimeLink issues its
own HMAC-signed, time-limited URL pointing at this endpoint.  The invariant is
identical either way:

* the link expires (15 minutes by default);
* the signature is verified in constant time;
* raw storage is never reachable from the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.adapters.objectstore.local import LocalObjectStore
from app.config import get_settings
from app.logging import get_logger

log = get_logger("crimelink.objects")
router = APIRouter(prefix="/objects", tags=["objects"])


@router.get("/{bucket}/{key:path}")
async def signed_object(
    bucket: str,
    key: str,
    exp: int = Query(...),
    sig: str = Query(...),
) -> Response:
    settings = get_settings()
    if not LocalObjectStore.verify_signature(settings, bucket, key, exp, sig):
        log.warning("object.signature_rejected", bucket=bucket, key=key)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Link expired or invalid.")

    store = LocalObjectStore(settings)
    try:
        data = store.get(bucket, key)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    meta = store.stat(bucket, key)
    media_type = (meta.content_type if meta else None) or "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"},
    )
