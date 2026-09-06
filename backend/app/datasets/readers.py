"""Format readers -- one place that knows how to open each real file type.

Two return shapes cover everything CrimeLink ingests:

``Table``
    Columns plus rows, where every row carries the coordinates needed to point
    back at it (``row`` for CSV, ``sheet`` + ``row`` for XLSX, ``index`` for
    JSON arrays).  Used by the normalization layer.

``TextDocument``
    Human-readable text plus the page/line map, so a citation can say "page 4"
    and mean it.

Nothing here decodes binary as UTF-8 and hopes for the best: a PDF is read
with a PDF parser, an XLSX with an XLSX parser, and a file that genuinely
cannot be parsed raises :class:`UnreadableSource` with a reason a human can
act on.  That is the difference between "no evidence" and "this renderer does
not support this format", which the UI must be able to tell apart.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.logging import get_logger

log = get_logger("crimelink.datasets.readers")

#: Hard cap on rows held in memory for a single table read.
MAX_ROWS = 200_000
#: Rows sampled for schema detection.
SAMPLE_ROWS = 200


class UnreadableSource(RuntimeError):
    """The file exists but cannot be parsed. Carries an actionable reason."""

    def __init__(self, reason: str, *, code: str = "EXTRACTION_FAILED") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(slots=True)
class Table:
    """A parsed tabular source (one sheet of one file)."""

    name: str                       # sheet name, or the filename for flat files
    columns: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    #: 1-based source coordinates parallel to ``rows``.
    row_numbers: list[int] = field(default_factory=list)
    truncated: bool = False

    def sample(self, limit: int = SAMPLE_ROWS) -> list[dict[str, Any]]:
        return self.rows[:limit]

    def iter_rows(self) -> Iterator[tuple[int, dict[str, Any]]]:
        for index, row in enumerate(self.rows):
            yield (self.row_numbers[index] if index < len(self.row_numbers) else index + 2, row)


@dataclass(slots=True)
class TextDocument:
    """A parsed text/document source."""

    text: str
    pages: list[str] = field(default_factory=list)
    page_count: int = 0
    #: True when the format carries text but this file had none to give.
    empty: bool = False


# ---------------------------------------------------------------------------
# Tabular readers
# ---------------------------------------------------------------------------


def _sniff_delimiter(sample: str, default: str = ",") -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return default


def read_csv(path: Path, *, delimiter: str | None = None) -> list[Table]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise UnreadableSource(f"the file could not be read ({exc})", code="NOT_FOUND") from exc
    if not text.strip():
        raise UnreadableSource("the file is empty", code="NO_EXTRACTED_TEXT")

    sep = delimiter or (
        "\t" if path.suffix.lower() == ".tsv" else _sniff_delimiter(text[:8192])
    )
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    try:
        header = next(reader)
    except StopIteration:
        raise UnreadableSource("the file has no header row", code="NO_EXTRACTED_TEXT") from None

    columns = [(h or "").strip() for h in header]
    # De-duplicate blank / repeated headers so row dicts stay addressable.
    seen: dict[str, int] = {}
    normalized: list[str] = []
    for index, name in enumerate(columns):
        base = name or f"column_{index + 1}"
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
        normalized.append(base)

    rows: list[dict[str, Any]] = []
    numbers: list[int] = []
    truncated = False
    for line_no, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        rows.append(
            {
                normalized[i]: (raw[i].strip() if i < len(raw) and raw[i] is not None else "")
                for i in range(len(normalized))
            }
        )
        numbers.append(line_no)
    return [Table(name=path.stem, columns=normalized, rows=rows, row_numbers=numbers, truncated=truncated)]


def read_xlsx(path: Path) -> list[Table]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise UnreadableSource("the XLSX parser is not installed", code="UNSUPPORTED") from exc
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise UnreadableSource(
            f"the workbook could not be opened ({type(exc).__name__}: {exc})",
            code="CORRUPTED",
        ) from exc

    tables: list[Table] = []
    try:
        for sheet in workbook.worksheets:
            iterator = sheet.iter_rows(values_only=True)
            try:
                header = next(iterator)
            except StopIteration:
                tables.append(Table(name=str(sheet.title), columns=[], rows=[]))
                continue
            columns: list[str] = []
            seen: dict[str, int] = {}
            for index, cell in enumerate(header or ()):
                base = str(cell).strip() if cell is not None else ""
                base = base or f"column_{index + 1}"
                if base in seen:
                    seen[base] += 1
                    base = f"{base}_{seen[base]}"
                else:
                    seen[base] = 0
                columns.append(base)

            rows: list[dict[str, Any]] = []
            numbers: list[int] = []
            truncated = False
            for row_no, values in enumerate(iterator, start=2):
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                if values is None or all(v is None or str(v).strip() == "" for v in values):
                    continue
                rows.append(
                    {
                        columns[i]: _cell_text(values[i]) if i < len(values) else ""
                        for i in range(len(columns))
                    }
                )
                numbers.append(row_no)
            tables.append(
                Table(
                    name=str(sheet.title),
                    columns=columns,
                    rows=rows,
                    row_numbers=numbers,
                    truncated=truncated,
                )
            )
    finally:
        try:
            workbook.close()
        except Exception:  # pragma: no cover - best effort
            pass
    if not tables:
        raise UnreadableSource("the workbook contains no sheets", code="NO_EXTRACTED_TEXT")
    return tables


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def read_json_table(path: Path) -> list[Table]:
    """Read JSON / JSONL as tables when it is record-shaped.

    A JSON document that is *not* a list of objects still produces a single
    one-row table of its top-level scalar fields plus the nested record lists
    it contains, so a nested export is not silently dropped.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UnreadableSource(f"the file could not be read ({exc})", code="NOT_FOUND") from exc
    if not raw.strip():
        raise UnreadableSource("the file is empty", code="NO_EXTRACTED_TEXT")

    suffix = path.suffix.lower()
    records: list[Any]
    if suffix in {".jsonl", ".ndjson"}:
        records = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        payload: Any = records
    else:
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise UnreadableSource(f"the JSON is malformed ({exc})", code="CORRUPTED") from exc

    return list(_tables_from_json(payload, path.stem))


