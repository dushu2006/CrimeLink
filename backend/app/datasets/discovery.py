"""Universal dataset file discovery.

The rule this module exists to enforce: **CrimeLink never requires a folder
layout.**  An investigator may hand it

* one ``case_records.csv``
* one ``bank_statement.xlsx``
* ``FIR_2024_019.pdf``
* ``evidence.zip`` containing arbitrary nesting
* a folder of mixed files
* the CrimeLink synthetic corpus with ``operational/`` and ``documents/``

and all of them must work.  Discovery therefore walks whatever it is given,
recursively expands archives into the dataset's own workspace, and describes
every file it finds by *content and extension* -- never by the name of the
directory it happened to sit in.

Security: archive extraction is path-jailed (no ``..``, no absolute members,
no symlinks) and bounded by a total-bytes budget, so a hostile ZIP cannot
write outside the dataset workspace or fill the disk.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.logging import get_logger

log = get_logger("crimelink.datasets.discovery")

#: Extensions we can parse into structured rows.
TABLE_EXTENSIONS = frozenset({".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".json", ".jsonl", ".ndjson"})
#: Extensions we can parse into readable text.
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".text", ".log", ".rtf"})
#: Extensions that are documents with an internal structure (pages/paragraphs).
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".doc"})
ARCHIVE_EXTENSIONS = frozenset({".zip"})

SUPPORTED_EXTENSIONS = (
    TABLE_EXTENSIONS | TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | ARCHIVE_EXTENSIONS
)

MEDIA_TYPES: dict[str, str] = {
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".ndjson": "application/x-ndjson; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".text": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".rtf": "application/rtf",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

#: Magic-byte signatures, checked so a mislabelled extension cannot make the
#: viewer render a PDF as text (the exact defect behind "%PDF-1.3" on screen).
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", ".pdf"),
    (b"\x50\x4b\x03\x04", ".zip"),  # also docx/xlsx -- refined below
    (b"\xd0\xcf\x11\xe0", ".doc"),  # OLE2: legacy .doc/.xls
)

#: Names that are never investigation input, whatever folder they sit in.
IGNORED_BASENAMES = frozenset(
    {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".gitignore"}
)

#: Path components that hold evaluation answers rather than evidence.  Skipped
#: with an explicit reason so the operator sees the exclusion instead of a
#: silent gap.  Matching is separator- and case-insensitive.
EVALUATION_COMPONENTS = frozenset({"groundtruth", "_groundtruth", "answers", "evaluation"})

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB of expanded content
MAX_ARCHIVE_DEPTH = 4


@dataclass(slots=True)
class DiscoveredFile:
    """One file that belongs to a dataset."""

    path: Path                    # absolute path inside the dataset workspace
    relative_path: str            # posix, relative to the dataset root
    filename: str
    extension: str
    media_type: str
    kind: str                     # "table" | "text" | "document" | "archive" | "unknown"
    size_bytes: int
    sha256: str
    container_path: str | None = None   # archive this file was extracted from
    status: str = "DISCOVERED"          # DISCOVERED | UNSUPPORTED | CORRUPT | SKIPPED
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "relative_path": self.relative_path,
            "filename": self.filename,
            "extension": self.extension,
            "media_type": self.media_type,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "container_path": self.container_path,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(slots=True)
class DiscoveryResult:
    root: Path
    files: list[DiscoveredFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def usable(self) -> list[DiscoveredFile]:
        return [f for f in self.files if f.status == "DISCOVERED"]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for f in self.files:
            counts[f.status] = counts.get(f.status, 0) + 1
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        return {
            "root": str(self.root),
            "files_discovered": len(self.files),
            "files_usable": len(self.usable),
            "by_status": counts,
            "by_kind": kinds,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def media_type_for(extension: str) -> str:
    return MEDIA_TYPES.get(extension.lower(), "application/octet-stream")


def kind_for(extension: str) -> str:
    ext = extension.lower()
    if ext in TABLE_EXTENSIONS:
        return "table"
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    if ext in ARCHIVE_EXTENSIONS:
        return "archive"
    return "unknown"


def sniff_extension(path: Path, declared: str) -> str:
    """Trust the bytes over the name when they disagree.

    A ``.txt`` that actually starts with ``%PDF-`` is a PDF; rendering it as
    text is what produced raw PDF internals in the evidence viewer.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return declared
    for signature, ext in _MAGIC:
        if head.startswith(signature):
            if ext == ".zip":
                # OOXML containers are ZIPs; keep the declared office type.
                if declared in {".docx", ".xlsx", ".xlsm", ".pptx"}:
                    return declared
                return ".zip"
            if ext == ".doc" and declared in {".xls", ".doc", ".ppt"}:
                return declared
            return ext
    return declared


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _is_evaluation_path(relative: Path) -> str | None:
    for part in relative.parts[:-1]:
        normal = part.lower().replace("-", "").replace(" ", "")
        if normal in EVALUATION_COMPONENTS or normal.replace("_", "") in EVALUATION_COMPONENTS:
            return part
    return None


