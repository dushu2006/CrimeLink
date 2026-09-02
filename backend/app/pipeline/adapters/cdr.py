"""CDR (call detail record) adapter (PRD 7).

Telecom operators ship incompatible CDR formats, so a per-operator schema
registry maps column aliases onto CrimeLink's canonical field names.  When no
registered schema covers the header row, schema inference is attempted; if the
result is still ambiguous the document is **failed with the header row quoted in
the reason** so an administrator can register the new format.

Two rules from the PRD that shape this file:

* **Bad-row tolerance is 5%.**  A CDR that is 40% unparseable is more dangerous
  than a failed one, because it looks complete while quietly missing calls.  Above
  the threshold the whole file fails and lands in quarantine.
* **Timestamps are normalised to IST**, then stored as UTC ISO-8601.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Any

from app.domain.enums import DocumentType
from app.domain.models import Block, NormalizedDocument
from app.domain.normalize import normalize_phone
from app.errors import PipelineError
from app.logging import get_logger
from app.pipeline.adapters.protocol import (
    DocumentMeta,
    detect_language,
    pick_column,
    to_ist_iso,
)
from app.pipeline.extraction.gazetteers import CDR_SCHEMAS

log = get_logger("crimelink.adapter.cdr")

MAX_BAD_ROW_RATIO = 0.05

_REQUIRED_FIELDS = ("caller", "callee", "timestamp")


class CDRAdapter:
    """CSV/XML call detail records → normalised call records."""

    document_type = DocumentType.CDR

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument:
        rows, headers, schema_name = self._read_rows(raw, doc_meta)
        mapping, coverage = self._resolve_schema(headers)
        if mapping is None or not self._has_required(mapping):
            quoted = ", ".join(headers[:12]) if headers else "(no header row found)"
            raise PipelineError(
                "Unrecognised CDR format. Could not reliably identify the caller, "
                f"callee and timestamp columns. Header row received: [{quoted}]. "
                "Register this operator's schema, or export using a supported format."
            )

        blocks: list[Block] = []
        warnings: list[str] = []
        bad_rows = 0
        text_cursor = 0
        rendered_rows: list[str] = []

        for index, row in enumerate(rows, start=1):
            record = self._record_from_row(row, mapping)
            if record is None:
                bad_rows += 1
                if bad_rows <= 5:
                    warnings.append(f"Row {index} skipped: caller/callee/date unusable.")
                continue
            rendered = (
                f"Call {record['caller']} -> {record['callee']} at {record['ts']} "
                f"for {record['duration_s']}s ({record['direction']})"
            )
            blocks.append(
                Block(
                    kind="record",
                    text=rendered,
                    offset=text_cursor,
                    data={"kind": "call", **record},
                )
            )
            rendered_rows.append(rendered)
            text_cursor += len(rendered) + 1

        total = len(rows)
        if total == 0:
            raise PipelineError("The CDR file contains no data rows.")
        bad_ratio = bad_rows / total
        if bad_ratio > MAX_BAD_ROW_RATIO:
            raise PipelineError(
                f"{bad_rows} of {total} rows ({bad_ratio:.0%}) could not be parsed, which "
                "exceeds the 5% tolerance. A mostly-unreadable CDR is quarantined rather "
                "than partially ingested, because a partial call graph looks complete but "
                "is not."
            )
        if bad_rows:
            warnings.append(f"{bad_rows} malformed row(s) skipped.")

        text = "\n".join(rendered_rows)
        return NormalizedDocument(
            doc_id=doc_meta.doc_id,
            case_id=doc_meta.case_id,
            doc_type=DocumentType.CDR.value,
            language=detect_language(text, doc_meta.language_hint),
            source_confidence=doc_meta.source_confidence,
            blocks=blocks,
            parse_warnings=warnings,
            metadata={
                "filename": doc_meta.filename,
                "schema": schema_name,
                "rows": total,
                "bad_rows": bad_rows,
                "columns": {key: mapping.get(key) for key in _REQUIRED_FIELDS},
            },
        )

    # ------------------------------------------------------------------- io
    def _read_rows(self, raw: bytes, doc_meta: DocumentMeta):
        """Read CSV or XML into (rows, headers, schema_name)."""
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        stripped = text.lstrip()
        if stripped.startswith("<"):
            return self._read_xml(text)
        sample = stripped[:4096]
        delimiter = ","
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        headers = [h for h in (reader.fieldnames or []) if h]
        if not headers:
            raise PipelineError("The CDR file has no header row.")
        rows = [dict(row) for row in reader]
        return rows, headers, "CSV"

    @staticmethod
    def _read_xml(text: str):
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise PipelineError(f"The CDR XML could not be parsed ({exc}).") from exc
        rows: list[dict[str, Any]] = []
        for element in root.iter():
            if not len(element):
                continue
            child_tags = {child.tag.lower() for child in element}
            if {"calling_number", "called_number"} & child_tags or {
                "a_party",
                "b_party",
            } & child_tags:
                rows.append({child.tag: (child.text or "") for child in element})
        if not rows:
            raise PipelineError(
                "The CDR XML contained no call elements with caller/callee children."
            )
        headers = sorted({key for row in rows for key in row})
        return rows, headers, "XML"

    # --------------------------------------------------------------- schema
    def _resolve_schema(self, headers: list[str]) -> tuple[dict[str, str] | None, float]:
        """Match the header row against the registry, then fall back to inference."""
        best_name: str | None = None
        best_mapping: dict[str, str] = {}
        best_coverage = 0.0
        for name, schema in CDR_SCHEMAS.items():
            mapping: dict[str, str] = {}
            matched = 0
            for field, aliases in schema.items():
                column = pick_column(headers, aliases)
                if column:
                    mapping[field] = column
                    matched += 1
            coverage = matched / len(schema)
            required_hit = sum(1 for field in _REQUIRED_FIELDS if field in mapping)
            score = coverage + (0.5 if required_hit == len(_REQUIRED_FIELDS) else 0.0)
            if score > best_coverage:
                best_coverage, best_mapping, best_name = score, mapping, name
        # Require the three mandatory columns to exist.
        if best_mapping and not self._has_required(best_mapping):
            return None, best_coverage
        return (best_mapping or None), best_coverage

    @staticmethod
    def _has_required(mapping: dict[str, str]) -> bool:
        return all(field in mapping for field in _REQUIRED_FIELDS)

    # ----------------------------------------------------------------- rows
    @staticmethod
    def _record_from_row(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any] | None:
        caller = normalize_phone(str(row.get(mapping["caller"], "") or ""))
        callee = normalize_phone(str(row.get(mapping["callee"], "") or ""))
        if not caller or not callee:
            return None
        ts = to_ist_iso(row.get(mapping["timestamp"]))
        if ts is None:
            return None
        duration_raw = row.get(mapping.get("duration", ""), 0) if mapping.get("duration") else 0
        return {
            "caller": caller,
            "callee": callee,
            "ts": ts,
            "duration_s": _as_int(duration_raw),
            "direction": str(
                row.get(mapping.get("direction", ""), "OUTGOING") or "OUTGOING"
            ).upper(),
            "imei": row.get(mapping.get("imei", "")) if mapping.get("imei") else None,
            "cell_id": row.get(mapping.get("cell_id", "")) if mapping.get("cell_id") else None,
        }


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0
