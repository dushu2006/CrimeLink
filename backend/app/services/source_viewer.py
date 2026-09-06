"""Reading dataset source files for human eyes.

Two jobs, one module:

* :func:`read_window` — bounded row/line slices of tabular and text files, so
  a citation can open ``operational/cdr.csv`` at row 18342 without shipping
  15,000 rows to the browser.
* :func:`preview` — the *renderer*: it decides what a file actually is from
  its bytes (never from its extension alone), then hands it to the matching
  reader — real PDF parsing, real workbook sheets, real document paragraphs —
  and refuses to decode arbitrary binary as UTF-8.  The explicit status
  (``AVAILABLE``, ``UNSUPPORTED``, ``CORRUPTED``, ``NOT_FOUND``,
  ``EXTRACTION_FAILED``, ``NO_EXTRACTED_TEXT``) is part of the contract: "no
  evidence" and "we cannot open this format" are different facts and the UI
  must be able to say which one it knows.

Security: every path is resolved against the *active dataset's* root and
verified to remain inside it. A reference is a *relative* path; an absolute
path, a symlink escape, or any ``..`` traversal is refused, so possessing an
evidence URL can never turn into an arbitrary filesystem read.  Files
belonging to an inactive dataset are not reachable through the active
dataset's root -- which is exactly how a replaced corpus used to stay
openable after it was supposed to be gone.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.errors import NotFoundError, ValidationFailedError
from app.logging import get_logger

log = get_logger("crimelink.source_viewer")

#: Rows/lines of context shown either side of the highlighted region.
DEFAULT_CONTEXT = 3
MAX_WINDOW = 500
#: Cap on how much of a rendered document is handed to the browser.
MAX_PDF_PAGES = 200
MAX_PAGE_CHARS = 20_000

# ---------------------------------------------------------------------------
# Explicit viewer states (requirement: no vague "No evidence" placeholders)
# ---------------------------------------------------------------------------

STATUS_AVAILABLE = "AVAILABLE"              # opened and rendered
STATUS_UNSUPPORTED = "UNSUPPORTED"          # format this viewer cannot render
STATUS_CORRUPTED = "CORRUPTED"              # file is damaged / not parseable
STATUS_NOT_FOUND = "NOT_FOUND"              # path resolves to nothing
STATUS_EXTRACTION_FAILED = "EXTRACTION_FAILED"  # parser blew up mid-file
STATUS_NO_EXTRACTED_TEXT = "NO_EXTRACTED_TEXT"  # format ok, nothing readable


class SourceAccessError(ValidationFailedError):
    """The requested path is not a readable file inside the dataset root."""

    status = STATUS_UNSUPPORTED

    def __init__(self, message: str, *, status: str = STATUS_UNSUPPORTED) -> None:
        super().__init__(message)
        self.status = status


class SourceNotFoundError(NotFoundError):
    """The referenced file does not exist inside the dataset."""

    status = STATUS_NOT_FOUND


# ---------------------------------------------------------------------------
# True-type detection
# ---------------------------------------------------------------------------

#: Content signatures checked before any renderer runs.  The defect this
#: prevents is precise: a PDF decoded as UTF-8 and shown on screen as
#: ``%PDF-1.3`` garbage.  The bytes decide the renderer, never the extension.
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
    (b"GIF87a", "image", "image/gif"),
    (b"GIF89a", "image", "image/gif"),
    (b"PK\x03\x04", "ooxml", "application/octet-stream"),  # zip container
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole", "application/octet-stream"),
)

_OOXML_MARKERS = (
    ("word/", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("xl/", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("ppt/", "pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
)

_TEXT_SUFFIXES = {".txt", ".md", ".text", ".log", ".rtf", ".eml", ".html", ".htm", ".xml"}
_JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
_CSV_SUFFIXES = {".csv", ".tsv"}


@dataclass(slots=True)
class FileKind:
    """What a file *is*, decided from its bytes."""

    kind: str            # pdf | docx | xlsx | pptx | csv | json | text | image | binary
    media_type: str
    detail: str = ""     # human note, e.g. "legacy OLE2 container (.doc/.ppt)"

    @property
    def is_readable_text(self) -> bool:
        return self.kind in {"csv", "json", "text"}


def detect_kind(path: Path) -> FileKind:
    """Sniff a file's real type from its first bytes, not its name."""
    try:
        with path.open("rb") as handle:
            head = handle.read(8192)
    except OSError as exc:
        raise SourceAccessError(
            f"the file could not be read ({exc})", status=STATUS_NOT_FOUND
        ) from exc

    for signature, kind, media_type in _MAGIC:
        if head.startswith(signature):
            if kind == "ooxml":
                return _sniff_ooxml(path)
            if kind == "ole":
                return FileKind(
                    "binary",
                    "application/x-ole-storage",
                    "legacy Office binary format (.doc/.xls/.ppt); re-save as "
                    "OOXML (.docx/.xlsx/.pptx) or PDF to preview it",
                )
            return FileKind(kind, media_type)

    suffix = path.suffix.lower()
    if suffix in _CSV_SUFFIXES:
        return FileKind("csv", "text/csv; charset=utf-8")
    if suffix in _JSON_SUFFIXES:
        return FileKind("json", "application/json; charset=utf-8")
    if suffix in _TEXT_SUFFIXES:
        return FileKind("text", _text_media(suffix))
    # Unknown: only classify as text when it demonstrably decodes as text.
    if b"\x00" in head:
        return FileKind("binary", "application/octet-stream")
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return FileKind("binary", "application/octet-stream")
    return FileKind("text", _text_media(suffix))