def _tables_from_json(payload: Any, name: str, depth: int = 0) -> Iterator[Table]:
    if depth > 3:
        return
    if isinstance(payload, list):
        objects = [item for item in payload if isinstance(item, dict)]
        if objects:
            yield _table_from_dicts(objects, name)
            return
        matrix = [item for item in payload if isinstance(item, (list, tuple))]
        if matrix:
            # An array of arrays is a table that lost its header on the way out
            # of whatever produced it. Dropping it because the column names are
            # missing would silently discard real records -- give the columns
            # positional names and let the schema mapper read the values.
            yield _table_from_matrix(matrix, name)
        return
    if isinstance(payload, dict):
        scalars = {
            key: _cell_text(value)
            for key, value in payload.items()
            if not isinstance(value, (dict, list))
        }
        if scalars:
            yield _table_from_dicts([scalars], name)
        for key, value in payload.items():
            if isinstance(value, list) and any(
                isinstance(i, (dict, list, tuple)) for i in value
            ):
                yield from _tables_from_json(value, f"{name}.{key}", depth + 1)
            elif isinstance(value, dict):
                yield from _tables_from_json(value, f"{name}.{key}", depth + 1)


def _table_from_matrix(matrix: list[Any], name: str) -> Table:
    """A headerless array-of-arrays, given positional column names."""
    width = max((len(row) for row in matrix[:SAMPLE_ROWS]), default=0)
    columns = [f"column_{index + 1}" for index in range(width)]
    rows: list[dict[str, Any]] = []
    numbers: list[int] = []
    for index, item in enumerate(matrix[:MAX_ROWS], start=1):
        values = list(item)
        rows.append(
            {
                column: (
                    json.dumps(values[position], ensure_ascii=False)
                    if position < len(values)
                    and isinstance(values[position], (dict, list))
                    else _cell_text(values[position]) if position < len(values) else ""
                )
                for position, column in enumerate(columns)
            }
        )
        numbers.append(index)
    return Table(
        name=name,
        columns=columns,
        rows=rows,
        row_numbers=numbers,
        truncated=len(matrix) > MAX_ROWS,
    )


