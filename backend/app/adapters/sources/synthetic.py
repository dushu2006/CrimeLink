"""Synthetic development corpus — wrapped as a SourceAdapter.

Registers under the name ``synthetic`` so operators and future tooling can
drive it through the same adapter boundary as file uploads and (future)
authorised government feeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from app.domain.enums import DocumentType, SourceConfidence

from .protocol import SourceAdapter, SourceRecord
from .registry import register_source_adapter


# DocumentType mapping by content kind produced in generate.py.
_DOCTYPE_BY_KIND = {
    "fir":              DocumentType.FIR,
    "cdr":              DocumentType.CDR,
    "bank":             DocumentType.FINANCIAL,
    "surveillance":     DocumentType.SURVEILLANCE,
    "social":           DocumentType.SOCIAL_MEDIA,
    "criminal_history": DocumentType.CRIMINAL_HISTORY,
    "intel":            DocumentType.INTEL,
}


@dataclass
class SyntheticCorpusAdapter(SourceAdapter):
    """Source adapter that yields synthetic corpus records.

    Accepts the same options as :class:`app.synthetic_corpus.generate.CorpusOptions`.
    The adapter does **not** ingest by itself; it yields records so the
    pipeline / CLI can process them through the standard upload_document path.
    """

    name: str = "synthetic"
    source_environment: str = "synthetic"

    # options
    seed: int = 20260902
    person_count: int = 60
    case_count: int = 12
    phone_count: int = 80
    vehicle_count: int = 20
    location_count: int = 20
    account_count: int = 25
    organization_count: int = 10
    document_count: int = 60
    call_count: int = 250
    transaction_count: int = 150
    bridge_count: int = 5
    network_count: int = 4
    missing_field_rate: float = 0.10
    duplicate_rate: float = 0.05
    name_variation_rate: float = 0.20

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_environment": self.source_environment,
            "synthetic": True,
            "parameters": {
                "seed": self.seed,
                "person_count": self.person_count,
                "case_count": self.case_count,
                "phone_count": self.phone_count,
                "vehicle_count": self.vehicle_count,
                "document_count": self.document_count,
                "call_count": self.call_count,
                "transaction_count": self.transaction_count,
                "bridge_count": self.bridge_count,
                "network_count": self.network_count,
            },
            "note": (
                "Records from this adapter are clearly labelled "
                "source_environment=synthetic in provenance metadata and UI."
            ),
        }

    def iter_records(self, **options: Any) -> Iterator[SourceRecord]:
        from app.synthetic_corpus.generate import CorpusOptions, SyntheticCorpus
        opts = CorpusOptions(
            seed=options.get("seed", self.seed),
            person_count=options.get("person_count", self.person_count),
            case_count=options.get("case_count", self.case_count),
            phone_count=options.get("phone_count", self.phone_count),
            vehicle_count=options.get("vehicle_count", self.vehicle_count),
            location_count=options.get("location_count", self.location_count),
            account_count=options.get("account_count", self.account_count),
            organization_count=options.get("organization_count", self.organization_count),
            document_count=options.get("document_count", self.document_count),
            call_count=options.get("call_count", self.call_count),
            transaction_count=options.get("transaction_count", self.transaction_count),
            bridge_count=options.get("bridge_count", self.bridge_count),
            network_count=options.get("network_count", self.network_count),
            missing_field_rate=options.get("missing_field_rate", self.missing_field_rate),
            duplicate_rate=options.get("duplicate_rate", self.duplicate_rate),
            name_variation_rate=options.get("name_variation_rate", self.name_variation_rate),
        )
        corpus = SyntheticCorpus(opts=opts)
        corpus.build()
        for doc in corpus.documents:
            content = doc["content"]
            dtype = doc.get("document_type") or DocumentType.FIR
            case_obj = doc.get("case")
            case_number = case_obj.case_number if case_obj is not None else case_obj.id if hasattr(case_obj, "id") else "SYN-CASE"
            # determine a kind tag for the doc by filename
            fname = doc.get("filename", "document.txt")
            if "FIR" in fname:
                kind = "fir"
            elif "CDR" in fname:
                kind = "cdr"
            elif "BANK" in fname or "Financial" in fname or "financial" in fname:
                kind = "bank"
            elif "SURV" in fname:
                kind = "surveillance"
            elif "SOC" in fname:
                kind = "social"
            elif "HISTORY" in fname:
                kind = "criminal_history"
            elif "INTEL" in fname:
                kind = "intel"
            else:
                kind = "fir"
            yield SourceRecord(
                external_id=doc["doc_id"],
                case_number=case_number,
                document_type=_DOCTYPE_BY_KIND.get(kind, dtype if isinstance(dtype, DocumentType) else DocumentType.FIR),
                filename=fname,
                content_type=doc.get("content_type", "text/plain"),
                content=content,
                source_environment="synthetic",
                source_confidence=SourceConfidence.SYNTHETIC,
                language=doc.get("language", "en"),
                metadata={
                    "corpus_seed": opts.seed,
                    "corpus_version": corpus.version,
                    "synthetic": True,
                    "doc_kind": kind,
                    **(doc.get("metadata") or {}),
                },
            )


register_source_adapter("synthetic", SyntheticCorpusAdapter)