def _sniff_ooxml(path: Path) -> FileKind:
    import zipfile

    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
    except Exception as exc:  # noqa: BLE001
        return FileKind(
            "binary", "application/zip", f"ZIP container could not be read ({exc})"
        )
    for marker, kind, media_type in _OOXML_MARKERS:
        if any(name.startswith(marker) for name in names[:4000]):
            return FileKind(kind, media_type)
    return FileKind("binary", "application/zip", "ZIP archive")


def _text_media(suffix: str) -> str:
    guessed = mimetypes.guess_type(f"file{suffix}")[0]
    return f"{guessed or 'text/plain'}; charset=utf-8"


# ---------------------------------------------------------------------------
# Windows (bounded slices)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SourceWindow:
    """A bounded slice of a source file, ready for display."""

    file: str
    source_type: str
    total_units: int          # rows (csv) or lines (txt)
    unit_label: str           # "row" | "line"
    start: int                # 1-based, inclusive
    end: int                  # 1-based, inclusive
    highlight: list[int]      # unit numbers to highlight
    columns: list[str]
    rows: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    truncated: bool
    size_bytes: int
    sheets: list[str] = field(default_factory=list)
    sheet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "source_type": self.source_type,
            "total_units": self.total_units,
            "unit_label": self.unit_label,
            "start": self.start,
            "end": self.end,
            "highlight": list(self.highlight),
            "columns": list(self.columns),
            "rows": list(self.rows),
            "lines": list(self.lines),
            "truncated": self.truncated,
            "size_bytes": self.size_bytes,
            "sheets": list(self.sheets),
            "sheet": self.sheet,
        }


def dataset_root() -> Path:
    from app.config import get_settings

    return get_settings().resolved_synthetic_data_root


