"""Source adapter contract (PRD 7).

Every adapter normalises one input format into the common
:class:`~app.domain.models.NormalizedDocument` intermediate representation, and
every adapter obeys the same three rules:

1. **The original bytes go to object storage first.**  The pristine original
   exists before any extraction runs, so a later stage corrupting data can
   always be undone by re-processing from the original.
2. **Offsets are preserved.**  Every block records its character offset in the
   normalised text, which is what makes the evidence pointer
   ``{source_doc_id, text_span}`` resolvable later.
3. **Failures are explicit.**  A malformed input raises
   :class:`~app.errors.PipelineError` with a human-readable reason that lands in
   the quarantine queue.  Nothing is skipped silently (PRD principle P4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from app.domain.enums import DocumentType, Language, SourceConfidence
from app.domain.models import Block, NormalizedDocument
from app.errors import PipelineError

IST = ZoneInfo("Asia/Kolkata")


@dataclass(slots=True)
class DocumentMeta:
    """Everything the adapter knows about the document being ingested."""

    doc_id: str
    case_id: str
    filename: str
    document_type: DocumentType
    source_confidence: SourceConfidence = SourceConfidence.UNVERIFIED
    mime_type: str = "application/octet-stream"
    language_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    document_type: DocumentType

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument: ...


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_SCRIPT_RANGES: tuple[tuple[str, tuple[int, int]], ...] = (
    (Language.HI.value, (0x0900, 0x097F)),   # Devanagari (Hindi, Marathi)
    (Language.TA.value, (0x0B80, 0x0BFF)),   # Tamil
    (Language.TE.value, (0x0C00, 0x0C7F)),   # Telugu
    (Language.BN.value, (0x0980, 0x09FF)),   # Bengali
)

_MARATHI_MARKERS = ("आहे", "म्हणून", "केले", "माझे", "त्याचे")
_HINDI_MARKERS = ("है", "था", "ने", "की", "में", "का", "और", "गिरफ्तार", "थाना", "धारा")


def detect_language(text: str, hint: str | None = None) -> str:
    """Script-based language detection (PRD 7: language is detected at stage 1).

    Cheap, deterministic and good enough to select a model — which is all stage 1
    needs.  An explicit officer-supplied hint always wins.
    """
    if hint in {item.value for item in Language} and hint != Language.UNKNOWN.value:
        return hint
    if not text:
        return Language.EN.value
    sample = text[:8000]
    counts: dict[str, int] = {}
    for name, (low, high) in _SCRIPT_RANGES:
        counts[name] = sum(1 for ch in sample if low <= ord(ch) <= high)
    best = max(counts, key=lambda k: counts[k])
    if counts[best] < 20:
        return Language.EN.value
    if best == Language.HI.value:
        # Marathi is Devanagari too; a small marker set separates it.
        marathi = sum(sample.count(marker) for marker in _MARATHI_MARKERS)
        hindi = sum(sample.count(marker) for marker in _HINDI_MARKERS)
        return Language.MR.value if marathi > hindi else Language.HI.value
    return best


# ---------------------------------------------------------------------------
# Helpers shared by adapters
# ---------------------------------------------------------------------------


def text_blocks_from_text(text: str, *, page: int | None = None) -> list[Block]:
    """Split plain text into paragraph blocks with accurate character offsets."""
    blocks: list[Block] = []
    cursor = 0
    for paragraph in re.split(r"\n\s*\n", text):
        start = text.find(paragraph, cursor)
        if start < 0:
            start = cursor
        blocks.append(
            Block(
                kind="text",
                text=paragraph.strip(),
                offset=start,
                page=page,
            )
        )
        cursor = start + len(paragraph)
    return [b for b in blocks if b.text]


def to_ist_iso(value: Any) -> str | None:
    """Normalise any timestamp to an IST ISO-8601 string (PRD 7).

    Dates are stored as ISO-8601 in UTC with an explicit offset so that ordering
    and windowing are unambiguous across operators and states.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d %H:%M:%S",
            "%d%m%Y",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                from dateutil import parser as date_parser

                parsed = date_parser.parse(text, dayfirst=True)
            except Exception:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(timezone.utc).isoformat()


def pick_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    """Resolve a column name from an operator-specific alias list."""
    normalised = {_slug(h): h for h in headers}
    for alias in aliases:
        key = _slug(alias)
        if key in normalised:
            return normalised[key]
    # Substring fallback for headers like "CALLING PARTY MSISDN (A)".
    for alias in aliases:
        key = _slug(alias)
        for slug, original in normalised.items():
            if key and key in slug:
                return original
    return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PipelineError(reason)