def stage_inputs(
    sources: Iterable[Path | str], workspace: Path, *, copy: bool = True
) -> Path:
    """Materialise the caller's inputs into one dataset workspace directory.

    ``sources`` may mix single files and directories.  A single directory is
    used as the workspace root directly (when ``copy`` is False) so the
    built-in corpus is not duplicated on disk; otherwise everything is copied
    under ``workspace`` preserving relative names.
    """
    items = [Path(s) for s in sources]
    if not items:
        raise ValueError("No dataset input was provided.")
    for item in items:
        if not item.exists():
            raise FileNotFoundError(f"Dataset input does not exist: {item}")

    if not copy and len(items) == 1 and items[0].is_dir():
        return items[0].resolve()

    workspace.mkdir(parents=True, exist_ok=True)
    for item in items:
        target = workspace / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, symlinks=False)
        else:
            shutil.copy2(item, target)
    return workspace.resolve()


def _safe_extract(archive: Path, destination: Path, budget: list[int]) -> list[str]:
    """Extract a ZIP into ``destination``, jailed and budgeted."""
    warnings: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts:
                    warnings.append(f"{archive.name}: refused unsafe member '{name}'")
                    continue
                if Path(name).name.lower() in IGNORED_BASENAMES or "__MACOSX" in name:
                    continue
                if info.file_size > budget[0]:
                    warnings.append(
                        f"{archive.name}: member '{name}' exceeds the extraction budget"
                    )
                    continue
                out = (base / name).resolve()
                if not str(out).startswith(str(base)):
                    warnings.append(f"{archive.name}: refused escaping member '{name}'")
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                budget[0] -= info.file_size
    except zipfile.BadZipFile as exc:
        warnings.append(f"{archive.name}: not a readable ZIP archive ({exc})")
    except OSError as exc:
        warnings.append(f"{archive.name}: could not be extracted ({exc})")
    return warnings


def discover(root: Path | str, *, expand_archives: bool = True) -> DiscoveryResult:
    """Walk ``root`` and describe every file, expanding archives in place.

    Returns *all* files with a status, including unsupported ones -- a silent
    skip is exactly the failure mode that lets 131 files disappear from a
    dataset without anybody noticing.
    """
    root_path = Path(root).resolve()
    result = DiscoveryResult(root=root_path)
    if not root_path.exists():
        result.errors.append(f"Dataset root does not exist: {root_path}")
        return result
    if root_path.is_file():
        # A single-file dataset: treat its parent as the root.
        parent = root_path.parent
        result.root = parent
        entry = _describe(root_path, parent)
        if entry is not None:
            result.files.append(entry)
        return result

    budget = [MAX_ARCHIVE_BYTES]
    if expand_archives:
        for depth in range(MAX_ARCHIVE_DEPTH):
            archives = [
                p
                for p in sorted(root_path.rglob("*"))
                if p.is_file() and p.suffix.lower() in ARCHIVE_EXTENSIONS
            ]
            pending = [p for p in archives if not _extract_dir(p).exists()]
            if not pending:
                break
            for archive in pending:
                result.warnings.extend(
                    _safe_extract(archive, _extract_dir(archive), budget)
                )
            log.info(
                "datasets.discovery.archives_expanded",
                depth=depth,
                count=len(pending),
            )

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d != "__MACOSX")
        for name in sorted(filenames):
            if name.lower() in IGNORED_BASENAMES:
                continue
            entry = _describe(base / name, root_path)
            if entry is not None:
                result.files.append(entry)

    if not result.usable:
        result.warnings.append(
            "No file in this dataset could be parsed. Supported formats are "
            "CSV, TSV, XLSX, JSON/JSONL, TXT/MD, PDF, DOCX and ZIP."
        )
    return result


def _extract_dir(archive: Path) -> Path:
    """Where an archive's contents are expanded (a sibling directory)."""
    return archive.with_name(f"{archive.stem}__unzipped")


def _describe(path: Path, root: Path) -> DiscoveredFile | None:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return None
    relative_posix = relative.as_posix()

    try:
        size = path.stat().st_size
    except OSError as exc:
        return DiscoveredFile(
            path=path,
            relative_path=relative_posix,
            filename=path.name,
            extension=path.suffix.lower(),
            media_type="application/octet-stream",
            kind="unknown",
            size_bytes=0,
            sha256="",
            status="CORRUPT",
            reason=f"file could not be read ({exc})",
        )

    declared = path.suffix.lower()
    extension = sniff_extension(path, declared) if size else declared
    entry = DiscoveredFile(
        path=path,
        relative_path=relative_posix,
        filename=path.name,
        extension=extension,
        media_type=media_type_for(extension),
        kind=kind_for(extension),
        size_bytes=size,
        sha256=sha256_of(path),
    )

    excluded = _is_evaluation_path(relative)
    if excluded:
        entry.status = "SKIPPED"
        entry.reason = (
            f"'{excluded}' holds evaluation/ground-truth material and is never "
            "ingested as evidence"
        )
        return entry

    if "__unzipped" in relative.parts[:-1] or entry.extension in ARCHIVE_EXTENSIONS:
        if entry.extension in ARCHIVE_EXTENSIONS:
            entry.status = "SKIPPED"
            entry.reason = "archive expanded; its contents were discovered individually"
            return entry

    if size == 0:
        entry.status = "CORRUPT"
        entry.reason = "the file is empty"
        return entry

    if entry.extension not in SUPPORTED_EXTENSIONS:
        entry.status = "UNSUPPORTED"
        entry.reason = (
            f"'{entry.extension or 'no extension'}' is not a format CrimeLink can "
            "parse; the file is kept and remains downloadable"
        )
    if declared and declared != entry.extension:
        entry.reason = (
            f"declared '{declared}' but the file content is '{entry.extension}'; "
            "the content wins"
        )
    return entry