def resolve_in_dataset(relative_path: str, *, root: Path | None = None) -> Path:
    """Resolve a dataset-relative path, refusing anything outside the root.

    ``root`` is the dataset directory the path is relative to.  Callers that
    know which dataset they mean (every HTTP path should) pass it explicitly:
    that is what keeps a replaced dataset's files unopenable.  Without a root,
    the legacy fallbacks apply (bundled corpus root first), kept so the
    external-synthetic evaluation flow keeps working.
    """
    cleaned = (relative_path or "").strip().replace("\\", "/")
    # A reference may address a slice of a table as "operational/cdr.csv#C0001";
    # the fragment identifies the slice, not the file.
    cleaned = cleaned.split("#", 1)[0]
    if not cleaned:
        raise SourceAccessError(
            "No source file was specified.", status=STATUS_NOT_FOUND
        )
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise SourceAccessError("Source paths must be relative to the dataset root.")
    parts = Path(cleaned).parts
    if any(part == ".." for part in parts):
        raise SourceAccessError("Source paths must not traverse outside the dataset.")

    if root is not None:
        base = Path(root).resolve()
        candidate = (base / cleaned).resolve()
        if not candidate.is_relative_to(base):
            raise SourceAccessError(
                f"Refusing to read '{relative_path}': it resolves outside the dataset root."
            )
        if candidate.exists() and candidate.is_file():
            return candidate
        # A path from another era of the same dataset (folder renamed, file
        # moved one level up) still deserves a try with leading segments
        # stripped -- but only inside THIS dataset root.
        stripped = parts
        for i in range(1, len(stripped)):
            cand = (base / Path(*stripped[i:])).resolve()
            if cand.is_file() and cand.is_relative_to(base):
                return cand
        raise SourceNotFoundError(f"Source file not found in the dataset: {cleaned}")

    # No explicit root: legacy resolution.  Bundled synthetic corpus first,
    # then -- only when no dataset is active -- the workspaces/uploads of
    # whatever has been imported.  An ACTIVE dataset always owns resolution, so
    # old-import paths are not silently openable beside it.
    base = dataset_root()
    candidate = (base / cleaned).resolve()
    if candidate.is_relative_to(base) and candidate.is_file():
        return candidate

    from app.config import get_settings

    settings = get_settings()
    try:
        active_root = active_dataset_root()
    except Exception:  # noqa: BLE001 - never let a lookup break resolution
        active_root = None
    search_roots: list[Path] = []
    if active_root is not None:
        search_roots = [active_root]
    else:
        datasets_root = (settings.data_dir / "datasets").resolve()
        if datasets_root.exists():
            search_roots = [p for p in sorted(datasets_root.iterdir()) if p.is_dir()]
        uploads_root = (settings.data_dir / "uploads").resolve()
        if uploads_root.exists():
            search_roots.append(uploads_root)

    for candidate_root in search_roots:
        direct = (candidate_root / cleaned).resolve()
        if direct.is_file() and direct.is_relative_to(candidate_root):
            return direct
        for i in range(1, len(parts)):
            cand = (candidate_root / Path(*parts[i:])).resolve()
            if cand.is_file() and cand.is_relative_to(candidate_root):
                return cand

    raise SourceNotFoundError(f"Source file not found in the dataset: {cleaned}")


def active_dataset_root() -> Path | None:
    """Workspace of the active dataset, resolved synchronously.

    HTTP routes use the async variant (:func:`app.api.v1.sources._dataset_root`)
    with their request session; this sync version exists for callers outside a
    request (CLI checks, tests) and reads the database through its own short
    session.
    """
    import asyncio

    from app.db.session import async_session
    from app.datasets import registry

    async def _lookup() -> Path | None:
        async with async_session() as session:
            dataset = await registry.active_dataset(session)
        if dataset is None or not dataset.root_path:
            return None
        root = Path(dataset.root_path)
        return root if root.is_dir() else None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError(
            "active_dataset_root() is blocking; await the registry lookup instead."
        )
    return asyncio.run(_lookup())


def _clamp_window(target: int | None, total: int, context: int) -> tuple[int, int]:
    if total <= 0:
        return (1, 0)
    if target is None:
        return (1, min(total, max(1, context * 2 + 1)))
    start = max(1, target - context)
    end = min(total, target + context)
    return (start, end)


def read_csv_window(
    path: Path,
    *,
    row: int | None = None,
    context: int = DEFAULT_CONTEXT,
    limit: int | None = None,
    offset: int | None = None,
    relative: str = "",
    delimiter: str = ",",
) -> SourceWindow:
    """Read a window of CSV/TSV rows around ``row`` (1-based, header = line 1).

    ``offset`` starts a page at that row number (used with ``limit`` for
    "load more" tables); ``row`` centres a small context window instead.
    """
    from app.domain.models import ORIGIN_COLUMN

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        header = []
    # The origin column is CrimeLink bookkeeping; investigators never see it.
    keep = [i for i, name in enumerate(header) if name != ORIGIN_COLUMN]
    columns = [header[i] for i in keep]

    all_rows = list(reader)
    total_lines = len(all_rows) + 1  # +1 for the header

    if offset is not None and limit is not None:
        start = max(2, int(offset))
        end = min(total_lines, start + max(1, int(limit)) - 1)
    elif limit is not None:
        start, end = 1, min(total_lines, max(2, limit + 1))
    else:
        start, end = _clamp_window(row, total_lines, context)
        if start < 2:
            start = 2 if total_lines > 1 else 1
    if end - start + 1 > MAX_WINDOW:
        end = start + MAX_WINDOW - 1

    rows: list[dict[str, Any]] = []
    for line_no in range(max(2, start), end + 1):
        raw = all_rows[line_no - 2] if line_no - 2 < len(all_rows) else []
        values = {
            columns[position]: (raw[index] if index < len(raw) else "")
            for position, index in enumerate(keep)
        }
        rows.append({"row": line_no, "values": values})

    return SourceWindow(
        file=relative or path.name,
        source_type="csv",
        total_units=total_lines,
        unit_label="row",
        start=start,
        end=end,
        highlight=[row] if row else [],
        columns=columns,
        rows=rows,
        lines=[],
        truncated=(end - start + 1) < (total_lines - 1),
        size_bytes=path.stat().st_size,
    )


