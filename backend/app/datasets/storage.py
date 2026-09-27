"""Dataset-owned object-store key helpers.

Every imported source file is stored below its dataset id.  Dataset-relative
paths stay unchanged in the manifest and API, while object keys cannot collide
when unrelated corpora contain e.g. ``operational/cases.csv``.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def dataset_object_key(dataset_id: str, relative_path: str) -> str:
    """Return the canonical object key for a source owned by ``dataset_id``."""
    dataset_id = str(dataset_id).strip().strip("/")
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if not dataset_id or not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("Dataset object keys require a dataset id and a safe relative path.")
    return f"{dataset_id}/{path.as_posix()}"


def legacy_object_key(relative_path: str) -> str:
    """The unscoped key used by imports created before dataset key isolation."""
    return str(relative_path).replace("\\", "/").lstrip("/")
