"""External synthetic corpus — a filesystem dataset wrapped as a SourceAdapter.

This adapter turns a checked-out *external* synthetic corpus (for example the
``CrimeLink_Synthetic_Corpus_v1`` directory that lives next to the CrimeLink
checkout) into :class:`SourceRecord` objects, so the dataset enters CrimeLink
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
    status: str                   # "accepted" | "unsupported" | "unreadable" | "excluded" | "ignored"
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
        return {
            "root": str(self.root),
            "ok": self.ok,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "counts": counts,
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
            if filename == "documents.csv":
                return None, "operational/documents.csv is a manifest; referenced evidence files are imported from documents/"
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
    paths resolve against the CrimeLink repository root, so the documented
    sibling layout works out of the box).
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
                "(e.g. ../CrimeLink_Synthetic_Corpus_v1 relative to the repo)."
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

    @staticmethod
    def _dataset_lookups(scan: CorpusScan) -> dict[str, dict[str, str]]:
        """Load reference tables used to normalize the corpus's ID columns."""
        result: dict[str, dict[str, str]] = {}
        for entry in scan.accepted:
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
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields)
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
