"""Reading exact slices of dataset source files.

The source viewer must open `operational/cdr.csv` at row 18342 without sending
15,000 rows to the browser, so every read here is bounded:

* CSV  — the header plus a window of rows around the target row.
* TXT  — a window of lines around the target line range.
* JSON — the addressed record, not the whole document.

Security: every path is resolved against the configured dataset root and
verified to remain inside it.  A reference is a *relative* path; an absolute
path, a symlink escape, or any ``..`` traversal is refused, so possessing an
evidence URL can never turn into an arbitrary filesystem read.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import NotFoundError, ValidationFailedError
from app.logging import get_logger

log = get_logger("crimelink.source_viewer")

#: Rows/lines of context shown either side of the highlighted region.
DEFAULT_CONTEXT = 3
MAX_WINDOW = 500


class SourceAccessError(ValidationFailedError):
    """The requested path is not a readable file inside the dataset root."""


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
        }


def dataset_root() -> Path:
    return get_settings().resolved_synthetic_data_root


def resolve_in_dataset(relative_path: str, *, root: Path | None = None) -> Path:
    """Resolve a dataset-relative path, refusing anything outside the root."""
    base = (root or dataset_root()).resolve()
    cleaned = (relative_path or "").strip().replace("\\", "/")
    # A reference may address a slice of a table as "operational/cdr.csv#C0001";
    # the fragment identifies the slice, not the file.
    cleaned = cleaned.split("#", 1)[0]
    if not cleaned:
        raise SourceAccessError("No source file was specified.")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise SourceAccessError("Source paths must be relative to the dataset root.")

    candidate = (base / cleaned).resolve()
    if not candidate.is_relative_to(base):
        raise SourceAccessError(
            f"Refusing to read '{relative_path}': it resolves outside the dataset root."
        )
    if not candidate.exists() or not candidate.is_file():
        raise NotFoundError(f"Source file not found in the dataset: {cleaned}")
    return candidate


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
    relative: str = "",
) -> SourceWindow:
    """Read a window of CSV rows around ``row`` (1-based, header = line 1)."""
    from app.domain.models import ORIGIN_COLUMN

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        header = []
    # The origin column is CrimeLink bookkeeping; investigators never see it.
    keep = [i for i, name in enumerate(header) if name != ORIGIN_COLUMN]
    columns = [header[i] for i in keep]

    all_rows = list(reader)
    total_lines = len(all_rows) + 1  # +1 for the header

    if limit is not None:
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


def read_text_window(
    path: Path,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
    context: int = DEFAULT_CONTEXT,
    relative: str = "",
) -> SourceWindow:
    """Read a window of text lines around the highlighted range."""
    content = path.read_text(encoding="utf-8", errors="replace")
    all_lines = content.splitlines()
    total = len(all_lines)

    if line_start is None:
        start, end = 1, min(total, MAX_WINDOW)
        highlight: list[int] = []
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


def read_window(
    relative_path: str,
    *,
    row: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    context: int = DEFAULT_CONTEXT,
    limit: int | None = None,
    root: Path | None = None,
) -> SourceWindow:
    """Open any supported dataset file at the requested position."""
    path = resolve_in_dataset(relative_path, root=root)
    clean = relative_path.split("#", 1)[0]
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_window(
            path, row=row, context=context, limit=limit, relative=clean
        )
    if suffix == ".json":
        return read_json_window(path, relative=clean)
    return read_text_window(
        path, line_start=line_start, line_end=line_end, context=context, relative=clean
    )
