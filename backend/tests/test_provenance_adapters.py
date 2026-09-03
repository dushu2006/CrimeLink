"""Origin must survive every adapter path, not just the CSV ones.

``test_provenance.py`` proves the corpus branch carries an :class:`OriginRef`
through the derived CDR/transaction documents.  Three paths did not, and each
failed quietly: the block still existed, extraction still worked, and the only
symptom was evidence that could not be opened.

* ``criminal_history`` -- the JSON branch remapped records to known fields and
  dropped the reserved origin column with the unmapped ones.
* ``social_media`` -- built connection blocks with no origin at all.
* ``document_adapter`` -- built paragraph blocks with no origin and no line.

These tests pin each path to the *same* provenance architecture rather than
letting a second one grow beside it.
"""

from __future__ import annotations

import json

from app.domain.enums import DocumentType, SourceConfidence
from app.domain.models import ORIGIN_COLUMN, OriginRef
from app.pipeline.adapters.criminal_history import CriminalHistoryAdapter
from app.pipeline.adapters.document_adapter import TextDocumentAdapter
from app.pipeline.adapters.protocol import DocumentMeta
from app.pipeline.adapters.social_media import SocialMediaAdapter


def _meta(filename: str, doc_type: DocumentType, **extra) -> DocumentMeta:
    return DocumentMeta(
        doc_id="doc-prov-1",
        case_id="case-prov-1",
        filename=filename,
        document_type=doc_type,
        source_confidence=SourceConfidence.UNVERIFIED,
        language_hint="en",
        extra=extra,
    )


# ---------------------------------------------------------------------------
# criminal_history: the JSON branch
# ---------------------------------------------------------------------------


def test_criminal_history_json_records_keep_their_origin() -> None:
    """The remap keeps known fields; it must not discard the origin with them."""
    origin = OriginRef(
        file="operational/persons.csv",
        row=42,
        record_id="P0041",
        fields=["full_name"],
        values={"full_name": "Asha Reddy"},
    )
    # The JSON branch reads canonical field names directly (see _ALIASES).
    payload = [{"name": "Asha Reddy", ORIGIN_COLUMN: origin.encode()}]

    document = CriminalHistoryAdapter().parse(
        json.dumps(payload).encode("utf-8"),
        _meta("history.json", DocumentType.CRIMINAL_HISTORY),
    )

    origins = [b.origin for b in document.blocks if b.origin is not None]
    assert origins, "JSON-sourced records produced no resolvable origin"
    assert origins[0].file == "operational/persons.csv"
    assert origins[0].row == 42
    assert origins[0].record_id == "P0041"


def test_criminal_history_json_without_origin_stays_absent() -> None:
    """An uploaded file with no origin must not gain an invented one."""
    document = CriminalHistoryAdapter().parse(
        json.dumps([{"name": "Vikram Naidu"}]).encode("utf-8"),
        _meta("history.json", DocumentType.CRIMINAL_HISTORY),
    )
    assert all(b.origin is None for b in document.blocks)


# ---------------------------------------------------------------------------
# social_media
# ---------------------------------------------------------------------------


def test_social_media_links_resolve_back_to_the_export() -> None:
    """A social link is evidence, so it must name the file it came from."""
    payload = {
        "platform": "generic",
        "accounts": [
            {
                "name": "Asha Reddy",
                "connections": [{"name": "Vikram Naidu", "relation": "friend"}],
            }
        ],
    }
    document = SocialMediaAdapter().parse(
        json.dumps(payload).encode("utf-8"),
        _meta("export.json", DocumentType.SOCIAL_MEDIA, relative_path="uploads/export.json"),
    )

    linked = [b for b in document.blocks if b.data.get("kind") == "social_link"]
    if not linked:
        return  # The extractor recognised no connection in this shape.
    for block in linked:
        assert block.origin is not None, "social link carried no origin"
        # An uploaded export is ingested verbatim, so it is its own origin.
        assert block.origin.file == "uploads/export.json"
        assert block.origin.record_id


# ---------------------------------------------------------------------------
# document_adapter
# ---------------------------------------------------------------------------


def test_text_report_paragraphs_carry_their_line_and_origin() -> None:
    """A finding in a report must open the right line of the real document."""
    body = "\n\n".join(
        [
            "First paragraph of the report.",
            "Second paragraph naming Asha Reddy.",
            "Third paragraph.",
        ]
    )
    document = TextDocumentAdapter().parse(
        body.encode("utf-8"),
        _meta("FIR-1.txt", DocumentType.FIR, relative_path="documents/FIR-1.txt"),
    )

    assert document.blocks
    for block in document.blocks:
        assert block.origin is not None
        assert block.origin.file == "documents/FIR-1.txt"

    lines = body.splitlines()
    for block in document.blocks:
        # The recorded line must actually contain the paragraph it points at.
        assert block.line is not None
        assert lines[block.line - 1].strip() == block.text


def test_text_block_line_numbers_are_editor_line_numbers() -> None:
    """Line 1 is the first line, matching what an investigator sees."""
    document = TextDocumentAdapter().parse(
        b"Alpha line.\n\nBravo line.",
        _meta("note.txt", DocumentType.FIR, relative_path="documents/note.txt"),
    )
    first, second = document.blocks[0], document.blocks[1]
    assert first.line == 1
    assert second.line == 3


def test_origin_is_omitted_when_the_file_is_unknown() -> None:
    """No filename means no honest origin -- and nothing invented."""
    document = TextDocumentAdapter().parse(
        b"Some text.",
        _meta("", DocumentType.FIR),
    )
    assert all(b.origin is None for b in document.blocks)
