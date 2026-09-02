"""Generate the two bulk synthetic sample files (CDR and bank transactions).

Kept as a script rather than committed prose so the demo volume can be
reproduced or scaled (e.g. to load-test a 10,000-node case) without hand-writing
thousands of CSV rows.

Both files are engineered to exercise specific, deterministic detectors:

``cdr_jio.csv``
    A short-lived number (``+918700012345``) contacts 18 distinct numbers inside
    a 10-day window — the burner-phone signature.

``bank_transactions.csv``
    Twenty-two transfers of ₹48,000 — each below the ₹50,000 reporting
    threshold — totalling ₹10.56 lakh inside a 30-day window, which is the
    structuring signature.

Everything here is synthetic.  No real person, number or account is referenced.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[2] / "samples"

CORE = [
    "+919811023456",  # Ramesh Kumar Yadav
    "+919829012345",  # Suresh Mehta
    "+919610012345",  # Vikram Singh Rathore
    "+919001123456",  # Anil Sharma
    "+919772211223",  # Sunil Jain
    "+919610019876",  # Meena Rathore
]
BURNNER = "+918700012345"
# Ten digit Indian mobile series: 98700xxxxx
BURNNER_TARGETS = [f"+91{9870000000 + i * 137}" for i in range(18)]


def write_cdr(path: Path) -> int:
    random.seed(20240814)
    rows: list[dict] = []
    start = datetime(2024, 8, 1, 9, 0, 0)

    # Routine traffic between the core members of the network.
    for day in range(14):
        for caller in CORE:
            for callee in CORE:
                if caller == callee:
                    continue
                if random.random() > 0.45:
                    continue
                ts = start + timedelta(days=day, hours=random.randint(8, 22), minutes=random.randint(0, 59))
                rows.append(
                    {
                        "call_date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "calling_number": caller,
                        "called_number": callee,
                        "duration": random.randint(8, 420),
                        "call_type": random.choice(["OUTGOING", "OUTGOING", "INCOMING"]),
                    }
                )

    # Burner phone: 18 distinct counterparties inside 10 days.
    for index, target in enumerate(BURNNER_TARGETS):
        ts = start + timedelta(days=index % 10, hours=7 + (index % 12), minutes=(index * 7) % 60)
        rows.append(
            {
                "call_date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "calling_number": BURNNER,
                "called_number": target,
                "duration": random.randint(5, 90),
                "call_type": "OUTGOING",
            }
        )
    # ...and a couple of inbound calls so the burner is not one-directional.
    rows.append(
        {
            "call_date": (start + timedelta(days=2, hours=11)).strftime("%Y-%m-%d %H:%M:%S"),
            "calling_number": CORE[0],
            "called_number": BURNNER,
            "duration": 64,
            "call_type": "OUTGOING",
        }
    )

    rows.sort(key=lambda row: row["call_date"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["call_date", "calling_number", "called_number", "duration", "call_type"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def write_bank(path: Path) -> int:
    rows: list[dict] = []
    source = "50100234567890"
    sink = "7799001122334455"
    start = datetime(2024, 7, 20, 10, 30, 0)

    # Structuring: 22 sub-threshold transfers inside a 30-day window.
    for index in range(22):
        ts = start + timedelta(days=index, hours=(index % 5), minutes=(index * 13) % 60)
        rows.append(
            {
                "txn_date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "from_account": source,
                "to_account": sink,
                "amount": 48000,
                "channel": "NEFT" if index % 3 else "IMPS",
                "reference": f"UTR2024STR{index:04d}",
            }
        )
    # A single large transfer that is entirely unremarkable on its own.
    rows.append(
        {
            "txn_date": "2024-08-05 16:20:00",
            "from_account": "7799001122334455",
            "to_account": "9911223344556677",
            "amount": 750000,
            "channel": "RTGS",
            "reference": "UTR2024LARGE001",
        }
    )
    rows.append(
        {
            "txn_date": "2024-08-12 12:05:00",
            "from_account": "9911223344556677",
            "to_account": "50100234567890",
            "amount": 120000,
            "channel": "NEFT",
            "reference": "UTR2024BACK002",
        }
    )
    rows.sort(key=lambda row: row["txn_date"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["txn_date", "from_account", "to_account", "amount", "channel", "reference"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    cdr_rows = write_cdr(SAMPLES / "cdr_jio.csv")
    bank_rows = write_bank(SAMPLES / "bank_transactions.csv")
    print(f"wrote {cdr_rows} CDR rows and {bank_rows} transaction rows to {SAMPLES}")


if __name__ == "__main__":
    main()
