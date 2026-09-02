"""Financial record adapter (PRD 7).

Parses bank statements and transaction exports into ``TRANSFER_TO`` records.
Handles Indian numeral conventions — the rupee sign, lakh/crore comma grouping
(``₹1,20,000``) and spelled-out units (``2.5 lakh``) — because a mis-parsed
amount is a mis-parsed structuring case.

Failure policy is row-level quarantine: one unparseable row is skipped with a
warning, but if more than a quarter of the file is unusable the whole document
fails, because the remaining transactions would silently understate totals.
"""

from __future__ import annotations

import csv
import io

from app.domain.enums import DocumentType
from app.domain.models import Block, NormalizedDocument
from app.domain.normalize import normalize_account, normalize_ifsc, parse_amount
from app.errors import PipelineError
from app.logging import get_logger
from app.pipeline.adapters.protocol import DocumentMeta, detect_language, pick_column, to_ist_iso

log = get_logger("crimelink.adapter.financial")

MAX_BAD_ROW_RATIO = 0.25

_ALIASES: dict[str, tuple[str, ...]] = {
    "from_account": ("from_account", "debit_account", "source_account", "account_no",
                     "account_number", "remitter_account", "payer_account", "from_ac"),
    "to_account": ("to_account", "credit_account", "beneficiary_account", "destination_account",
                   "payee_account", "to_ac", "counterparty_account"),
    "from_ifsc": ("from_ifsc", "remitter_ifsc", "branch_ifsc", "ifsc"),
    "to_ifsc": ("to_ifsc", "beneficiary_ifsc", "counterparty_ifsc"),
    "amount": ("amount", "transaction_amount", "txn_amount", "debit", "credit", "value",
               "amount_inr", "transaction_value"),
    "ts": ("timestamp", "txn_date", "transaction_date", "value_date", "date",
           "posting_date", "transaction_time"),
    "channel": ("channel", "mode", "transaction_type", "payment_mode", "txn_type"),
    "reference": ("reference", "txn_id", "transaction_id", "utr", "ref_no", "narration"),
}


class FinancialAdapter:
    """Bank statement / transaction CSV → normalised transfer records."""

    document_type = DocumentType.FINANCIAL

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument:
        text = raw.decode("utf-8", errors="replace")
        sample = text[:4096]
        delimiter = ","
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        headers = [h for h in (reader.fieldnames or []) if h]
        if not headers:
            raise PipelineError("The financial file has no header row.")

        mapping = {field: pick_column(headers, aliases) for field, aliases in _ALIASES.items()}
        if not mapping["from_account"] or not mapping["to_account"] or not mapping["amount"]:
            quoted = ", ".join(headers[:12])
            raise PipelineError(
                "Unrecognised financial statement format: could not identify the "
                f"payer account, beneficiary account and amount columns. "
                f"Header row received: [{quoted}]."
            )

        blocks: list[Block] = []
        warnings: list[str] = []
        bad_rows = 0
        rendered_rows: list[str] = []
        cursor = 0

        rows = list(reader)
        for index, row in enumerate(rows, start=1):
            record = self._record_from_row(row, mapping)
            if record is None:
                bad_rows += 1
                if bad_rows <= 5:
                    warnings.append(f"Row {index} quarantined: amount, account or date unusable.")
                continue
            rendered = (
                f"Transfer of Rs {record['amount']:,.2f} from A/C {record['from_account']} "
                f"to A/C {record['to_account']} on {record['ts']} via {record['channel']}"
            )
            blocks.append(
                Block(kind="record", text=rendered, offset=cursor,
                      data={"kind": "transfer", **record})
            )
            rendered_rows.append(rendered)
            cursor += len(rendered) + 1

        if not rows:
            raise PipelineError("The financial file contains no data rows.")
        bad_ratio = bad_rows / len(rows)
        if bad_rows and bad_ratio > MAX_BAD_ROW_RATIO:
            raise PipelineError(
                f"{bad_rows} of {len(rows)} rows ({bad_ratio:.0%}) could not be parsed. "
                "Too much of the statement is unusable to present a trustworthy total."
            )
        if bad_rows:
            warnings.append(f"{bad_rows} row(s) quarantined and excluded.")

        text_rendered = "\n".join(rendered_rows)
        return NormalizedDocument(
            doc_id=doc_meta.doc_id,
            case_id=doc_meta.case_id,
            doc_type=DocumentType.FINANCIAL.value,
            language=detect_language(text_rendered, doc_meta.language_hint),
            source_confidence=doc_meta.source_confidence,
            blocks=blocks,
            parse_warnings=warnings,
            metadata={
                "filename": doc_meta.filename,
                "rows": len(rows),
                "bad_rows": bad_rows,
                "columns": {k: v for k, v in mapping.items() if v},
            },
        )

    @staticmethod
    def _record_from_row(row: dict, mapping: dict[str, str | None]) -> dict | None:
        from_account = normalize_account(str(row.get(mapping["from_account"] or "", "") or ""))
        to_account = normalize_account(str(row.get(mapping["to_account"] or "", "") or ""))
        amount = parse_amount(row.get(mapping["amount"] or "", ""))
        ts = to_ist_iso(row.get(mapping["ts"] or "", "")) if mapping["ts"] else None
        if not from_account or not to_account or amount is None:
            return None
        return {
            "from_account": from_account,
            "to_account": to_account,
            "from_ifsc": normalize_ifsc(str(row.get(mapping["from_ifsc"] or "", "") or "")),
            "to_ifsc": normalize_ifsc(str(row.get(mapping["to_ifsc"] or "", "") or "")),
            "amount": amount,
            "ts": ts or "",
            "channel": str(row.get(mapping["channel"] or "", "") or "UNKNOWN").upper(),
            "reference": (row.get(mapping["reference"] or "", "") or None) if mapping["reference"] else None,
        }
