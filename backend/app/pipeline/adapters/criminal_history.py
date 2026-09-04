"""Criminal history adapter (PRD 7 / 15).

A criminal-history record is only as trustworthy as the system it came from, so
this adapter enforces the PRD's rule directly: ``source_confidence`` is
``VERIFIED`` **only** when the record was fetched from the authoritative system
(CCTNS) with its transaction id recorded in ``extra['source_txn_id']``.
A manual upload of the same data is truthful but not authoritative, so it is
ingested as ``UNVERIFIED`` and warned about.

If CCTNS is enabled but unavailable, the integration fails loudly with a retry
hint — stale cached criminal records presented as live records would be worse
than no records at all.
"""

from __future__ import annotations

import csv
import io
import json

from app.domain.enums import DocumentType, SourceConfidence
from app.domain.models import ORIGIN_COLUMN, Block, NormalizedDocument
from app.domain.normalize import (
    normalize_account,
    normalize_name,
    normalize_phone,
    normalize_plate,
)
from app.errors import PipelineError
from app.logging import get_logger
from app.pipeline.adapters.protocol import (
    DocumentMeta,
    detect_language,
    pick_column,
    pop_origin,
    strip_origin_column,
    to_ist_iso,
)

log = get_logger("crimelink.adapter.criminal_history")

_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "accused_name", "person_name", "full_name", "criminal_name"),
    "aliases": ("aliases", "alias", "aka", "other_names"),
    "role": ("role", "status", "accused_status", "category"),
    "plate": ("vehicle", "vehicle_no", "plate", "vehicle_registration", "vehicle_plate"),
    "phone": ("phone", "mobile", "mobile_no", "contact_no", "contact"),
    "account": ("account", "account_no", "account_number", "bank_account", "bank_account_no"),
    "bank_code": ("bank_code", "bank", "bank_name", "bank_branch"),
    "address": ("address", "residence", "permanent_address"),
    "ipc_sections": ("ipc_sections", "sections", "ipc", "sections_invoked"),
    "case_ref": ("case_ref", "fir_no", "fir_number", "case_number", "case_no"),
    "case_date": ("case_date", "fir_date", "date", "year"),
    "make": ("vehicle_make", "make", "vehicle_model"),
}