def list_sheets(path: Path) -> list[str]:
    """Sheet names of a workbook, for the sheet picker in the UI."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        return list(workbook.sheetnames)
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001
            pass


def read_xlsx_window(
    path: Path,
    *,
    row: int | None = None,
    context: int = DEFAULT_CONTEXT,
    limit: int | None = None,
    offset: int | None = None,
    relative: str = "",
    sheet: str | None = None,
) -> SourceWindow:
    """Read a window of rows from an XLSX workbook around ``row``.

    ``sheet`` selects a named worksheet (first sheet by default).  Values are
    stringified, never re-typed: dates and numbers arrive as openpyxl renders
    them so the viewer shows the workbook, not an interpretation of it.
    """
    import openpyxl
    from app.domain.models import ORIGIN_COLUMN

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        names = list(workbook.sheetnames)
        target = (
            workbook[sheet]
            if sheet and sheet in names
            else (workbook.active or workbook.worksheets[0])
        )
        sheet_name = target.title
        iterator = target.iter_rows(values_only=True)
        try:
            raw_header = next(iterator)
        except StopIteration:
            raw_header = ()
        header = [
            str(c).strip() if c is not None else f"column_{idx + 1}"
            for idx, c in enumerate(raw_header)
        ]
        keep = [i for i, name in enumerate(header) if name != ORIGIN_COLUMN]
        columns = [header[i] for i in keep]

        all_rows: list[list[Any]] = []
        for r in iterator:
            all_rows.append(list(r))
    finally:
        try:
            workbook.close()
        except Exception:  # noqa: BLE001
            pass

    total_lines = len(all_rows) + 1  # +1 for header

    if offset is not None and limit is not None:
        start = max(2, int(offset))
        end = min(total_lines, start + max(1, int(limit)) - 1)
    elif limit is not None:
        start, end = 1, min(total_lines, max(2, limit + 1))
    else:
        start, end = _clamp_window(row, total_lines, context)
        if start < 2:
            start = 2 if total_lines > 1 else 1
    if end - start + 1 > MAX_WINDOW:
        end = start + MAX_WINDOW - 1

    rows: list[dict[str, Any]] = []
    for line_no in range(max(2, start), end + 1):
        raw = all_rows[line_no - 2] if line_no - 2 < len(all_rows) else []
        values = {
            columns[pos]: (
                str(raw[idx]).strip()
                if idx < len(raw) and raw[idx] is not None
                else ""
            )
            for pos, idx in enumerate(keep)
        }
        rows.append({"row": line_no, "values": values})

    return SourceWindow(
        file=relative or path.name,
        source_type="table",
        total_units=total_lines,
        unit_label="row",
        start=start,
        end=end,
        highlight=[row] if row else [],
        columns=columns,
        rows=rows,
        lines=[],
        truncated=(end - start + 1) < (total_lines - 1),
        size_bytes=path.stat().st_size,
        sheets=names,
        sheet=sheet_name,
    )


def read_text_window(
    path: Path,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
    context: int = DEFAULT_CONTEXT,
    offset: int | None = None,
    limit: int | None = None,
    relative: str = "",
) -> SourceWindow:
    """Read a window of text lines around the highlighted range.

    Only ever called on files :func:`detect_kind` has proven to be text.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines()
    total = len(all_lines)

    if offset is not None and limit is not None:
        start = max(1, int(offset))
        end = min(total, start + max(1, int(limit)) - 1)
        highlight: list[int] = []
    elif line_start is None:
        start, end = 1, min(total, MAX_WINDOW)
        highlight = []
    else:
        stop = line_end or line_start
        start = max(1, line_start - context)
        end = min(total, stop + context)
        highlight = list(range(line_start, min(stop, total) + 1))
    if end - start + 1 > MAX_WINDOW:
        end = start + MAX_WINDOW - 1

    lines = [
        {"line": number, "text": all_lines[number - 1]}
        for number in range(start, end + 1)
        if 0 < number <= total
    ]
    return SourceWindow(
        file=relative or path.name,
        source_type="txt",
        total_units=total,
        unit_label="line",
        start=start,
        end=end,
        highlight=highlight,
        columns=[],
        rows=[],
        lines=lines,
        truncated=(end - start + 1) < total,
        size_bytes=path.stat().st_size,
    )