def _table_from_dicts(objects: list[dict], name: str) -> Table:
    columns: list[str] = []
    for item in objects[:SAMPLE_ROWS]:
        for key in item:
            if key not in columns:
                columns.append(str(key))
    rows: list[dict[str, Any]] = []
    numbers: list[int] = []
    for index, item in enumerate(objects[:MAX_ROWS], start=1):
        flat: dict[str, Any] = {}
        for column in columns:
            value = item.get(column)
            flat[column] = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else _cell_text(value)
            )
        rows.append(flat)
        numbers.append(index)
    return Table(
        name=name,
        columns=columns,
        rows=rows,
        row_numbers=numbers,
        truncated=len(objects) > MAX_ROWS,
    )


def read_tables(path: Path, extension: str | None = None) -> list[Table]:
    """Parse any supported tabular file into one table per sheet/array."""
    ext = (extension or path.suffix).lower()
    if ext in {".csv", ".tsv"}:
        return read_csv(path)
    if ext in {".xlsx", ".xlsm"}:
        return read_xlsx(path)
    if ext == ".xls":
        raise UnreadableSource(
            "legacy .xls workbooks are not supported; re-save the file as .xlsx",
            code="UNSUPPORTED",
        )
    if ext in {".json", ".jsonl", ".ndjson"}:
        return read_json_table(path)
    raise UnreadableSource(f"'{ext}' is not a tabular format", code="UNSUPPORTED")


# ---------------------------------------------------------------------------
# Text / document readers
# ---------------------------------------------------------------------------


def read_plain_text(path: Path) -> TextDocument:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UnreadableSource(f"the file could not be read ({exc})", code="NOT_FOUND") from exc
    text = decode_text(raw)
    return TextDocument(text=text, pages=[text], page_count=1, empty=not text.strip())


def decode_text(raw: bytes) -> str:
    """Decode bytes as text, trying the encodings real datasets actually use.

    Falling straight to ``utf-8`` with ``errors='replace'`` is what fills a
    screen with U+FFFD replacement characters; trying the plausible encodings
    first keeps Devanagari and Latin-1 exports readable.
    """
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_pdf(path: Path) -> TextDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise UnreadableSource("the PDF parser is not installed", code="UNSUPPORTED") from exc
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page must not kill the file
                pages.append("")
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        raise UnreadableSource(
            f"the PDF could not be parsed ({type(exc).__name__}: {exc})", code="CORRUPTED"
        ) from exc
    text = "\n\n".join(pages)
    return TextDocument(
        text=text, pages=pages, page_count=len(pages), empty=not text.strip()
    )


def read_docx(path: Path) -> TextDocument:
    try:
        import docx  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise UnreadableSource("the DOCX parser is not installed", code="UNSUPPORTED") from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableSource(
            f"the DOCX could not be parsed ({type(exc).__name__}: {exc})", code="CORRUPTED"
        ) from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    return TextDocument(text=text, pages=[text], page_count=1, empty=not text.strip())


def read_text(path: Path, extension: str | None = None) -> TextDocument:
    ext = (extension or path.suffix).lower()
    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".docx":
        return read_docx(path)
    if ext == ".doc":
        raise UnreadableSource(
            "legacy .doc files are not supported; re-save the file as .docx or PDF",
            code="UNSUPPORTED",
        )
    return read_plain_text(path)
