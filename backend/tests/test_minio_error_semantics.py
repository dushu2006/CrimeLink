"""Missing S3 objects are distinct from storage/authentication outages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.objectstore import minio_store
from app.errors import DependencyUnavailableError, NotFoundError


class FakeS3Error(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class FakeClient:
    def __init__(self, *, get_error=None, stat_error=None, list_error=None):
        self.get_error = get_error
        self.stat_error = stat_error
        self.list_error = list_error

    def get_object(self, *_args, **_kwargs):
        if self.get_error:
            raise self.get_error
        return SimpleNamespace(read=lambda: b"bytes", close=lambda: None, release_conn=lambda: None)

    def stat_object(self, *_args, **_kwargs):
        if self.stat_error:
            raise self.stat_error
        return SimpleNamespace(
            size=5,
            content_type="application/pdf",
            metadata={"x-amz-meta-sha256": "actual-hash"},
            etag="etag",
        )

    def list_objects(self, *_args, **_kwargs):
        if self.list_error:
            raise self.list_error
        return []


def make_store(monkeypatch, client):
    monkeypatch.setattr(minio_store, "S3Error", FakeS3Error)
    store = object.__new__(minio_store.MinioObjectStore)
    store._client = client
    return store


def test_get_maps_only_missing_object_codes_to_not_found(monkeypatch):
    missing = make_store(monkeypatch, FakeClient(get_error=FakeS3Error("NoSuchKey")))
    with pytest.raises(NotFoundError):
        missing.get("documents", "absent.pdf")

    denied = make_store(monkeypatch, FakeClient(get_error=FakeS3Error("AccessDenied")))
    with pytest.raises(DependencyUnavailableError):
        denied.get("documents", "private.pdf")

    missing_bucket = make_store(
        monkeypatch, FakeClient(get_error=FakeS3Error("NoSuchBucket"))
    )
    with pytest.raises(DependencyUnavailableError):
        missing_bucket.get("documents", "source.pdf")


def test_stat_maps_only_missing_object_codes_to_none(monkeypatch):
    missing = make_store(monkeypatch, FakeClient(stat_error=FakeS3Error("NoSuchKey")))
    assert missing.stat("documents", "absent.pdf") is None

    unavailable = make_store(
        monkeypatch, FakeClient(stat_error=FakeS3Error("ServiceUnavailable"))
    )
    with pytest.raises(DependencyUnavailableError):
        unavailable.stat("documents", "source.pdf")

    missing_bucket = make_store(
        monkeypatch, FakeClient(stat_error=FakeS3Error("NoSuchBucket"))
    )
    with pytest.raises(DependencyUnavailableError):
        missing_bucket.stat("documents", "source.pdf")

    list_outage = make_store(
        monkeypatch, FakeClient(list_error=FakeS3Error("AccessDenied"))
    )
    with pytest.raises(DependencyUnavailableError):
        list_outage.list_keys("documents", prefix="candidate/")

    found = make_store(monkeypatch, FakeClient())
    meta = found.stat("documents", "source.pdf")
    assert meta is not None and meta.etag == "actual-hash"