def read_json_window(path: Path, *, relative: str = "") -> SourceWindow:
    """Render a JSON document as addressable text lines."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except ValueError:
        pretty = raw
    all_lines = pretty.splitlines()[:MAX_WINDOW]
    return SourceWindow(
        file=relative or path.name,
        source_type="json",
        total_units=len(pretty.splitlines()),
        unit_label="line",
        start=1,
        end=len(all_lines),
        highlight=[],
        columns=[],
        rows=[],
        lines=[{"line": i, "text": t} for i, t in enumerate(all_lines, start=1)],
        truncated=len(all_lines) < len(pretty.splitlines()),
        size_bytes=path.stat().st_size,
    )


def read_document_window(path: Path, *, relative: str = "") -> SourceWindow:
    """Render a PDF/DOCX/PPTX as readable lines via its real parser.

    This is the fallback view for citations (references address a page/line,
    not a byte offset); the rich preview at :func:`preview` is what the
    Sources page uses to *render* the document.
    """
    from app.datasets import readers

    try:
        parsed = readers.read_text(path)
    except readers.UnreadableSource as exc:
        status = exc.code if exc.code in {
            "UNSUPPORTED", "CORRUPTED", "NOT_FOUND", "NO_EXTRACTED_TEXT"
        } else STATUS_EXTRACTION_FAILED
        raise SourceAccessError(exc.reason, status=status) from exc
    if not parsed.text.strip():
        raise SourceAccessError(
            "the document contains no extractable text (it may be a scan)",
            status=STATUS_NO_EXTRACTED_TEXT,
        )
    if parsed.pages and len(parsed.pages) > 1:
        # Keep the page structure visible: label lines with their page.
        lines: list[dict[str, Any]] = []
        number = 1
        for page_index, page in enumerate(parsed.pages[:MAX_PDF_PAGES], start=1):
            lines.append(
                {"line": number, "text": f"—— page {page_index} of {parsed.page_count} ——" if parsed.page_count > 1 else f"—— page {page_index} ——"}
            )
            number += 1
            for text in page.splitlines():
                lines.append({"line": number, "text": text})
                number += 1
                if number > MAX_WINDOW:
                    break
            if number > MAX_WINDOW:
                break
    else:
        all_lines = parsed.text.splitlines()
        lines = [
            {"line": i, "text": t}
            for i, t in enumerate(all_lines[:MAX_WINDOW], start=1)
        ]
    return SourceWindow(
        file=relative or path.name,
        source_type="document",
        total_units=len(parsed.text.splitlines()),
        unit_label="line",
        start=1,
        end=len(lines),
        highlight=[],
        columns=[],
        rows=[],
        lines=lines,
        truncated=len(lines) < len(parsed.text.splitlines()),
        size_bytes=path.stat().st_size,
    )


def read_window(
    relative_path: str,
    *,
    row: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    context: int = DEFAULT_CONTEXT,
    limit: int | None = None,
    offset: int | None = None,
    root: Path | None = None,
    sheet: str | None = None,
) -> SourceWindow:
    """Open any supported dataset file at the requested position.

    The renderer is chosen from the file's bytes: a PDF never reaches a text
    decoder here, which is the whole "%PDF-1.3 on screen" class of bugs.
    """
    path = resolve_in_dataset(relative_path, root=root)
    clean = relative_path.split("#", 1)[0]
    kind = detect_kind(path)
    if kind.kind == "pdf" or kind.kind in {"docx", "pptx"} or (
        kind.kind == "binary" and path.suffix.lower() == ".doc"
    ):
        return read_document_window(path, relative=clean)
    if kind.kind == "csv" or (kind.kind == "binary" and path.suffix.lower() in _CSV_SUFFIXES):
        return read_csv_window(
            path,
            row=row,
            context=context,
            limit=limit,
            offset=offset,
            relative=clean,
            delimiter="\t" if path.suffix.lower() == ".tsv" else ",",
        )
    if kind.kind == "xlsx":
        return read_xlsx_window(
            path, row=row, context=context, limit=limit, offset=offset,
            relative=clean, sheet=sheet,
        )
    if kind.kind == "json":
        return read_json_window(path, relative=clean)
    if kind.kind == "image":
        raise SourceAccessError(
            "this is an image; open the file itself to view it",
            status=STATUS_UNSUPPORTED,
        )
    if kind.kind == "binary":
        raise SourceAccessError(
            kind.detail or "binary files are never decoded as text; open or download the file",
            status=STATUS_UNSUPPORTED,
        )
    return read_text_window(
        path,
        line_start=line_start,
        line_end=line_end,
        context=context,
        offset=offset,
        limit=limit,
        relative=clean,
    )


# ---------------------------------------------------------------------------
# Preview: the renderer decision used by the Sources page
# ---------------------------------------------------------------------------


def extract_docx_blocks(path: Path) -> list[dict[str, Any]]:
    """Structured, readable content of a DOCX: paragraphs and tables.

    Basic structure is preserved -- headings stay headings, tables stay
    tables -- because "the text of the document with all layout lost" is a
    worse preview than the one a court would expect from an exhibit viewer.
    """
    import docx  # type: ignore[import-untyped]

    document = docx.Document(str(path))
    blocks: list[dict[str, Any]] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
        kind = "paragraph"
        if "heading" in style or style == "title":
            kind = "heading"
        depth = 1
        for token in style.replace("heading ", "").strip():
            if token.isdigit():
                depth = int(token)
                break
        blocks.append({"type": kind, "level": depth if kind == "heading" else None, "text": text})
    for table_index, table in enumerate(document.tables):
        rows = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        if rows:
            blocks.append({"type": "table", "index": table_index, "rows": rows})
    return blocks


def preview(
    relative_path: str,
    *,
    root: Path | None = None,
    row: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    context: int = DEFAULT_CONTEXT,
    limit: int | None = None,
    offset: int | None = None,
    sheet: str | None = None,
    dataset_id: str | None = None,
    raw_url: str | None = None,
    download_url: str | None = None,
) -> dict[str, Any]:
    """Decide what a file is, render it with the matching renderer, report why not.

    The result always carries an explicit ``status`` and a ``render_kind`` the
    frontend maps to exactly one viewer.  Nothing in here decodes binary as
    UTF-8 and hopes: the browser gets raw bytes (with an honest content type)
    for native rendering, and parsed content for documents we can extract.
    """
    clean = (relative_path or "").split("#", 1)[0]
    try:
        path = resolve_in_dataset(relative_path, root=root)
    except SourceNotFoundError as exc:
        return {
            "status": STATUS_NOT_FOUND,
            "reason": str(exc),
            "openable": False,
            "render_kind": "none",
            "file": {"path": clean},
            "window": None,
        }
    except SourceAccessError as exc:
        return {
            "status": exc.status,
            "reason": str(exc),
            "openable": False,
            "render_kind": "none",
            "file": {"path": clean},
            "window": None,
        }

    kind = detect_kind(path)
    meta = {
        "path": clean,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "media_type": kind.media_type,
        "size_bytes": path.stat().st_size,
        "dataset_id": dataset_id,
    }
    result: dict[str, Any] = {
        "status": STATUS_AVAILABLE,
        "reason": kind.detail or None,
        "openable": True,
        "render_kind": kind.kind,
        "file": meta,
        "window": None,
        "pdf": None,
        "document_blocks": None,
        "slides": None,
        "sheets": None,
        "raw_url": raw_url,
        "download_url": download_url,
    }

    try:
        if kind.kind == "pdf":
            result.update(_preview_pdf(path))
            return result
        if kind.kind == "docx":
            result["document_blocks"] = extract_docx_blocks(path)
            if not result["document_blocks"]:
                result["status"] = STATUS_NO_EXTRACTED_TEXT
                result["reason"] = "the document contains no readable text"
            return result
        if kind.kind == "pptx":
            result["slides"] = _preview_pptx(path)
            if all(not slide["lines"] for slide in result["slides"]):
                result["status"] = STATUS_NO_EXTRACTED_TEXT
                result["reason"] = (
                    "the presentation contains no extractable text (image-only slides)"
                )
            return result
        if kind.kind == "xlsx":
            result["sheets"] = list_sheets(path)
            result["window"] = read_xlsx_window(
                path, row=row, context=context, limit=limit, offset=offset,
                relative=clean, sheet=sheet,
            ).to_dict()
            result["sheet"] = (result["window"] or {}).get("sheet")
            return result
        if kind.kind == "csv":
            result["window"] = read_csv_window(
                path,
                row=row,
                context=context,
                limit=limit,
                offset=offset,
                relative=clean,
                delimiter="\t" if path.suffix.lower() == ".tsv" else ",",
            ).to_dict()
            return result
        if kind.kind == "json":
            result["window"] = read_json_window(path, relative=clean).to_dict()
            return result
        if kind.kind == "text":
            result["window"] = read_text_window(
                path,
                line_start=line_start,
                line_end=line_end,
                context=context,
                offset=offset,
                limit=limit,
                relative=clean,
            ).to_dict()
            return result
        if kind.kind == "image":
            # The browser itself is the renderer; raw bytes carry the type.
            return result
        result["status"] = STATUS_UNSUPPORTED
        result["openable"] = False
        result["render_kind"] = "binary"
        result["reason"] = kind.detail or (
            "CrimeLink has no viewer for this file type; download or open it "
            "outside the application"
        )
        return result
    except SourceAccessError as exc:
        result["status"] = exc.status
        result["reason"] = str(exc)
        result["openable"] = exc.status in {STATUS_NO_EXTRACTED_TEXT, STATUS_AVAILABLE}
        result["window"] = None
        return result
    except Exception as exc:  # noqa: BLE001 - the preview must degrade explicitly
        log.warning(
            "source_viewer.preview_failed", path=clean, error=f"{type(exc).__name__}: {exc}"
        )
        result["status"] = STATUS_EXTRACTION_FAILED
        result["reason"] = f"{type(exc).__name__}: {exc}"
        result["window"] = None
        return result


def _preview_pdf(path: Path) -> dict[str, Any]:
    from app.datasets import readers

    try:
        parsed = readers.read_pdf(path)
    except readers.UnreadableSource as exc:
        status = exc.code if exc.code == "CORRUPTED" else STATUS_EXTRACTION_FAILED
        raise SourceAccessError(exc.reason, status=status) from exc
    pages = [
        {"page": index, "text": page[:MAX_PAGE_CHARS]}
        for index, page in enumerate(parsed.pages[:MAX_PDF_PAGES], start=1)
    ]
    note = None
    status = STATUS_AVAILABLE
    if parsed.empty:
        # The PDF itself is still perfectly renderable -- say so, and keep the
        # page images available rather than reporting "no evidence".
        status = STATUS_NO_EXTRACTED_TEXT
        note = (
            "no text layer (image-only PDF, e.g. a scan) -- the pages render "
            "visually below"
        )
    return {
        "status": status,
        "reason": note,
        "render_kind": "pdf",
        "pdf": {
            "page_count": parsed.page_count,
            "text_available": not parsed.empty,
            "pages": pages,
            "truncated": parsed.page_count > MAX_PDF_PAGES,
        },
    }


def _preview_pptx(path: Path) -> list[dict[str, Any]]:
    from app.datasets import readers

    try:
        parsed = readers.read_pptx(path)
    except readers.UnreadableSource as exc:
        status = exc.code if exc.code in {"CORRUPTED", "UNSUPPORTED"} else STATUS_EXTRACTION_FAILED
        raise SourceAccessError(exc.reason, status=status) from exc
    slides = []
    for index, page in enumerate(parsed.pages, start=1):
        lines = [line for line in page.splitlines() if line.strip()]
        slides.append(
            {
                "index": index,
                "title": lines[0] if lines else f"Slide {index}",
                "lines": [line.strip() for line in lines[1:200]],
            }
        )
    return slides
