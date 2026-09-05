"""Corpus validation — the same invariants for both generation paths.

Both generators (``generate.py`` in-process, ``build_external.py`` on-disk) must
produce datasets that mean the same thing, so this module checks the same
semantic invariants in both places:

* **Referential integrity** — every ``*_id`` referenced by a row exists in the
  table it points at (case members, asset owners, call/transfer endpoints,
  sighting subjects).
* **Event identity** — every event row (sighting) carries its own id and a
  distinct ``(subject, timestamp)``, so two CCTV sightings stay two events.
* **Background isolation** — a person who is not a member of any case is never
  referenced by a case-scoped call / transfer / sighting / intel row, and owns
  none of a case's assets.  The background population is the coherent
  *unrelated* negative class, not garbage attached to an investigation.
* **Meaningful ownership** — phones, vehicles and accounts always name an
  owner/holder that actually exists; an asset is never attached to a person
  without that claim in the source data.
* **Transaction traceability** — every transfer names two existing accounts,
  both of which have a holder, so ``Person -> Account -> Transaction ->
  Account -> Person`` is always traversable.
* **Valid timestamps** — every event timestamp parses and falls inside a sane
  window (no pre-corpus dates, nothing in the far future).

The checkers only *read*; they never mutate the corpus.  ``validate_generated``
operates on the in-memory model, ``validate_external`` on the on-disk CSV
tables written by ``build_external.py``.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import is only for typing
    from app.synthetic_corpus.generate import SyntheticCorpus

# ---------------------------------------------------------------------------
# Shared micro-checks
# ---------------------------------------------------------------------------

#: The earliest timestamp the corpus may carry (matches the reference corpus
#: window, 2024–2025).  Anything before this is treated as a data defect.
MIN_TIMESTAMP = datetime(2020, 1, 1, tzinfo=timezone.utc)
#: Small future slack for timestamps generated "now" on a machine whose clock
#: skews a little ahead of the validator's.
FUTURE_SLACK_DAYS = 2

_EXTERNAL_TABLES = (
    "cases",
    "persons",
    "phones",
    "vehicles",
    "accounts",
    "locations",
    "organizations",
    "case_members",
    "person_organizations",
    "cdr",
    "transactions",
    "sightings",
    "intel",
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _ts_problems(ts: str | None, what: str, problems: list[str]) -> None:
    parsed = _parse_ts(ts)
    if parsed is None:
        problems.append(f"{what}: unparseable timestamp {ts!r}")
        return
    if parsed < MIN_TIMESTAMP:
        problems.append(f"{what}: timestamp {ts!r} precedes {MIN_TIMESTAMP.date()}")
    if parsed > datetime.now(timezone.utc) + timedelta(days=FUTURE_SLACK_DAYS):
        problems.append(f"{what}: timestamp {ts!r} is in the future")


def _dup_ids(kind: str, ids: list[str], problems: list[str]) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            problems.append(f"{kind}: duplicate id {i!r}")
        seen.add(i)


# ---------------------------------------------------------------------------
# In-memory generator (generate.py)
# ---------------------------------------------------------------------------


def validate_generated(corpus: "SyntheticCorpus") -> list[str]:
    """Validate an in-memory :class:`SyntheticCorpus`.

    Returns a list of human-readable problems; an empty list means the corpus
    satisfies every invariant.
    """
    problems: list[str] = []

    persons = {p.id: p for p in corpus.persons}
    phones = {ph.id: ph for ph in corpus.phones}
    vehicles = {v.id: v for v in corpus.vehicles}
    accounts = {a.id: a for a in corpus.accounts}
    locations = {l.id: l for l in corpus.locations}
    organizations = {o.id: o for o in corpus.organizations}

    _dup_ids("person", [p.id for p in corpus.persons], problems)
    _dup_ids("phone", [ph.id for ph in corpus.phones], problems)
    _dup_ids("vehicle", [v.id for v in corpus.vehicles], problems)
    _dup_ids("account", [a.id for a in corpus.accounts], problems)
    _dup_ids("location", [l.id for l in corpus.locations], problems)
    _dup_ids("organization", [o.id for o in corpus.organizations], problems)
    _dup_ids("case", [c.id for c in corpus.cases], problems)

    # Referential integrity: person -> assets and orgs.
    for p in corpus.persons:
        for pid in p.phone_ids:
            if pid not in phones:
                problems.append(f"person {p.id} references missing phone {pid!r}")
        for vid in p.vehicle_ids:
            if vid not in vehicles:
                problems.append(f"person {p.id} references missing vehicle {vid!r}")
        for aid in p.account_ids:
            if aid not in accounts:
                problems.append(f"person {p.id} references missing account {aid!r}")
        for oid in p.organization_ids:
            if oid not in organizations:
                problems.append(f"person {p.id} references missing organization {oid!r}")

    # Case scoping: everything a case references must exist.
    case_person_ids: set[str] = set()
    case_phone_ids: set[str] = set()
    case_vehicle_ids: set[str] = set()
    case_account_ids: set[str] = set()
    for c in corpus.cases:
        for pid in c.person_ids:
            if pid not in persons:
                problems.append(f"case {c.id} references missing person {pid!r}")
        for pid in c.phone_ids:
            if pid not in phones:
                problems.append(f"case {c.id} references missing phone {pid!r}")
        for vid in c.vehicle_ids:
            if vid not in vehicles:
                problems.append(f"case {c.id} references missing vehicle {vid!r}")
        for lid in c.location_ids:
            if lid not in locations:
                problems.append(f"case {c.id} references missing location {lid!r}")
        for aid in c.account_ids:
            if aid not in accounts:
                problems.append(f"case {c.id} references missing account {aid!r}")
        case_person_ids.update(c.person_ids)
        case_phone_ids.update(c.phone_ids)
        case_vehicle_ids.update(c.vehicle_ids)
        case_account_ids.update(c.account_ids)

    # Calls and transfers reference existing endpoints.
    case_call_phones: set[str] = set()
    for call in corpus.calls:
        for pid in (call.src_phone, call.dst_phone):
            if pid not in phones:
                problems.append(f"call references missing phone {pid!r}")
        case_call_phones.add(call.src_phone)
        case_call_phones.add(call.dst_phone)
        _ts_problems(_dt_iso(call.ts), f"call {call.src_phone}->{call.dst_phone}", problems)

    for txn in corpus.transactions:
        src = accounts.get(txn.src_account)
        dst = accounts.get(txn.dst_account)
        if src is None:
            problems.append(f"transaction {txn.id} references missing source account {txn.src_account!r}")
        if dst is None:
            problems.append(f"transaction {txn.id} references missing target account {txn.dst_account!r}")
        if txn.amount <= 0:
            problems.append(f"transaction {txn.id} has non-positive amount {txn.amount}")
        if src is not None and not src.controller_ids:
            problems.append(f"transaction {txn.id} source account {txn.src_account!r} has no holder")
        if dst is not None and not dst.controller_ids:
            problems.append(f"transaction {txn.id} target account {txn.dst_account!r} has no holder")
        _ts_problems(_dt_iso(txn.ts), f"transaction {txn.id}", problems)

    # Meaningful ownership: an asset that *appears* anywhere in the corpus must
    # name a real owner/holder; unreferenced pool spares (generate.py keeps a
    # pool larger than its networks) are inert and never enter the graph, so
    # they are not required to have an owner.
    referenced_phones: set[str] = set()
    referenced_vehicles: set[str] = set()
    referenced_accounts: set[str] = set()
    for p in corpus.persons:
        referenced_phones.update(p.phone_ids)
        referenced_vehicles.update(p.vehicle_ids)
        referenced_accounts.update(p.account_ids)
    for c in corpus.cases:
        referenced_phones.update(c.phone_ids)
        referenced_vehicles.update(c.vehicle_ids)
        referenced_accounts.update(c.account_ids)
    for call in corpus.calls + corpus.background_calls:
        referenced_phones.add(call.src_phone)
        referenced_phones.add(call.dst_phone)
    for txn in corpus.transactions + corpus.background_transactions:
        referenced_accounts.add(txn.src_account)
        referenced_accounts.add(txn.dst_account)
    for s in corpus.sightings:
        referenced_vehicles.add(s.vehicle_id)

    for ph in corpus.phones:
        if ph.id not in referenced_phones:
            continue
        if not ph.owner_ids:
            problems.append(f"phone {ph.id} ({ph.number}) has no owner")
        for oid in ph.owner_ids:
            if oid not in persons:
                problems.append(f"phone {ph.id} references missing owner {oid!r}")
    for v in corpus.vehicles:
        if v.id not in referenced_vehicles:
            continue
        if not v.owner_ids:
            problems.append(f"vehicle {v.id} ({v.plate}) has no owner")
        for oid in v.owner_ids:
            if oid not in persons:
                problems.append(f"vehicle {v.id} references missing owner {oid!r}")
    for a in corpus.accounts:
        if a.id not in referenced_accounts:
            continue
        if not a.controller_ids:
            problems.append(f"account {a.id} has no holder")
        for oid in a.controller_ids:
            if oid not in persons:
                problems.append(f"account {a.id} references missing holder {oid!r}")

    # Background isolation: the unrelated population must stay outside every
    # case, every case asset list, every case call, transfer and sighting.
    bg_person_ids = {p.id for p in corpus.persons if p.subject_in_no_case}
    case_txn_accounts = {t.src_account for t in corpus.transactions} | {
        t.dst_account for t in corpus.transactions
    }
    for p in corpus.persons:
        if not p.subject_in_no_case:
            continue
        if not p.canonical_name.strip():
            problems.append(f"background person {p.id} has an empty name")
        if not p.phone_ids:
            problems.append(f"background person {p.id} has no phone (background population is structurally valid)")
        if p.id in case_person_ids:
            problems.append(f"background person {p.id} is attached to a case")
        for pid in p.phone_ids:
            if pid in case_phone_ids:
                problems.append(f"background person {p.id} phone {pid!r} is attached to a case")
            if pid in case_call_phones:
                problems.append(f"background person {p.id} phone {pid!r} appears in a case call")
        for vid in p.vehicle_ids:
            if vid in case_vehicle_ids:
                problems.append(f"background person {p.id} vehicle {vid!r} is attached to a case")
        for aid in p.account_ids:
            if aid in case_account_ids:
                problems.append(f"background person {p.id} account {aid!r} is attached to a case")
            if aid in case_txn_accounts:
                problems.append(f"background person {p.id} account {aid!r} appears in a case transfer")

    # Event identity: every sighting is its own entity.
    _dup_ids("sighting", [s.id for s in corpus.sightings], problems)
    seen_keys: set[tuple[str, str]] = set()
    for s in corpus.sightings:
        if not s.id:
            problems.append("sighting has an empty id")
        if s.vehicle_id not in vehicles:
            problems.append(f"sighting {s.id} references missing vehicle {s.vehicle_id!r}")
        if s.location_id not in locations:
            problems.append(f"sighting {s.id} references missing location {s.location_id!r}")
        if s.person_id and s.person_id not in persons:
            problems.append(f"sighting {s.id} references missing subject {s.person_id!r}")
        if s.person_id in bg_person_ids:
            problems.append(f"sighting {s.id} subject {s.person_id!r} is background population")
        key = (s.person_id, _dt_iso(s.ts))
        if key in seen_keys:
            problems.append(f"sighting {s.id} collides with another sighting on (subject, timestamp) {key!r}")
        seen_keys.add(key)
        _ts_problems(_dt_iso(s.ts), f"sighting {s.id}", problems)

    # Background activity stays coherent and case-free.
    for call in corpus.background_calls:
        for pid in (call.src_phone, call.dst_phone):
            if pid not in phones:
                problems.append(f"background call references missing phone {pid!r}")
                continue
            owners = phones[pid].owner_ids
            if not owners or any(o not in bg_person_ids for o in owners):
                problems.append(f"background call uses a non-background phone {pid!r}")
        _ts_problems(_dt_iso(call.ts), "background call", problems)
    for txn in corpus.background_transactions:
        src = accounts.get(txn.src_account)
        dst = accounts.get(txn.dst_account)
        if src is None or dst is None:
            problems.append(f"background transaction {txn.id} references a missing account")
        if txn.amount <= 0:
            problems.append(f"background transaction {txn.id} has non-positive amount")
        _ts_problems(_dt_iso(txn.ts), f"background transaction {txn.id}", problems)

    return problems


def _dt_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


# ---------------------------------------------------------------------------
# External corpus (build_external.py output on disk)
# ---------------------------------------------------------------------------


def _read_tables(root: Path) -> dict[str, list[dict[str, str]]]:
    """Read the operational CSVs written by ``build_external.py``."""
    tables: dict[str, list[dict[str, str]]] = {}
    op = root / "operational"
    if not op.is_dir():
        return tables
    for name in _EXTERNAL_TABLES:
        # build_external writes cdr.csv, transactions.csv, vehicle_sightings.csv,
        # intelligence_reports.csv under these table keys.
        filename = {
            "cdr": "cdr.csv",
            "transactions": "transactions.csv",
            "sightings": "vehicle_sightings.csv",
            "intel": "intelligence_reports.csv",
            "case_members": "case_members.csv",
            "person_organizations": "person_organizations.csv",
        }.get(name, f"{name}.csv")
        path = op / filename
        if not path.is_file():
            continue
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                tables[name] = [dict(row) for row in csv.DictReader(handle)]
        except (OSError, UnicodeError, csv.Error):
            continue
    return tables


def validate_external(root: Path) -> list[str]:
    """Validate an on-disk external corpus (the ``build_external.py`` output).

    Returns a list of human-readable problems; an empty list means the corpus
    satisfies every invariant.
    """
    problems: list[str] = []
    tables = _read_tables(root)
    if not tables:
        return [f"no operational CSV tables found under {root}"]

    persons = {r.get("person_id", ""): r for r in tables.get("persons", []) if r.get("person_id")}
    phones = {r.get("phone_id", ""): r for r in tables.get("phones", []) if r.get("phone_id")}
    vehicles = {r.get("vehicle_id", ""): r for r in tables.get("vehicles", []) if r.get("vehicle_id")}
    accounts = {r.get("account_id", ""): r for r in tables.get("accounts", []) if r.get("account_id")}
    locations = {r.get("location_id", ""): r for r in tables.get("locations", []) if r.get("location_id")}
    organizations = {
        r.get("organization_id", ""): r for r in tables.get("organizations", []) if r.get("organization_id")
    }
    cases = {r.get("case_id", ""): r for r in tables.get("cases", []) if r.get("case_id")}

    _dup_ids("case_id", list(cases), problems)
    _dup_ids("person_id", list(persons), problems)
    _dup_ids("phone_id", list(phones), problems)
    _dup_ids("vehicle_id", list(vehicles), problems)
    _dup_ids("account_id", list(accounts), problems)
    _dup_ids("location_id", list(locations), problems)
    _dup_ids("organization_id", list(organizations), problems)

    # Ownership: every asset names an existing owner/holder.
    for pid, row in phones.items():
        owner = row.get("owner_person_id", "")
        if not owner:
            problems.append(f"phone {pid!r} has no owner")
        elif owner not in persons:
            problems.append(f"phone {pid!r} references missing owner {owner!r}")
    for vid, row in vehicles.items():
        owner = row.get("owner_person_id", "")
        if not owner:
            problems.append(f"vehicle {vid!r} has no owner")
        elif owner not in persons:
            problems.append(f"vehicle {vid!r} references missing owner {owner!r}")
    for aid, row in accounts.items():
        holder = row.get("holder_person_id", "")
        if not holder:
            problems.append(f"account {aid!r} has no holder")
        elif holder not in persons:
            problems.append(f"account {aid!r} references missing holder {holder!r}")

    # Case membership + referential integrity.
    case_member_persons: dict[str, set[str]] = {}   # case_id -> person_ids
    members_of: dict[str, set[str]] = {}            # person_id -> case_ids
    for row in tables.get("case_members", []):
        cid, pid = row.get("case_id", ""), row.get("person_id", "")
        if not cid or not pid:
            continue
        if cid not in cases:
            problems.append(f"case_member row references missing case {cid!r}")
        if pid not in persons:
            problems.append(f"case_member row references missing person {pid!r}")
        case_member_persons.setdefault(cid, set()).add(pid)
        members_of.setdefault(pid, set()).add(cid)

    for row in tables.get("person_organizations", []):
        pid, oid = row.get("person_id", ""), row.get("organization_id", "")
        if pid and pid not in persons:
            problems.append(f"person_organizations row references missing person {pid!r}")
        if oid and oid not in organizations:
            problems.append(f"person_organizations row references missing organization {oid!r}")

    for cid in cases:
        if not case_member_persons.get(cid):
            problems.append(f"case {cid!r} has no members")

    def _event_case_ok(cid: str, kind: str, row_id: str) -> bool:
        if cid and cid not in cases:
            problems.append(f"{kind} {row_id} references missing case {cid!r}")
            return False
        return True

    def _background_owner_ok(person_ids: list[str], kind: str, row_id: str) -> bool:
        """A case-scoped event must never reference a non-member (background) person."""
        for pid in person_ids:
            if pid and pid not in persons:
                problems.append(f"{kind} {row_id} references missing person {pid!r}")
                return False
            if pid and not members_of.get(pid):
                problems.append(
                    f"{kind} {row_id} references background person {pid!r} who is not a member of any case"
                )
                return False
        return True

    # CDR.
    _dup_ids("cdr_id", [r.get("cdr_id", "") for r in tables.get("cdr", [])], problems)
    for row in tables.get("cdr", []):
        rid = row.get("cdr_id", "")
        for phone_id in (row.get("from_phone_id", ""), row.get("to_phone_id", "")):
            if phone_id and phone_id not in phones:
                problems.append(f"cdr {rid} references missing phone {phone_id!r}")
        cid = row.get("case_id", "")
        _event_case_ok(cid, "cdr", rid)
        _ts_problems(row.get("timestamp"), f"cdr {rid}", problems)
        if cid:  # case-scoped: endpoints must belong to that case's members
            for phone_id in (row.get("from_phone_id", ""), row.get("to_phone_id", "")):
                owner = phones.get(phone_id, {}).get("owner_person_id", "")
                if owner and cid in case_member_persons and owner not in case_member_persons[cid]:
                    problems.append(
                        f"cdr {rid} references phone {phone_id!r} owned by non-member {owner!r}"
                    )

    # Transactions.
    _dup_ids("transaction_id", [r.get("transaction_id", "") for r in tables.get("transactions", [])], problems)
    for row in tables.get("transactions", []):
        rid = row.get("transaction_id", "")
        src = accounts.get(row.get("from_account_id", ""))
        dst = accounts.get(row.get("to_account_id", ""))
        if not src:
            problems.append(f"transaction {rid} references missing source account {row.get('from_account_id')!r}")
        if not dst:
            problems.append(f"transaction {rid} references missing target account {row.get('to_account_id')!r}")
        if src and not src.get("holder_person_id"):
            problems.append(f"transaction {rid} source account has no holder")
        if dst and not dst.get("holder_person_id"):
            problems.append(f"transaction {rid} target account has no holder")
        try:
            amount = float(row.get("amount_inr", "") or 0)
            if amount <= 0:
                problems.append(f"transaction {rid} has non-positive amount")
        except ValueError:
            problems.append(f"transaction {rid} has unparseable amount {row.get('amount_inr')!r}")
        cid = row.get("case_id", "")
        _event_case_ok(cid, "transaction", rid)
        _ts_problems(row.get("timestamp"), f"transaction {rid}", problems)
        if cid and cid in case_member_persons:
            for acc_id in (row.get("from_account_id", ""), row.get("to_account_id", "")):
                holder = accounts.get(acc_id, {}).get("holder_person_id", "")
                if holder and holder not in case_member_persons[cid]:
                    problems.append(
                        f"transaction {rid} references account {acc_id!r} held by non-member {holder!r}"
                    )

    # Sightings (each row is its own event).
    _dup_ids("sighting_id", [r.get("sighting_id", "") for r in tables.get("sightings", [])], problems)
    seen_keys: set[tuple[str, str]] = set()
    for row in tables.get("sightings", []):
        rid = row.get("sighting_id", "")
        vid = row.get("vehicle_id", "")
        lid = row.get("location_id", "")
        if vid and vid not in vehicles:
            problems.append(f"sighting {rid} references missing vehicle {vid!r}")
        if lid and lid not in locations:
            problems.append(f"sighting {rid} references missing location {lid!r}")
        cid = row.get("case_id", "")
        _event_case_ok(cid, "sighting", rid)
        _ts_problems(row.get("timestamp"), f"sighting {rid}", problems)
        if vid and vid in vehicles:
            owner = vehicles[vid].get("owner_person_id", "")
            key = (owner, row.get("timestamp", ""))
            if key in seen_keys:
                problems.append(f"sighting {rid} collides with another sighting on (owner, timestamp)")
            seen_keys.add(key)
            if cid and owner and cid in case_member_persons and owner not in case_member_persons[cid]:
                problems.append(f"sighting {rid} vehicle {vid!r} owned by non-member {owner!r}")
            if owner and not members_of.get(owner):
                problems.append(f"sighting {rid} vehicle {vid!r} owned by background person {owner!r}")

    # Intel reports.
    _dup_ids("report_id", [r.get("report_id", "") for r in tables.get("intel", [])], problems)
    for row in tables.get("intel", []):
        rid = row.get("report_id", "")
        subj = row.get("subject_person_id", "")
        lid = row.get("location_id", "")
        if subj and subj not in persons:
            problems.append(f"intel {rid} references missing subject {subj!r}")
        if lid and lid not in locations:
            problems.append(f"intel {rid} references missing location {lid!r}")
        cid = row.get("case_id", "")
        _event_case_ok(cid, "intel", rid)
        _ts_problems(row.get("report_date"), f"intel {rid}", problems)
        if cid and subj and cid in case_member_persons and subj not in case_member_persons[cid]:
            problems.append(f"intel {rid} subject {subj!r} is not a member of case {cid!r}")

    return problems