class CriminalHistoryAdapter:
    """Structured criminal-history import (CSV/JSON) → normalised person records."""

    document_type = DocumentType.CRIMINAL_HISTORY

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument:
        rows, kind = self._read(raw)
        blocks: list[Block] = []
        warnings: list[str] = list()
        rendered: list[str] = []
        cursor = 0
        skipped = 0

        for index, row in enumerate(rows, start=1):
            origin = pop_origin(row)
            name = str(row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            aliases = self._split_aliases(row.get("aliases"))
            sections = self._split_sections(row.get("ipc_sections"))
            plate = normalize_plate(str(row.get("plate") or ""))
            phone = normalize_phone(str(row.get("phone") or ""))
            account = normalize_account(str(row.get("account") or ""))
            record = {
                "kind": "person_record",
                "name": name,
                "aliases": aliases,
                "role": str(row.get("role") or "").upper() or None,
                "ipc_sections": sections,
                "case_ref": row.get("case_ref"),
                "case_date": to_ist_iso(row.get("case_date")) if row.get("case_date") else None,
                "valid_from": row.get("valid_from"),
                "valid_to": row.get("valid_to"),
            }
            if plate:
                record["plate"] = plate
                record["make"] = row.get("make")
            if phone:
                record["phone"] = phone
                record["first_seen"] = record["case_date"]
                record["last_seen"] = record["case_date"]
            if account:
                record["account"] = account
                record["bank_code"] = row.get("bank_code")

            text = (
                f"Criminal history: {name}"
                + (f" alias {', '.join(aliases)}" if aliases else "")
                + (f", IPC {', '.join(sections)}" if sections else "")
                + (f", vehicle {plate}" if plate else "")
                + (f", phone {phone}" if phone else "")
                + (f", account {account}" if account else "")
                + (f", case {record['case_ref']}" if record["case_ref"] else "")
            )
            blocks.append(
                Block(kind="record", text=text, offset=cursor, data=record, origin=origin)
            )
            rendered.append(text)
            cursor += len(text) + 1

        if not blocks:
            raise PipelineError(
                "No criminal-history records could be read: every row was missing a name."
            )
        if skipped:
            warnings.append(f"{skipped} row(s) skipped because the name field was empty.")

        confidence, provenance_warning = self._provenance(doc_meta)
        if provenance_warning:
            warnings.append(provenance_warning)

        text = "\n".join(rendered)
        return NormalizedDocument(
            doc_id=doc_meta.doc_id,
            case_id=doc_meta.case_id,
            doc_type=DocumentType.CRIMINAL_HISTORY.value,
            language=detect_language(text, doc_meta.language_hint),
            source_confidence=confidence,
            blocks=blocks,
            parse_warnings=warnings,
            metadata={
                "filename": doc_meta.filename,
                "format": kind,
                "records": len(blocks),
                "source_txn_id": doc_meta.extra.get("source_txn_id"),
            },
        )

    # -------------------------------------------------------------- internals
    @staticmethod
    def _read(raw: bytes) -> tuple[list[dict], str]:
        text = raw.decode("utf-8", errors="replace")
        stripped = text.lstrip()
        if stripped.startswith("["):
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise PipelineError(f"Criminal history JSON is invalid ({exc}).") from exc
            return [CriminalHistoryAdapter._remap(item) for item in payload if isinstance(item, dict)], "JSON"
        if stripped.startswith("{"):
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise PipelineError(f"Criminal history JSON is invalid ({exc}).") from exc
            if isinstance(payload, dict):
                records = payload.get("records") or payload.get("data") or [payload]
                return [CriminalHistoryAdapter._remap(r) for r in records if isinstance(r, dict)], "JSON"
        reader = csv.DictReader(io.StringIO(text))
        headers = strip_origin_column([h for h in (reader.fieldnames or []) if h])
        if not headers:
            raise PipelineError("The criminal-history file has no header row.")
        rows = []
        for row in reader:
            remapped = {
                field: (row.get(pick_column(headers, aliases)) or None)
                for field, aliases in _ALIASES.items()
            }
            # The remap keeps only known fields, so the origin is carried across
            # explicitly rather than being dropped with the unmapped columns.
            remapped[ORIGIN_COLUMN] = row.get(ORIGIN_COLUMN)
            rows.append(remapped)
        return rows, "CSV"

    @staticmethod
    def _remap(record: dict) -> dict:
        """Keep the known fields, and the origin that says where they came from.

        The remap deliberately drops unmapped keys, which silently took the
        reserved origin column with them on the JSON branch -- so JSON-sourced
        records produced blocks that could not be traced back to a corpus row
        even though the CSV branch could.
        """
        remapped = {field: record.get(field) for field in _ALIASES}
        if record.get(ORIGIN_COLUMN) is not None:
            remapped[ORIGIN_COLUMN] = record[ORIGIN_COLUMN]
        return remapped

    @staticmethod
    def _provenance(doc_meta: DocumentMeta) -> tuple[SourceConfidence, str | None]:
        txn = doc_meta.extra.get("source_txn_id")
        requested = doc_meta.source_confidence
        if txn:
            return SourceConfidence.VERIFIED, None
        if requested == SourceConfidence.VERIFIED:
            return (
                SourceConfidence.UNVERIFIED,
                "Record was supplied as VERIFIED but carries no authoritative transaction "
                "id, so it has been downgraded to UNVERIFIED.",
            )
        return requested, None

    @staticmethod
    def _split_aliases(value) -> list[str]:
        if not value:
            return []
        parts = str(value).replace(";", ",").split(",")
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _split_sections(value) -> list[str]:
        if not value:
            return []
        import re

        parts = re.split(r"[,;/&]|\band\b", str(value))
        return [p.strip().upper() for p in parts if p.strip()]


class SurveillanceAdapter:
    """Surveillance log (CSV) → normalised sighting events with locations.

    Sightings are what make RAPID_MOVEMENT detection possible: each row becomes
    an ``Event`` node with a timestamp plus a ``LOCATED_AT`` edge to a location.
    """

    document_type = DocumentType.SURVEILLANCE

    _ALIASES = {
        "person": ("person", "subject", "name", "suspect", "target"),
        "location": ("location", "place", "area", "site", "address"),
        "ts": ("timestamp", "date_time", "datetime", "observed_at", "date", "time"),
        "vehicle_plate": ("vehicle", "vehicle_no", "plate", "vehicle_plate"),
        "description": ("description", "observation", "remarks", "note", "activity"),
    }

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument:
        text = raw.decode("utf-8", errors="replace")
        if text.lstrip().startswith(("[", "{")):
            rows = self._read_json(text)
        else:
            reader = csv.DictReader(io.StringIO(text))
            headers = strip_origin_column([h for h in (reader.fieldnames or []) if h])
            if not headers:
                raise PipelineError("The surveillance log has no header row.")
            rows = [
                {
                    **{
                        field: row.get(pick_column(headers, aliases))
                        for field, aliases in self._ALIASES.items()
                    },
                    ORIGIN_COLUMN: row.get(ORIGIN_COLUMN),
                }
                for row in reader
            ]

        blocks: list[Block] = []
        rendered: list[str] = []
        cursor = 0
        skipped = 0
        for row in rows:
            origin = pop_origin(row)
            person = str(row.get("person") or "").strip()
            ts = to_ist_iso(row.get("ts"))
            location = str(row.get("location") or "").strip()
            if not person or not ts:
                skipped += 1
                continue
            record = {
                "kind": "sighting",
                "person": person,
                "ts": ts,
                "location": location or None,
                "vehicle_plate": row.get("vehicle_plate") or None,
                "description": str(row.get("description") or "Surveillance sighting"),
            }
            rendered_text = (
                f"Surveillance: {person} observed at {location or 'unknown location'} "
                f"on {ts}"
                + (f" with vehicle {record['vehicle_plate']}" if record["vehicle_plate"] else "")
            )
            blocks.append(
                Block(kind="record", text=rendered_text, offset=cursor, data=record, origin=origin)
            )
            rendered.append(rendered_text)
            cursor += len(rendered_text) + 1

        if not blocks:
            raise PipelineError(
                "No usable surveillance sightings: every row was missing a subject or a timestamp."
            )
        warnings = []
        if skipped:
            warnings.append(f"{skipped} row(s) skipped (missing subject or timestamp).")
        return NormalizedDocument(
            doc_id=doc_meta.doc_id,
            case_id=doc_meta.case_id,
            doc_type=DocumentType.SURVEILLANCE.value,
            language=detect_language("\n".join(rendered), doc_meta.language_hint),
            source_confidence=doc_meta.source_confidence,
            blocks=blocks,
            parse_warnings=warnings,
            metadata={"filename": doc_meta.filename, "sightings": len(blocks)},
        )

    @staticmethod
    def _read_json(text: str) -> list[dict]:
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise PipelineError(f"Surveillance JSON is invalid ({exc}).") from exc
        if isinstance(payload, dict):
            payload = payload.get("sightings") or payload.get("records") or [payload]
        out = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            out.append({field: item.get(field) for field in SurveillanceAdapter._ALIASES})
        return out
