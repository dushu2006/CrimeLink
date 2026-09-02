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
    return {
        DocumentType.FIR: text_adapter,
        DocumentType.INTEL: IntelReportAdapter(),
        DocumentType.SURVEILLANCE: SurveillanceAdapter(),
        DocumentType.CDR: CDRAdapter(),
        DocumentType.FINANCIAL: FinancialAdapter(),
        DocumentType.SOCIAL_MEDIA: SocialMediaAdapter(),
        DocumentType.CRIMINAL_HISTORY: CriminalHistoryAdapter(),
    }


_REGISTRY: dict[DocumentType, SourceAdapter] | None = None


def get_adapter(document_type: DocumentType, *, anonymous_tip: bool = False) -> SourceAdapter:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    if anonymous_tip and document_type == DocumentType.INTEL:
        from app.pipeline.adapters.document_adapter import AnonymousTipAdapter

        return AnonymousTipAdapter()
    adapter = _REGISTRY.get(document_type)
    if adapter is None:
        raise KeyError(f"No adapter registered for {document_type}")
    return adapter


def supported_types() -> list[str]:
    return [member.value for member in DocumentType]


__all__ = ["get_adapter", "supported_types", "DocumentMeta", "SourceAdapter"]
