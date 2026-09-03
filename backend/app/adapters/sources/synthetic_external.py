"""External synthetic corpus — a filesystem dataset wrapped as a SourceAdapter.

This adapter turns a checked-out *external* synthetic corpus (for example the
``CrimeLink_Synthetic_Corpus_v1`` directory under ``backend/``) into
:class:`SourceRecord` objects, so the dataset enters CrimeLink
through exactly the same six-stage ingestion pipeline as every other source:

    external corpus → source adapter → upload_document → six-stage pipeline
                    → relational store → graph store → entity resolution

Rules that are enforced here, before anything touches the database:

* **Only ``operational/`` and ``documents/`` are ingestion sources.**  Any
  path component named ``ground_truth`` or ``metadata`` is refused outright.
  Ground truth is evaluation-only data and must never reach the operational
  stores; dataset metadata may describe the corpus but is never imported as
  investigation records.
* **Nothing is invented.**  Files are classified by *content signatures* —
  CSV header aliases and JSON structural markers shared with the real
  pipeline adapters (``app.pipeline.adapters.*``) — never by assuming files
  with particular names must exist.  A file that matches no known signature
  is reported as ``unsupported`` with its header row quoted, so an operator
  sees the exact gap instead of a silent skip.
* **Failures are explicit.**  A missing corpus root, a missing
  ``operational/``/``documents/`` directory or an unreadable file produces a
  clear error naming the path, mirroring the failure style of the pipeline
  adapters (PRD principle P4).
* **Provenance is preserved.**  Every record carries
  ``source_environment="synthetic"`` and ``SourceConfidence.SYNTHETIC`` so
  synthetic data can never masquerade as operational (government) records.

The adapter never writes to the database or the graph, and it never modifies
the corpus on disk.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.enums import DocumentType, SourceConfidence
from app.domain.models import ORIGIN_COLUMN, OriginRef
from app.logging import get_logger

from .protocol import SourceAdapter, SourceRecord
from .registry import register_source_adapter

log = get_logger("crimelink.sources.external")

# Directories that are operational ingestion sources — nothing else is walked.
OPERATIONAL_DIR = "operational"
DOCUMENTS_DIR = "documents"
INGESTION_SECTIONS = (OPERATIONAL_DIR, DOCUMENTS_DIR)

# Path components that must never enter the operational pipeline, even if the
# configured root points deeper into the corpus than intended.  Comparison is
# separator-insensitive (``ground-truth`` == ``ground_truth``).
NEVER_INGEST_COMPONENTS = frozenset({"groundtruth", "metadata", "evaluation"})

# Corpus-local documentation (README.md / SCHEMA.md) is operator-facing;
# listing it as ignored keeps the scan report truthful without pretending
# these files are investigation records.
IGNORED_ROOT_DOCUMENTS = frozenset({"readme", "schema", "license", "changelog"})

# Relational tables that describe the corpus but are not themselves uploaded
# as investigation documents.  They are used to create Case rows and to route
# other files; ingesting them as FIR/CSV blobs would collapse 36 cases into
# one document.
REFERENCE_TABLES = frozenset(
    {
        "cases.csv",
        "case_members.csv",
        "documents.csv",
        "person_organizations.csv",
        "persons.csv",
        "phones.csv",
        "vehicles.csv",
        "accounts.csv",
        "locations.csv",
        "organizations.csv",
    }
)

# Loader-injected bookkeeping keys.  Prefixed so they can never collide with a
# real corpus column, and stripped before any row is rendered.
ROW_NUMBER_KEY = "__crimelink_row__"
SOURCE_FILE_KEY = "__crimelink_file__"

CASE_GROUP_FALLBACK = "CORPUS"  # root name is used instead when it is known

_TEXT_EXTENSIONS = {".txt", ".md", ".text"}
_OFFICE_EXTENSIONS = {".pdf", ".docx"}
_SUPPORTED_EXTENSIONS = _TEXT_EXTENSIONS | _OFFICE_EXTENSIONS | {".csv", ".json"}

_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/plain",
    ".text": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_DATASET_TABLE_HEADERS: dict[str, frozenset[str]] = {
    "accounts.csv": frozenset({"account_id", "account_number", "holder_person_id", "bank_code", "account_status"}),
    "case_members.csv": frozenset({"case_member_id", "case_id", "person_id", "role"}),
    "cases.csv": frozenset({"case_id", "case_number", "registered_date", "case_type", "police_station", "city", "status"}),
    "cdr.csv": frozenset({"cdr_id", "timestamp", "from_phone_id", "to_phone_id", "duration_seconds", "call_type", "cell_location_id", "case_id"}),
    "documents.csv": frozenset({"document_id", "case_id", "document_type", "file_path", "language", "source_environment"}),
    "intelligence_reports.csv": frozenset({"report_id", "report_date", "subject_person_id", "location_id", "case_id", "source_type", "summary"}),
    "locations.csv": frozenset({"location_id", "name", "city", "state", "latitude", "longitude"}),
    "organizations.csv": frozenset({"organization_id", "name", "organization_type", "city", "state"}),
    "person_organizations.csv": frozenset({"person_org_id", "person_id", "organization_id", "role", "start_date", "end_date"}),
    "persons.csv": frozenset({"person_id", "full_name", "gender", "dob", "address", "city", "state", "status"}),
    "phones.csv": frozenset({"phone_id", "phone_number", "owner_person_id", "status", "source"}),
    "transactions.csv": frozenset({"transaction_id", "timestamp", "from_account_id", "to_account_id", "amount_inr", "transaction_type", "location_id", "case_id"}),
    "vehicle_sightings.csv": frozenset({"sighting_id", "vehicle_id", "location_id", "timestamp", "case_id", "source"}),
    "vehicles.csv": frozenset({"vehicle_id", "registration_number", "vehicle_type", "owner_person_id", "color"}),
}


class ExternalCorpusError(RuntimeError):
    """Fatal corpus problem: missing root, missing sections, unreadable file."""


@dataclass(slots=True)
class DiscoveredFile:
    """One file found while scanning the corpus."""

    path: Path
    relative_path: str            # posix-style, relative to the corpus root
    section: str | None           # "operational" | "documents" | None (ignored root file)
    size_bytes: int
    sha256: str | None            # None when the file could not be read
    status: str                   # "accepted" | "unsupported" | "unreadable" | "excluded" | "ignored" | "reference"
    document_type: DocumentType | None = None
    case_key: str | None = None   # directory grouping used for case assignment
    reason: str | None = None


@dataclass(slots=True)
class CorpusScan:
    """Result of discovering/validating/classifying the whole corpus."""

    root: Path
    issues: list[str] = field(default_factory=list)   # fatal problems
    warnings: list[str] = field(default_factory=list)
    files: list[DiscoveredFile] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def accepted(self) -> list[DiscoveredFile]:
        return [f for f in self.files if f.status == "accepted"]

    def by_status(self, status: str) -> list[DiscoveredFile]:
        return [f for f in self.files if f.status == status]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for f in self.files:
            counts[f.status] = counts.get(f.status, 0) + 1
        operational = [f for f in self.files if f.section == OPERATIONAL_DIR]
        documents = [f for f in self.files if f.section == DOCUMENTS_DIR]
        return {
            "root": str(self.root),
            "dataset_name": self.root.name,
            "ok": self.ok,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "counts": counts,
            "files_discovered": len(self.files),
            "operational_files": len(operational),
            "document_files": len(documents),
            "accepted_files": len(self.accepted),
            "reference_files": len(self.by_status("reference")),
            "excluded_evaluation_files": len(self.by_status("excluded")),
            "unsupported_files": len(self.by_status("unsupported"))
            + len(self.by_status("unreadable")),
            "ground_truth_excluded": any(
                "ground_truth" in f.relative_path.replace("\\", "/")
                for f in self.files
            ),
            "schema_tables": sorted(
                {
                    Path(f.relative_path).name
                    for f in operational
                    if f.path.suffix.lower() == ".csv"
                }
            ),
        }


def _normal_component(value: str) -> str:
    return value.lower().replace("-", "").replace("_", "").replace(" ", "")


def _has_forbidden_component(relative: Path) -> str | None:
    for part in relative.parts[:-1]:  # directory components only
        if _normal_component(part) in NEVER_INGEST_COMPONENTS:
            return part
    return None


def _case_group_for(section: str, relative: Path, corpus_root_name: str) -> str:
    """Directory grouping used to assign files to synthetic cases.

    ``operational/CELL-ALPHA/cdr.csv`` groups under ``CELL-ALPHA``; a file
    directly inside ``operational/`` groups under the corpus directory name,
    so two different corpora on disk cannot collapse into the same case.
    """
    parent = relative.parent
    try:
        below = parent.relative_to(section)
    except ValueError:
        below = Path()
    parts = [p for p in below.parts if p not in ("", ".")]
    raw = "-".join(parts) if parts else (corpus_root_name or CASE_GROUP_FALLBACK)
    return _sanitize_group(raw)


def _sanitize_group(raw: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(raw)).strip("-").upper()
    return (cleaned or CASE_GROUP_FALLBACK)[:100]


def _is_crimelink_relational_corpus(scan: CorpusScan) -> bool:
    """True when operational/ contains the documented cases + persons tables."""
    names = {
        Path(entry.relative_path).name.lower()
        for entry in scan.files
        if entry.section == OPERATIONAL_DIR
    }
    return "cases.csv" in names and "persons.csv" in names


def _load_operational_tables(scan: CorpusScan) -> dict[str, list[dict[str, str]]]:
    """Read every operational CSV, including reference tables."""
    tables: dict[str, list[dict[str, str]]] = {}
    for entry in scan.files:
        if entry.section != OPERATIONAL_DIR:
            continue
        if entry.status in {"excluded", "unreadable"}:
            continue
        if entry.path.suffix.lower() != ".csv":
            continue
        try:
            with entry.path.open(newline="", encoding="utf-8-sig") as handle:
                # ``index`` starts at 2: line 1 is the header, so the number
                # recorded here is the line an investigator sees in an editor.
                tables[entry.path.name.lower()] = [
                    {
                        **{str(k): (v or "").strip() for k, v in row.items() if k},
                        ROW_NUMBER_KEY: index,
                        SOURCE_FILE_KEY: entry.relative_path,
                    }
                    for index, row in enumerate(csv.DictReader(handle), start=2)
                ]
        except (OSError, UnicodeError, csv.Error):
            continue
    return tables


def _encode_origin(origin: "OriginRef | None") -> str:
    return origin.encode() if origin is not None else ""


def _origin_for(
    row: dict[str, str], *, record_id_field: str, fields: list[str]
) -> OriginRef | None:
    """Build an :class:`OriginRef` from a corpus row annotated by the loader."""
    source_file = row.get(SOURCE_FILE_KEY)
    if not source_file:
        return None
    return OriginRef(
        file=str(source_file),
        row=row.get(ROW_NUMBER_KEY),
        record_id=row.get(record_id_field) or None,
        fields=[f for f in fields if row.get(f)],
        values={f: row.get(f, "") for f in fields if row.get(f)},
    )


def _map_case_status(raw: str) -> str:
    value = (raw or "OPEN").strip().upper().replace(" ", "_")
    if value in {"OPEN", "UNDER_REVIEW", "CLOSED"}:
        return value
    return "OPEN"


def _synthetic_case_title(row: dict[str, str]) -> str:
    kind = (row.get("case_type") or "CASE").strip() or "CASE"
    station = (row.get("police_station") or "").strip()
    city = (row.get("city") or "").strip()
    where = ", ".join(part for part in (station, city) if part)
    label = f"{kind} — {where}" if where else kind
    return f"[SYNTHETIC] {label}"


def _document_type_from_manifest(raw: str) -> DocumentType:
    value = (raw or "").strip().upper().replace("-", "_")
    if value in {"INTEL", "INTELLIGENCE"}:
        return DocumentType.INTEL
    if value in {"CDR"}:
        return DocumentType.CDR
    if value in {"FINANCIAL", "BANK"}:
        return DocumentType.FINANCIAL
    if value in {"SURVEILLANCE"}:
        return DocumentType.SURVEILLANCE
    if value in {"CRIMINAL_HISTORY"}:
        return DocumentType.CRIMINAL_HISTORY
    return DocumentType.FIR


def _person_history_rows(
    case_row: dict[str, str],
    members: list[dict[str, str]],
    person_by_id: dict[str, dict[str, str]],
    phones_by_person: dict[str, list[dict[str, str]]],
    vehicles_by_person: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for member in members:
        person = person_by_id.get(member.get("person_id", ""))
        if not person or not person.get("full_name"):
            continue
        phones = phones_by_person.get(person["person_id"], [])
        vehicles = vehicles_by_person.get(person["person_id"], [])
        address = ", ".join(
            part for part in (person.get("address"), person.get("city"), person.get("state")) if part
        )
        rows.append(
            {
                "name": person.get("full_name", ""),
                "aliases": "",
                "role": member.get("role") or person.get("status") or "",
                "case_ref": case_row.get("case_number", ""),
                "case_date": case_row.get("registered_date", ""),
                "phone": phones[0].get("phone_number", "") if phones else "",
                "plate": vehicles[0].get("registration_number", "") if vehicles else "",
                "address": address,
                # The authoritative origin of a person row is persons.csv, not
                # the case_members join row that selected it.
                ORIGIN_COLUMN: _encode_origin(
                    _origin_for(
                        person,
                        record_id_field="person_id",
                        fields=["full_name", "gender", "city", "state", "status"],
                    )
                ),
            }
        )
    return rows


def _render_case_overview(
    case_row: dict[str, str],
    members: list[dict[str, str]],
    person_by_id: dict[str, dict[str, str]],
    phones_by_person: dict[str, list[dict[str, str]]],
    vehicles_by_person: dict[str, list[dict[str, str]]],
    orgs_by_person: dict[str, list[dict[str, str]]],
    org_by_id: dict[str, dict[str, str]],
) -> str:
    lines = [
        "[SYNTHETIC DEVELOPMENT RECORD]",
        f"Case Number: {case_row.get('case_number', '')}",
        f"Type: {case_row.get('case_type', '')}",
        f"Police Station: {case_row.get('police_station', '')}",
        f"City: {case_row.get('city', '')}",
        f"Registered: {case_row.get('registered_date', '')}",
        f"Status: {case_row.get('status', '')}",
        "",
        "Members:",
    ]
    if not members:
        lines.append("- (none listed in case_members.csv)")
    for member in members:
        person = person_by_id.get(member.get("person_id", ""), {})
        name = person.get("full_name") or member.get("person_id") or "unknown"
        role = member.get("role") or ""
        phones = phones_by_person.get(person.get("person_id", ""), [])
        vehicles = vehicles_by_person.get(person.get("person_id", ""), [])
        extras: list[str] = []
        if phones:
            extras.append(f"phone {phones[0].get('phone_number', '')}")
        if vehicles:
            extras.append(f"vehicle {vehicles[0].get('registration_number', '')}")
        extra = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"- {name} ({role}){extra}")
    org_lines: list[str] = []
    for member in members:
        pid = member.get("person_id", "")
        person = person_by_id.get(pid, {})
        name = person.get("full_name") or pid
        for link in orgs_by_person.get(pid, []):
            org = org_by_id.get(link.get("organization_id", ""), {})
            org_name = org.get("name") or link.get("organization_id") or ""
            if not org_name:
                continue
            role = link.get("role") or "MEMBER"
            org_lines.append(f"- {name} is {role} at {org_name}")
    if org_lines:
        lines.extend(["", "Organizations:"])
        lines.extend(org_lines)
    lines.append("")
    lines.append("This record is synthetic development data, not operational police data.")
    return "\n".join(lines)


def _render_intel(
    rows: list[dict[str, str]],
    person_by_id: dict[str, dict[str, str]],
    location_by_id: dict[str, dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    """Render intel rows to text, plus the line range each row occupies.

    Free text cannot carry a reserved column, so the origin of each report is
    returned alongside as ``{line_start, line_end, origin}``.  Line numbers are
    1-based and inclusive, matching what the source viewer displays.
    """
    line_origins: list[dict[str, Any]] = []
    blocks: list[str] = ["[SYNTHETIC] Intelligence reports"]
    # Block 0 is the header line; blocks are later joined with a blank line.
    cursor = 1
    for row in rows:
        subject = person_by_id.get(row.get("subject_person_id", ""), {}).get("full_name") or row.get(
            "subject_person_id", ""
        )
        location = location_by_id.get(row.get("location_id", ""), {}).get("name") or row.get(
            "location_id", ""
        )
        rendered = "\n".join(
            [
                f"Report {row.get('report_id', '')} ({row.get('report_date', '')})",
                f"Subject: {subject}",
                f"Location: {location}",
                f"Source: {row.get('source_type', '')}",
                f"Summary: {row.get('summary', '')}",
            ]
        )
        blocks.append(rendered)
        height = rendered.count("\n") + 1
        # +1 for the blank separator line that precedes every block after the
        # header, so `start` is the real line number in the joined document.
        start = cursor + 1
        origin = _origin_for(
            row,
            record_id_field="report_id",
            fields=["report_date", "subject_person_id", "location_id", "source_type", "summary"],
        )
        if origin is not None:
            line_origins.append(
                {"line_start": start, "line_end": start + height - 1, "origin": origin.to_dict()}
            )
        cursor = start + height - 1
    return "\n\n".join(blocks), line_origins


def _read_csv_headers(text: str) -> list[str]:
    sample = text[:4096]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        return []
    return [h.strip() for h in header_row if h and h.strip()]


def _classify_csv(headers: list[str]) -> tuple[DocumentType | None, str]:
    """Match a CSV header row against the pipeline adapters' own schemata.

    Reuses the alias tables of the real document adapters so classification
    stays consistent with what the pipeline can actually parse: when an
    adapter gains support for a new column alias, classification follows.

    Ordering matters: the financial triple (payer/beneficiary/amount) is the
    most specific structured signature, because the generic CDR schema also
    accepts vague ``from``/``to``/``date`` aliases that would otherwise claim
    a bank statement.  Surveillance vs criminal history — which share generic
    name/phone/date columns — is decided by each side's *distinctive* fields
    (location/observation vs aliases/IPC sections/case reference).
    """
    # Local imports keep module import cheap and cycle-free.
    from app.pipeline.adapters.cdr import CDRAdapter
    from app.pipeline.adapters.criminal_history import (
        _ALIASES as HISTORY_ALIASES,
    )
    from app.pipeline.adapters.criminal_history import (
        SurveillanceAdapter,
    )
    from app.pipeline.adapters.financial import _ALIASES as FIN_ALIASES
    from app.pipeline.adapters.protocol import pick_column

    if not headers:
        return None, "the CSV file has no header row"

    # The checked-out CrimeLink corpus uses relational tables rather than the
    # generic operator exports below.  Match the complete, documented header
    # set before applying generic aliases so these tables are normalized by
    # records_from_scan without weakening validation for unknown CSVs.
    normalized_headers = frozenset(re.sub(r"[^a-z0-9]+", "", h.lower()) for h in headers)
    for filename, expected in _DATASET_TABLE_HEADERS.items():
        normalized_expected = frozenset(re.sub(r"[^a-z0-9]+", "", h) for h in expected)
        if normalized_headers == normalized_expected:
            if filename == "cdr.csv":
                return DocumentType.CDR, "matched CrimeLink corpus CDR table"
            if filename == "transactions.csv":
                return DocumentType.FINANCIAL, "matched CrimeLink corpus transaction table"
            if filename == "vehicle_sightings.csv":
                return DocumentType.SURVEILLANCE, "matched CrimeLink corpus vehicle-sighting table"
            if filename == "persons.csv":
                return DocumentType.CRIMINAL_HISTORY, "matched CrimeLink corpus persons table"
            if filename == "intelligence_reports.csv":
                return DocumentType.INTEL, "matched CrimeLink corpus intelligence-report table"
            return DocumentType.FIR, f"matched CrimeLink corpus table '{filename}'"

    quoted = ", ".join(headers[:12])

    # 1. Financial — payer account, beneficiary account and amount must resolve.
    fin = {field: pick_column(headers, aliases) for field, aliases in FIN_ALIASES.items()}
    if fin["from_account"] and fin["to_account"] and fin["amount"]:
        return DocumentType.FINANCIAL, "matched financial statement columns"

    # 2. CDR — delegate to the adapter's own schema resolution (registry of
    #    operator formats + inference); it enforces caller/callee/timestamp.
    mapping, _coverage = CDRAdapter()._resolve_schema(headers)
    if mapping is not None:
        return DocumentType.CDR, "matched CDR column schema"

    # 3. Surveillance vs criminal history, decided by matched alias breadth
    #    and distinctive fields rather than declaration order.
    surv = {
        field: pick_column(headers, aliases)
        for field, aliases in SurveillanceAdapter._ALIASES.items()
    }
    hist = {field: pick_column(headers, aliases) for field, aliases in HISTORY_ALIASES.items()}
    surv_ok = bool(surv["person"] and surv["ts"])
    hist_ok = bool(hist["name"])
    if surv_ok or hist_ok:
        surv_score = sum(1 for column in surv.values() if column) + 2 * sum(
            1 for field in ("location", "description", "vehicle_plate") if surv[field]
        )
        hist_score = sum(1 for column in hist.values() if column) + 2 * sum(
            1 for field in ("aliases", "ipc_sections", "case_ref", "role") if hist[field]
        )
        if surv_ok and (not hist_ok or surv_score > hist_score):
            return DocumentType.SURVEILLANCE, "matched surveillance log columns"
        if hist_ok:
            return DocumentType.CRIMINAL_HISTORY, "matched criminal-history columns"

    msg = (
        "no known operational CSV schema matched; header row received: "
        f"[{quoted}]. Register a mapping in the pipeline adapters or fix the file."
    )
    return None, msg


def _classify_json(payload: Any) -> tuple[DocumentType | None, str]:
    """Classify a parsed JSON document by structural signature."""
    from app.pipeline.adapters.criminal_history import (
        _ALIASES as HISTORY_ALIASES,
    )
    from app.pipeline.adapters.criminal_history import (
        SurveillanceAdapter,
    )
    from app.pipeline.adapters.social_media import SocialMediaAdapter

    platform = SocialMediaAdapter._detect_platform(payload)
    if platform != "UNKNOWN":
        return DocumentType.SOCIAL_MEDIA, f"matched {platform} export signature"

    items: list[dict] = []
    if isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("records", "data", "sightings"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = [item for item in candidate if isinstance(item, dict)]
                break
        else:
            items = [payload]
    if not items:
        return None, "the JSON document contains no recognisable record objects"

    def _keys(item: dict) -> set[str]:
        import re

        return {re.sub(r"[^a-z0-9]+", "", str(k).lower()) for k in item}

    sample = items[:10]

    surv_keys = {
        alias.replace("_", "") for aliases in SurveillanceAdapter._ALIASES.values() for alias in aliases
    }
    if all(_keys(item) & surv_keys for item in sample) and any(
        _keys(item) & {"person", "subject", "name", "suspect", "target"} for item in sample
    ) and any(_keys(item) & {"timestamp", "datetime", "observedat", "date", "time"} for item in sample):
        return DocumentType.SURVEILLANCE, "matched surveillance sighting records"

    name_keys = {alias.replace("_", "") for alias in HISTORY_ALIASES["name"]}
    if any(_keys(item) & name_keys for item in sample):
        return DocumentType.CRIMINAL_HISTORY, "matched criminal-history records"

    return None, "the JSON document matches no known export signature"


def _classify(
    path: Path, raw: bytes
) -> tuple[DocumentType | None, str, str | None]:
    """Return (document_type, basis, error_reason)."""
    ext = path.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        lowered = path.name.lower()
        if "intel" in lowered:
            return DocumentType.INTEL, "text document; filename marks it as intelligence", None
        return DocumentType.FIR, "free-text investigation document", None
    if ext in _OFFICE_EXTENSIONS:
        return DocumentType.FIR, "office document handled by the text document adapter", None
    if ext == ".csv":
        text = raw.decode("utf-8", errors="replace")
        headers = _read_csv_headers(text)
        dtype, reason = _classify_csv(headers)
        return dtype, reason, None if dtype else reason
    if ext == ".json":
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            reason = f"the JSON file is malformed ({exc})"
            return None, reason, reason
        dtype, reason = _classify_json(payload)
        return dtype, reason, None if dtype else reason
    return None, f"unsupported file extension '{ext}'", f"unsupported file extension '{ext}'"


@dataclass
class ExternalSyntheticCorpusAdapter(SourceAdapter):
    """Source adapter that reads an external synthetic corpus from disk.

    ``root`` resolution order: the ``root`` constructor/``iter_records``
    option wins; otherwise ``settings.synthetic_data_root`` is used (relative
    paths resolve against the CrimeLink repository root, so
    ``backend/CrimeLink_Synthetic_Corpus_v1`` works out of the box).
    """

    name: str = "synthetic_external"
    source_environment: str = "synthetic"
    root: str | Path | None = None
    case_prefix: str = "SYN-EXT"

    # ------------------------------------------------------------------ root
    def resolve_root(self, override: str | Path | None = None) -> Path:
        candidate = override if override is not None else self.root
        if candidate is not None:
            path = Path(candidate).expanduser()
            if not path.is_absolute():
                from app.config import REPO_ROOT

                path = (REPO_ROOT / path).resolve()
            return path.resolve()
        from app.config import get_settings

        return get_settings().resolved_synthetic_data_root

    def case_number_for(self, case_key: str) -> str:
        return f"{self.case_prefix}/{case_key}"[:120]

    # ------------------------------------------------------------------ scan
    def scan(self, root: str | Path | None = None) -> CorpusScan:
        """Discover, validate and classify every ingestible file.

        Pure filesystem operation: no database access, no corpus mutation.
        """
        resolved = self.resolve_root(root)
        result = CorpusScan(root=resolved)
        if not resolved.exists():
            result.issues.append(
                f"External synthetic corpus root does not exist: {resolved}. "
                "Set CRIMELINK_SYNTHETIC_DATA_ROOT to the corpus directory "
                "(e.g. backend/CrimeLink_Synthetic_Corpus_v1 relative to the repo)."
            )
            return result
        if not resolved.is_dir():
            result.issues.append(
                f"External synthetic corpus root is not a directory: {resolved}"
            )
            return result

        for section in INGESTION_SECTIONS:
            if not (resolved / section).is_dir():
                result.issues.append(
                    f"Expected corpus subdirectory is missing: {resolved / section}. "
                    "The corpus must provide operational/ and documents/."
                )

        seen_hashes: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(resolved, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d != "__MACOSX"
            )
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                file_path = base / name
                try:
                    relative = file_path.relative_to(resolved)
                except ValueError:  # pragma: no cover - defensive
                    continue
                discovered = self._inspect_file(
                    file_path, relative, resolved, result, seen_hashes
                )
                result.files.append(discovered)

        if result.ok and not result.accepted:
            result.warnings.append(
                "No supported files were found under operational/ or documents/; "
                "there is nothing to ingest."
            )
        return result

    def _inspect_file(
        self,
        file_path: Path,
        relative: Path,
        corpus_root: Path,
        scan: CorpusScan,
        seen_hashes: dict[str, str],
    ) -> DiscoveredFile:
        size = 0
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            return self._entry(
                file_path, relative, None, size, None, "unreadable", reason=str(exc)
            )

        top = relative.parts[0] if relative.parts else ""
        forbidden = _has_forbidden_component(relative)
        if forbidden is not None:
            return self._entry(
                file_path, relative, None, size, None, "excluded",
                reason=(
                    f"'{forbidden}' is evaluation/metadata material and is never "
                    "ingested into the operational pipeline"
                ),
            )
        if top not in INGESTION_SECTIONS:
            stem = file_path.stem.lower()
            if stem in IGNORED_ROOT_DOCUMENTS or relative.parent.parts:
                return self._entry(
                    file_path, relative, None, size, None, "ignored",
                    reason="outside operational/ and documents/ (corpus documentation)",
                )
            return self._entry(
                file_path, relative, None, size, None, "ignored",
                reason="outside operational/ and documents/",
            )

        # Symlink jail: never follow a link that escapes the corpus root.
        real = file_path.resolve()
        if not real.is_relative_to(corpus_root):
            return self._entry(
                file_path, relative, top, size, None, "excluded",
                reason="symbolic link resolves outside the corpus root",
            )

        try:
            raw = file_path.read_bytes()
        except OSError as exc:
            return self._entry(
                file_path, relative, top, size, None, "unreadable", reason=str(exc)
            )
        digest = hashlib.sha256(raw).hexdigest()

        if file_path.name.lower() in REFERENCE_TABLES:
            return self._entry(
                file_path, relative, top, size, digest, "reference",
                reason=(
                    "CrimeLink corpus reference table; used to create cases and "
                    "route records, not ingested as a document"
                ),
            )

        ext = file_path.suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            return self._entry(
                file_path, relative, top, size, digest, "unsupported",
                reason=(
                    f"file extension '{ext or '(none)'}' is not supported; "
                    "ingestible formats are CSV, JSON, TXT/Markdown, PDF and DOCX"
                ),
            )

        dtype, basis, error = _classify(file_path, raw)
        if dtype is None:
            return self._entry(
                file_path, relative, top, size, digest, "unsupported", reason=error
            )

        case_key = _case_group_for(top, relative, corpus_root.name)
        previous = seen_hashes.get(digest)
        if previous is not None:
            scan.warnings.append(
                f"'{relative.as_posix()}' is byte-identical to '{previous}'; both are "
                "ingested (the second may be rejected as a duplicate within its case)."
            )
        else:
            seen_hashes[digest] = relative.as_posix()
        return self._entry(
            file_path, relative, top, size, digest, "accepted",
            document_type=dtype, case_key=case_key, reason=basis,
        )

    def _entry(
        self,
        path: Path,
        relative: Path,
        section: str | None,
        size: int,
        digest: str | None,
        status: str,
        *,
        document_type: DocumentType | None = None,
        case_key: str | None = None,
        reason: str | None = None,
    ) -> DiscoveredFile:
        return DiscoveredFile(
            path=path,
            relative_path=relative.as_posix(),
            section=section,
            size_bytes=size,
            sha256=digest,
            status=status,
            document_type=document_type,
            case_key=case_key,
            reason=reason,
        )

    # --------------------------------------------------------------- records
    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        scan = self.scan(options.get("root"))
        if scan.issues:
            raise ExternalCorpusError(
                "External synthetic corpus is not ingestible: " + "; ".join(scan.issues)
            )
        yield from self.records_from_scan(scan)

    def records_from_scan(self, scan: CorpusScan) -> Iterator[SourceRecord]:
        """Yield one :class:`SourceRecord` per accepted file of a prior scan."""
        if _is_crimelink_relational_corpus(scan):
            yield from self._records_from_crimelink_corpus(scan)
            return
        lookups = self._dataset_lookups(scan)
        for entry in scan.accepted:
            try:
                raw = entry.path.read_bytes()
            except OSError as exc:
                raise ExternalCorpusError(
                    f"Corpus file became unreadable during ingestion: {entry.path} ({exc})"
                ) from exc
            external_id = hashlib.sha256(
                f"{entry.relative_path}|{entry.sha256}".encode()
            ).hexdigest()[:24]
            content, content_type = self._normalize_dataset_file(
                entry.path, raw, lookups
            )
            yield SourceRecord(
                external_id=f"synthetic-external:{external_id}",
                case_number=self.case_number_for(entry.case_key or CASE_GROUP_FALLBACK),
                document_type=entry.document_type or DocumentType.FIR,
                filename=entry.path.name,
                content_type=content_type,
                content=content,
                source_environment="synthetic",
                source_confidence=SourceConfidence.SYNTHETIC,
                language="en",
                metadata={
                    "synthetic": True,
                    "synthetic_data_mode": "external",
                    "corpus_root": scan.root.name,
                    "relative_path": entry.relative_path,
                    "section": entry.section,
                    "case_key": entry.case_key,
                    "sha256": entry.sha256,
                    "classification": entry.reason,
                },
            )

    def _records_from_crimelink_corpus(self, scan: CorpusScan) -> Iterator[SourceRecord]:
        """Map the documented relational corpus onto per-case pipeline documents.

        Deterministic transformations (not invented data):

        * ``cases.csv`` → one CrimeLink Case per row (case_number as written).
        * ``documents/*.txt`` → uploaded to the case named in ``documents.csv``.
        * Event tables (CDR, transactions, sightings, intel) are sliced by
          ``case_id``. Rows with an empty ``case_id`` are attached to cases of
          the related person (phone owner / account holder / vehicle owner /
          subject) via ``case_members.csv``. Unmapped rows are skipped and
          counted in warnings — they are not assigned to a fabricated case.
        * ``persons.csv`` is sliced per case via ``case_members.csv`` and
          joined with owned phones/vehicles so the criminal-history adapter
          can emit ``USES_PHONE`` / ``OWNS_VEHICLE``.
        """
        tables = _load_operational_tables(scan)
        cases = tables.get("cases.csv", [])
        if not cases:
            scan.warnings.append(
                "CrimeLink corpus tables were detected but cases.csv has no data rows."
            )
            return

        person_by_id = {r.get("person_id", ""): r for r in tables.get("persons.csv", []) if r.get("person_id")}
        phone_by_id = {r.get("phone_id", ""): r for r in tables.get("phones.csv", []) if r.get("phone_id")}
        account_by_id = {r.get("account_id", ""): r for r in tables.get("accounts.csv", []) if r.get("account_id")}
        vehicle_by_id = {r.get("vehicle_id", ""): r for r in tables.get("vehicles.csv", []) if r.get("vehicle_id")}
        location_by_id = {r.get("location_id", ""): r for r in tables.get("locations.csv", []) if r.get("location_id")}
        org_by_id = {r.get("organization_id", ""): r for r in tables.get("organizations.csv", []) if r.get("organization_id")}

        phones_by_person: dict[str, list[dict[str, str]]] = {}
        for row in phone_by_id.values():
            owner = row.get("owner_person_id", "")
            if owner:
                phones_by_person.setdefault(owner, []).append(row)
        vehicles_by_person: dict[str, list[dict[str, str]]] = {}
        for row in vehicle_by_id.values():
            owner = row.get("owner_person_id", "")
            if owner:
                vehicles_by_person.setdefault(owner, []).append(row)
        accounts_by_person: dict[str, list[dict[str, str]]] = {}
        for row in account_by_id.values():
            owner = row.get("holder_person_id", "")
            if owner:
                accounts_by_person.setdefault(owner, []).append(row)

        members_by_case: dict[str, list[dict[str, str]]] = {}
        members_by_person: dict[str, set[str]] = {}
        for row in tables.get("case_members.csv", []):
            cid, pid = row.get("case_id", ""), row.get("person_id", "")
            if not cid or not pid:
                continue
            members_by_case.setdefault(cid, []).append(row)
            members_by_person.setdefault(pid, set()).add(cid)

        orgs_by_person: dict[str, list[dict[str, str]]] = {}
        for row in tables.get("person_organizations.csv", []):
            pid = row.get("person_id", "")
            if pid:
                orgs_by_person.setdefault(pid, []).append(row)

        case_by_id: dict[str, dict[str, str]] = {}
        for row in cases:
            cid = row.get("case_id", "")
            number = row.get("case_number", "")
            if not cid or not number:
                scan.warnings.append(
                    f"Skipping cases.csv row with missing case_id/case_number: {row}"
                )
                continue
            case_by_id[cid] = row

        known_cases = set(case_by_id)

        def cases_for_people(person_ids: list[str]) -> set[str]:
            out: set[str] = set()
            for pid in person_ids:
                out.update(members_by_person.get(pid, ()))
            return out & known_cases

        def attach(row: dict[str, str], *person_ids: str) -> set[str]:
            cid = (row.get("case_id") or "").strip()
            if cid:
                return {cid} if cid in known_cases else set()
            return cases_for_people([p for p in person_ids if p])

        unmapped = {"cdr": 0, "transactions": 0, "vehicle_sightings": 0, "intelligence_reports": 0}

        cdr_by_case: dict[str, list[dict[str, str]]] = {}
        for row in tables.get("cdr.csv", []):
            owners = [
                phone_by_id.get(row.get("from_phone_id", ""), {}).get("owner_person_id", ""),
                phone_by_id.get(row.get("to_phone_id", ""), {}).get("owner_person_id", ""),
            ]
            targets = attach(row, *owners)
            if not targets:
                unmapped["cdr"] += 1
                continue
            for cid in targets:
                cdr_by_case.setdefault(cid, []).append(row)

        tx_by_case: dict[str, list[dict[str, str]]] = {}
        for row in tables.get("transactions.csv", []):
            holders = [
                account_by_id.get(row.get("from_account_id", ""), {}).get("holder_person_id", ""),
                account_by_id.get(row.get("to_account_id", ""), {}).get("holder_person_id", ""),
            ]
            targets = attach(row, *holders)
            if not targets:
                unmapped["transactions"] += 1
                continue
            for cid in targets:
                tx_by_case.setdefault(cid, []).append(row)

        sight_by_case: dict[str, list[dict[str, str]]] = {}
        for row in tables.get("vehicle_sightings.csv", []):
            owner = vehicle_by_id.get(row.get("vehicle_id", ""), {}).get("owner_person_id", "")
            targets = attach(row, owner)
            if not targets:
                unmapped["vehicle_sightings"] += 1
                continue
            for cid in targets:
                sight_by_case.setdefault(cid, []).append(row)

        intel_by_case: dict[str, list[dict[str, str]]] = {}
        for row in tables.get("intelligence_reports.csv", []):
            targets = attach(row, row.get("subject_person_id", ""))
            if not targets:
                unmapped["intelligence_reports"] += 1
                continue
            for cid in targets:
                intel_by_case.setdefault(cid, []).append(row)

        for kind, count in unmapped.items():
            if count:
                scan.warnings.append(
                    f"{count} {kind} row(s) had no resolvable case_id and were not imported."
                )

        doc_manifest = {
            (r.get("file_path") or "").replace("\\", "/"): r
            for r in tables.get("documents.csv", [])
            if r.get("file_path")
        }
        doc_by_id = {
            r.get("document_id", ""): r
            for r in tables.get("documents.csv", [])
            if r.get("document_id")
        }

        for cid, case_row in case_by_id.items():
            number = case_row["case_number"]
            title = _synthetic_case_title(case_row)
            status = _map_case_status(case_row.get("status", ""))
            meta_base = {
                "synthetic": True,
                "synthetic_data_mode": "external",
                "corpus_root": scan.root.name,
                "case_key": cid,
                "case_title": title,
                "case_status": status,
                "case_type": case_row.get("case_type", ""),
                "external_case_id": cid,
            }

            # Case overview rendered from structured columns — not invented.
            overview = _render_case_overview(
                case_row,
                members_by_case.get(cid, []),
                person_by_id,
                phones_by_person,
                vehicles_by_person,
                orgs_by_person,
                org_by_id,
            )
            yield self._corpus_record(
                scan,
                case_number=number,
                filename=f"{cid}-case-record.txt",
                document_type=DocumentType.FIR,
                content=overview.encode("utf-8"),
                content_type="text/plain",
                relative_path=f"operational/cases.csv#{cid}",
                extra={
                    **meta_base,
                    "row_count": 1,
                    "classification": "cases.csv row rendered as case record",
                    "document_origin": (
                        _origin_for(
                            case_row,
                            record_id_field="case_id",
                            fields=["case_number", "registered_date", "case_type", "police_station", "city", "status"],
                        )
                        or OriginRef(file="operational/cases.csv")
                    ).to_dict(),
                },
            )

            history_rows = _person_history_rows(
                case_row,
                members_by_case.get(cid, []),
                person_by_id,
                phones_by_person,
                vehicles_by_person,
            )
            if history_rows:
                fields = ["name", "aliases", "role", "case_ref", "case_date", "phone", "plate", "address"]
                yield self._corpus_record(
                    scan,
                    case_number=number,
                    filename=f"{cid}-persons.csv",
                    document_type=DocumentType.CRIMINAL_HISTORY,
                    content=self._csv_bytes(fields, history_rows),
                    content_type="text/csv",
                    relative_path=f"operational/persons.csv#{cid}",
                    extra={**meta_base, "row_count": len(history_rows), "classification": "persons.csv sliced by case_members"},
                )

            cdr_rows = cdr_by_case.get(cid) or []
            if cdr_rows:
                fields = ["calling_number", "called_number", "timestamp", "duration_seconds", "direction", "imei"]
                output = [
                    row
                    for row in (
                        {
                            "calling_number": phone_by_id.get(r.get("from_phone_id", ""), {}).get("phone_number", ""),
                            "called_number": phone_by_id.get(r.get("to_phone_id", ""), {}).get("phone_number", ""),
                            "timestamp": r.get("timestamp", ""),
                            "duration_seconds": r.get("duration_seconds", ""),
                            "direction": r.get("call_type", ""),
                            "imei": "",
                            ORIGIN_COLUMN: _encode_origin(
                                _origin_for(
                                    r,
                                    record_id_field="cdr_id",
                                    fields=["from_phone_id", "to_phone_id", "timestamp", "duration_seconds", "call_type"],
                                )
                            ),
                        }
                        for r in cdr_rows
                    )
                    if row["calling_number"] and row["called_number"] and row["timestamp"]
                ]
                if output:
                    yield self._corpus_record(
                    scan,
                    case_number=number,
                    filename=f"{cid}-cdr.csv",
                    document_type=DocumentType.CDR,
                    content=self._csv_bytes(fields, output),
                    content_type="text/csv",
                    relative_path=f"operational/cdr.csv#{cid}",
                    extra={**meta_base, "row_count": len(output), "classification": "cdr.csv sliced by case"},
                )

            tx_rows = tx_by_case.get(cid) or []
            if tx_rows:
                fields = ["transaction_id", "date", "from_account", "to_account", "amount", "channel", "reference"]
                output = [
                    row
                    for row in (
                        {
                            "transaction_id": r.get("transaction_id", ""),
                            "date": r.get("timestamp", ""),
                            "from_account": account_by_id.get(r.get("from_account_id", ""), {}).get("account_number", ""),
                            "to_account": account_by_id.get(r.get("to_account_id", ""), {}).get("account_number", ""),
                            "amount": r.get("amount_inr", ""),
                            "channel": r.get("transaction_type", ""),
                            "reference": r.get("transaction_id", ""),
                            ORIGIN_COLUMN: _encode_origin(
                                _origin_for(
                                    r,
                                    record_id_field="transaction_id",
                                    fields=["from_account_id", "to_account_id", "amount_inr", "timestamp", "transaction_type"],
                                )
                            ),
                        }
                        for r in tx_rows
                    )
                    if row["from_account"] and row["to_account"] and row["amount"]
                ]
                if output:
                    yield self._corpus_record(
                    scan,
                    case_number=number,
                    filename=f"{cid}-transactions.csv",
                    document_type=DocumentType.FINANCIAL,
                    content=self._csv_bytes(fields, output),
                    content_type="text/csv",
                    relative_path=f"operational/transactions.csv#{cid}",
                    extra={**meta_base, "row_count": len(output), "classification": "transactions.csv sliced by case"},
                )

            sight_rows = sight_by_case.get(cid) or []
            if sight_rows:
                fields = ["subject", "observed_at", "location", "vehicle", "remarks"]
                output = [
                    row
                    for row in (
                        {
                            "subject": person_by_id.get(
                                vehicle_by_id.get(r.get("vehicle_id", ""), {}).get("owner_person_id", ""),
                                {},
                            ).get("full_name", ""),
                            "observed_at": r.get("timestamp", ""),
                            "location": location_by_id.get(r.get("location_id", ""), {}).get("name", ""),
                            "vehicle": vehicle_by_id.get(r.get("vehicle_id", ""), {}).get("registration_number", ""),
                            "remarks": r.get("source", ""),
                            ORIGIN_COLUMN: _encode_origin(
                                _origin_for(
                                    r,
                                    record_id_field="sighting_id",
                                    fields=["vehicle_id", "location_id", "timestamp", "source"],
                                )
                            ),
                        }
                        for r in sight_rows
                    )
                    if row["subject"] and row["observed_at"]
                ]
                if not output:
                    continue
                yield self._corpus_record(
                    scan,
                    case_number=number,
                    filename=f"{cid}-vehicle-sightings.csv",
                    document_type=DocumentType.SURVEILLANCE,
                    content=self._csv_bytes(fields, output),
                    content_type="text/csv",
                    relative_path=f"operational/vehicle_sightings.csv#{cid}",
                    extra={**meta_base, "row_count": len(output), "classification": "vehicle_sightings.csv sliced by case"},
                )

            intel_rows = intel_by_case.get(cid) or []
            if intel_rows:
                text, intel_line_origins = _render_intel(
                    intel_rows, person_by_id, location_by_id
                )
                yield self._corpus_record(
                    scan,
                    case_number=number,
                    filename=f"{cid}-intelligence.txt",
                    document_type=DocumentType.INTEL,
                    content=text.encode("utf-8"),
                    content_type="text/plain",
                    relative_path=f"operational/intelligence_reports.csv#{cid}",
                    extra={
                        **meta_base,
                        "row_count": len(intel_rows),
                        "classification": "intelligence_reports.csv sliced by case",
                        "line_origins": intel_line_origins,
                    },
                )

        unmatched_docs = 0
        for entry in scan.accepted:
            if entry.section != DOCUMENTS_DIR:
                continue
            manifest = doc_manifest.get(entry.relative_path) or doc_by_id.get(entry.path.stem)
            if manifest is None:
                unmatched_docs += 1
                continue
            cid = manifest.get("case_id", "")
            case_row = case_by_id.get(cid)
            if case_row is None:
                unmatched_docs += 1
                continue
            try:
                raw = entry.path.read_bytes()
            except OSError as exc:
                raise ExternalCorpusError(
                    f"Corpus file became unreadable during ingestion: {entry.path} ({exc})"
                ) from exc
            dtype = _document_type_from_manifest(manifest.get("document_type", ""))
            yield self._corpus_record(
                scan,
                case_number=case_row["case_number"],
                filename=entry.path.name,
                document_type=dtype,
                content=raw,
                content_type=_CONTENT_TYPES.get(entry.path.suffix.lower(), "text/plain"),
                relative_path=entry.relative_path,
                extra={
                    "synthetic": True,
                    "synthetic_data_mode": "external",
                    "corpus_root": scan.root.name,
                    "case_key": cid,
                    "case_title": _synthetic_case_title(case_row),
                    "case_status": _map_case_status(case_row.get("status", "")),
                    "case_type": case_row.get("case_type", ""),
                    "external_case_id": cid,
                    "sha256": entry.sha256,
                    "row_count": 1,
                    "classification": entry.reason or "free-text investigation document",
                    "language": manifest.get("language") or "en",
                    # Ingested byte-for-byte, so the document *is* the origin.
                    "document_origin": OriginRef(
                        file=entry.relative_path,
                        record_id=manifest.get("document_id") or entry.path.stem,
                    ).to_dict(),
                    "verbatim": True,
                },
                language=manifest.get("language") or "en",
            )
        if unmatched_docs:
            scan.warnings.append(
                f"{unmatched_docs} document file(s) were not listed in documents.csv "
                "with a valid case_id and were not imported."
            )

    def _corpus_record(
        self,
        scan: CorpusScan,
        *,
        case_number: str,
        filename: str,
        document_type: DocumentType,
        content: bytes,
        content_type: str,
        relative_path: str,
        extra: dict[str, Any],
        language: str = "en",
    ) -> SourceRecord:
        digest = hashlib.sha256(content).hexdigest()
        external_id = hashlib.sha256(f"{relative_path}|{digest}".encode()).hexdigest()[:24]
        metadata = dict(extra)
        metadata.setdefault("relative_path", relative_path)
        metadata.setdefault("section", relative_path.split("/", 1)[0] if "/" in relative_path else OPERATIONAL_DIR)
        metadata.setdefault("sha256", digest)
        return SourceRecord(
            external_id=f"synthetic-external:{external_id}",
            case_number=case_number,
            document_type=document_type,
            filename=filename,
            content_type=content_type,
            content=content,
            source_environment="synthetic",
            source_confidence=SourceConfidence.SYNTHETIC,
            language=language,
            metadata=metadata,
        )

    @staticmethod
    def _dataset_lookups(scan: CorpusScan) -> dict[str, dict[str, str]]:
        """Load reference tables used to normalize the corpus's ID columns."""
        result: dict[str, dict[str, str]] = {}
        for entry in scan.files:
            if entry.section != OPERATIONAL_DIR:
                continue
            if entry.status in {"excluded", "unreadable"}:
                continue
            if entry.path.suffix.lower() != ".csv":
                continue
            try:
                with entry.path.open(newline="", encoding="utf-8-sig") as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, UnicodeError, csv.Error):
                continue
            table = entry.path.name.lower()
            if table == "persons.csv":
                result["person"] = {r.get("person_id", ""): r.get("full_name", "") for r in rows}
            elif table == "phones.csv":
                result["phone"] = {r.get("phone_id", ""): r.get("phone_number", "") for r in rows}
            elif table == "accounts.csv":
                result["account"] = {r.get("account_id", ""): r.get("account_number", "") for r in rows}
            elif table == "vehicles.csv":
                result["vehicle"] = {r.get("vehicle_id", ""): r.get("registration_number", "") for r in rows}
                result["vehicle_owner"] = {r.get("vehicle_id", ""): r.get("owner_person_id", "") for r in rows}
            elif table == "locations.csv":
                result["location"] = {r.get("location_id", ""): r.get("name", "") for r in rows}
            elif table == "organizations.csv":
                result["organization"] = {r.get("organization_id", ""): r.get("name", "") for r in rows}
        return result

    @classmethod
    def _normalize_dataset_file(
        cls, path: Path, raw: bytes, lookups: dict[str, dict[str, str]]
    ) -> tuple[bytes, str]:
        """Convert the documented relational tables to existing pipeline formats."""
        name = path.name.lower()
        if name not in _DATASET_TABLE_HEADERS:
            return raw, _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        text = raw.decode("utf-8-sig", errors="replace")
        headers = _read_csv_headers(text)
        expected = _DATASET_TABLE_HEADERS[name]
        normalized_headers = frozenset(re.sub(r"[^a-z0-9]+", "", h.lower()) for h in headers)
        normalized_expected = frozenset(re.sub(r"[^a-z0-9]+", "", h) for h in expected)
        if normalized_headers != normalized_expected:
            return raw, _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        rows = list(csv.DictReader(io.StringIO(text)))
        person = lookups.get("person", {})
        phone = lookups.get("phone", {})
        account = lookups.get("account", {})
        vehicle = lookups.get("vehicle", {})
        vehicle_owner = lookups.get("vehicle_owner", {})
        location = lookups.get("location", {})
        organization = lookups.get("organization", {})

        if name == "cdr.csv":
            fields = ["calling_number", "called_number", "timestamp", "duration_seconds", "direction", "imei"]
            output = [{"calling_number": phone.get(r.get("from_phone_id", ""), ""),
                       "called_number": phone.get(r.get("to_phone_id", ""), ""),
                       "timestamp": r.get("timestamp", ""),
                       "duration_seconds": r.get("duration_seconds", ""),
                       "direction": r.get("call_type", ""), "imei": ""} for r in rows]
            return cls._csv_bytes(fields, output), "text/csv"
        if name == "transactions.csv":
            fields = ["transaction_id", "date", "from_account", "to_account", "amount", "channel", "reference"]
            output = [{"transaction_id": r.get("transaction_id", ""),
                       "date": r.get("timestamp", ""),
                       "from_account": account.get(r.get("from_account_id", ""), ""),
                       "to_account": account.get(r.get("to_account_id", ""), ""),
                       "amount": r.get("amount_inr", ""),
                       "channel": r.get("transaction_type", ""), "reference": r.get("transaction_id", "")} for r in rows]
            return cls._csv_bytes(fields, output), "text/csv"
        if name == "vehicle_sightings.csv":
            fields = ["subject", "observed_at", "location", "vehicle", "remarks"]
            output = [{"subject": person.get(vehicle_owner.get(r.get("vehicle_id", ""), ""), ""), "observed_at": r.get("timestamp", ""),
                       "location": location.get(r.get("location_id", ""), ""),
                       "vehicle": vehicle.get(r.get("vehicle_id", ""), ""),
                       "remarks": r.get("source", "")} for r in rows]
            return cls._csv_bytes(fields, output), "text/csv"
        if name == "persons.csv":
            fields = ["name", "aliases", "role", "case_ref", "case_date", "phone", "plate", "address"]
            output = [{"name": r.get("full_name", ""), "aliases": "", "role": r.get("status", ""),
                       "case_ref": "", "case_date": r.get("dob", ""), "phone": "", "plate": "",
                       "address": ", ".join(x for x in (r.get("address", ""), r.get("city", ""), r.get("state", "")) if x)} for r in rows]
            return cls._csv_bytes(fields, output), "text/csv"

        lines: list[str] = []
        for row in rows:
            values = dict(row)
            for key, table in (("person_id", person), ("owner_person_id", person),
                               ("holder_person_id", person), ("subject_person_id", person),
                               ("phone_id", phone), ("from_phone_id", phone), ("to_phone_id", phone),
                               ("from_account_id", account), ("to_account_id", account),
                               ("vehicle_id", vehicle), ("location_id", location),
                               ("organization_id", organization)):
                if values.get(key) and values[key] in table:
                    values[key] = table[values[key]]
            lines.append("; ".join(f"{key}: {value}" for key, value in values.items() if value))
        return "\n".join(lines).encode("utf-8"), "text/plain"

    @staticmethod
    def _csv_bytes(fields: list[str], rows: list[dict[str, str]]) -> bytes:
        """Render derived rows, carrying each row's origin in a reserved column.

        The origin column is emitted only when at least one row actually has an
        origin, so unrelated callers/tests see byte-identical output to before.
        Pipeline adapters strip ``ORIGIN_COLUMN`` before column matching, so it
        never participates in schema detection.
        """
        has_origin = any(row.get(ORIGIN_COLUMN) for row in rows)
        columns = [*fields, ORIGIN_COLUMN] if has_origin else list(fields)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")

    # --------------------------------------------------------------- describe
    def describe(self) -> dict[str, Any]:
        try:
            resolved = self.resolve_root()
        except Exception as exc:  # noqa: BLE001 - describe must never fail
            log.debug("sources.external.describe_failed", error=str(exc))
            resolved = None
        return {
            "name": self.name,
            "source_environment": self.source_environment,
            "synthetic": True,
            "root": str(resolved) if resolved else None,
            "root_exists": bool(resolved and resolved.is_dir()),
            "ingestion_sections": list(INGESTION_SECTIONS),
            "never_ingested": ["ground_truth", "metadata"],
            "note": (
                "Reads the external synthetic corpus from the filesystem. Only "
                "operational/ and documents/ are ingestion sources; "
                "ground_truth/ is evaluation-only and never enters the "
                "operational pipeline. Records carry "
                "source_environment=synthetic and SourceConfidence.SYNTHETIC."
            ),
        }


register_source_adapter("synthetic_external", ExternalSyntheticCorpusAdapter)
