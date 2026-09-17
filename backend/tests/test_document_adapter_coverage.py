"""Every document type the upload endpoint accepts must be ingestable.

The defect: ``POST /cases/{id}/documents`` validates ``document_type`` against
the whole ``DocumentType`` enum (37 members) and answers **202 Accepted**, but
the adapter registry only covered 7 of them.  Uploading a WITNESS_STATEMENT —
or an ARREST_RECORD, a SCENE_REPORT, a CASE_DIARY, an ANPR feed… — reached the
pipeline and raised::

    KeyError: 'No adapter registered for DocumentType.WITNESS_STATEMENT'

five times, after which the document was quarantined with
"Processing failed after 5 attempts: KeyError".  The investigator had already
been told the upload succeeded.  ``supported_types()`` made it worse by
returning every enum member, so the API advertised support it did not have.
"""

from __future__ import annotations

import pytest

from app.domain.enums import DocumentType
from app.pipeline.adapters.registry import get_adapter, supported_types


def test_every_document_type_resolves_to_an_adapter():
    missing = []
    for member in DocumentType:
        try:
            get_adapter(member)
        except KeyError:
            missing.append(member.value)
    assert not missing, f"no adapter for: {missing}"


def test_supported_types_matches_what_the_pipeline_can_actually_parse():
    """The advertised list is the registry, not the enum."""
    advertised = set(supported_types())
    ingestable = set()
    for member in DocumentType:
        try:
            get_adapter(member)
            ingestable.add(member.value)
        except KeyError:
            pass
    assert advertised == ingestable, (
        f"advertised but not ingestable: {sorted(advertised - ingestable)}"
    )


def test_the_types_that_broke_upload_are_covered():
    """Regression pins for the specific types an investigator uploads often."""
    for name in (
        "WITNESS_STATEMENT",
        "ARREST_RECORD",
        "SCENE_REPORT",
        "CASE_DIARY",
        "SEIZURE",
        "BAIL_RECORD",
        "LEGAL_RECORD",
        "PATROL_REPORT",
        "ANPR",
        "CCTV",
        "FORENSIC",
        "REVIEW",
    ):
        member = DocumentType(name)
        adapter = get_adapter(member)
        assert adapter is not None, name


def test_specialised_adapters_are_still_used_where_they_exist():
    """The universal fallback must not silently replace a real parser."""
    from app.pipeline.adapters.cdr import CDRAdapter
    from app.pipeline.adapters.financial import FinancialAdapter
    from app.pipeline.adapters.social_media import SocialMediaAdapter

    assert isinstance(get_adapter(DocumentType.CDR), CDRAdapter)
    assert isinstance(get_adapter(DocumentType.FINANCIAL), FinancialAdapter)
    assert isinstance(get_adapter(DocumentType.FINANCIAL_TRANSACTION), FinancialAdapter)
    assert isinstance(get_adapter(DocumentType.SOCIAL_MEDIA), SocialMediaAdapter)


def test_text_like_types_share_the_text_adapter():
    """An arrest record and a witness statement differ in provenance, not parsing."""
    from app.pipeline.adapters.document_adapter import TextDocumentAdapter

    text = get_adapter(DocumentType.FIR)
    assert isinstance(text, TextDocumentAdapter)
    for name in ("WITNESS_STATEMENT", "ARREST_RECORD", "SCENE_REPORT", "CASE_DIARY"):
        assert get_adapter(DocumentType(name)) is text, name


def test_anonymous_tip_still_gets_its_own_adapter():
    from app.pipeline.adapters.document_adapter import AnonymousTipAdapter

    assert isinstance(get_adapter(DocumentType.INTEL, anonymous_tip=True), AnonymousTipAdapter)


@pytest.mark.parametrize(
    "mime",
    ["application/pdf", "text/csv", "text/plain", "application/json", "image/png"],
)
def test_allowed_mime_types_are_a_documented_allowlist(mime):
    """Upload rejects an undeclared content type instead of guessing."""
    from app.api.v1.documents import _ALLOWED_MIME

    # image/png is deliberately NOT in the allowlist; the point of the test is
    # that the allowlist is explicit rather than permissive.
    if mime == "image/png":
        assert mime not in _ALLOWED_MIME
    else:
        assert mime in _ALLOWED_MIME
