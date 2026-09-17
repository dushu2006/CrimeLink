"""Source adapter registry (PRD 7).

One adapter per data source named in the problem statement.  Adapters are
selected by declared ``document_type``; text-like types share the text adapter
because an FIR, an intelligence report and an anonymous tip are all free text —
what differs is their provenance classification, not their parsing.
"""

from __future__ import annotations

from app.domain.enums import DocumentType
from app.logging import get_logger
from app.pipeline.adapters.protocol import DocumentMeta, SourceAdapter

log = get_logger("crimelink.adapter.registry")


def _build_registry() -> dict[DocumentType, SourceAdapter]:
    from app.pipeline.adapters.cdr import CDRAdapter
    from app.pipeline.adapters.criminal_history import (
        CriminalHistoryAdapter,
        SurveillanceAdapter,
    )
    from app.pipeline.adapters.document_adapter import (
        AnonymousTipAdapter,
        IntelReportAdapter,
        TextDocumentAdapter,
    )
    from app.pipeline.adapters.financial import FinancialAdapter
    from app.pipeline.adapters.social_media import SocialMediaAdapter

    text_adapter = TextDocumentAdapter()
    registry = {
        DocumentType.FIR: text_adapter,
        DocumentType.INTEL: IntelReportAdapter(),
        DocumentType.SURVEILLANCE: SurveillanceAdapter(),
        DocumentType.SURVEILLANCE_REPORT: SurveillanceAdapter(),
        DocumentType.CDR: CDRAdapter(),
        DocumentType.FINANCIAL: FinancialAdapter(),
        DocumentType.FINANCIAL_TRANSACTION: FinancialAdapter(),
        DocumentType.SOCIAL_MEDIA: SocialMediaAdapter(),
        DocumentType.SOCIAL_MEDIA_INTELLIGENCE: SocialMediaAdapter(),
        DocumentType.CRIMINAL_HISTORY: CriminalHistoryAdapter(),
        DocumentType.CRIMINAL_RECORD: CriminalHistoryAdapter(),
    }
    # Every remaining document type is text-like: an arrest record, a witness
    # statement, a scene report and a case diary are all free text, and what
    # differs between them is provenance classification, not parsing.  The
    # upload endpoint accepts the whole DocumentType enum, so a type with no
    # adapter used to reach the pipeline and raise
    # "No adapter registered for DocumentType.WITNESS_STATEMENT" — five times,
    # after which the document was quarantined, even though the API had already
    # answered 202 Accepted.
    for member in DocumentType:
        registry.setdefault(member, text_adapter)
    return registry


_REGISTRY: dict[DocumentType, SourceAdapter] | None = None


def get_adapter(document_type: DocumentType, *, anonymous_tip: bool = False) -> SourceAdapter:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    if anonymous_tip and document_type == DocumentType.INTEL:
        from app.pipeline.adapters.document_adapter import AnonymousTipAdapter

        return AnonymousTipAdapter()
    adapter = _REGISTRY.get(document_type)
    if adapter is None:  # pragma: no cover - the registry covers every member
        raise KeyError(f"No adapter registered for {document_type}")
    return adapter


def supported_types() -> list[str]:
    """Every document type the pipeline can actually ingest.

    This is what the upload endpoint advertises, so it must match the registry
    rather than the enum: advertising a type the pipeline cannot parse is how a
    202 turns into a quarantined document.
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return [member.value for member in _REGISTRY]


__all__ = ["get_adapter", "supported_types", "DocumentMeta", "SourceAdapter"]
