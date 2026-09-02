"""Synthetic development corpus generator.

Design summary (see PRD §4–§13):

* Configurable via environment variables / CLI.  Changing
  ``SYNTHETIC_PERSON_COUNT`` changes the size of the corpus without code
  changes — there are no hard-coded "30 people" assumptions.
* Realistic Indian-style names, addresses, phone numbers, vehicle plates,
  bank accounts, transactions, calls.
* Built-in name/format variation (``"Ramesh Kumar"`` / ``"Ramesh K."`` /
  ``"RAMESH KUMAR"`` / ``"R. Kumar"``) that intentionally creates
  entity-resolution ambiguity.
* Several independent criminal **networks** (configurable count) plus
  configurable **bridge** people who appear in multiple networks — so
  betweenness, PageRank and cross-case analytics have real signal to find.
* **Indirect connections** (A → phone → phone → B; A → vehicle → location → B;
  account → account → person) rather than fake "CONNECTED_TO" edges.
* Multiple **cases** with overlapping entities (cross-case signal).
* **Temporal realism**: timestamps over a configurable window with bursts of
  calls/transfers around incident dates and time-chains of transfers.
* **Dirty/missing data**: controlled rates of missing fields, whitespace
  corruption, duplicate source records, conflicting attributes.
* **Ground truth** (identity groups, bridges, intended scenarios) is written
  to a separate JSON file — never to the operational graph/DB and never
  exposed to the investigator UI.
* Safety: refuses to run against a non-empty production database unless an
  explicit ``--yes-i-am-sure`` flag is given; all records are tagged
  ``source_environment="synthetic"``.

The CLI entry point is ``python -m app.synthetic_corpus.generate``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings, get_settings
from app.db.base import new_uuid, utcnow
from app.db.models import Case as DBCase
from app.db.session import async_session, init_db
from app.domain.enums import CaseStatus, DocumentType, Role, SourceConfidence
from app.logging import configure_logging, get_logger
from app.synthetic_corpus.names import (
    BANK_IFSC_PREFIXES,
    BANK_NAMES,
    CASE_TYPES,
    DISTRICTS,
    FATHER_HONORIFICS,
    GIVEN_NAMES_F,
    GIVEN_NAMES_M,
    INDIAN_STATES,
    IPC_SECTIONS,
    OCCUPATIONS,
    ORG_PREFIXES,
    ORG_SUFFIXES,
    RTO_CODES,
    SPOUSE_HONORIFICS,
    STREET_WORDS,
    SURNAMES,
)

log = get_logger("crimelink.synthetic")

SOURCE_ENV = "synthetic"


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class CorpusOptions:
    seed: int = 20260902
    version: int = 1
    person_count: int = 60
    case_count: int = 12
    phone_count: int = 75
    vehicle_count: int = 30
    location_count: int = 25
    account_count: int = 35
    organization_count: int = 12
    document_count: int = 60
    call_count: int = 350
    transaction_count: int = 180
    bridge_count: int = 4
    network_count: int = 3
    missing_field_rate: float = 0.12
    duplicate_rate: float = 0.08
    name_variation_rate: float = 0.15
    time_window_days: int = 120

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "CorpusOptions":
        s = settings or get_settings()
        return cls(
            seed=s.synthetic_corpus_seed,
            version=s.synthetic_corpus_version,
            person_count=s.synthetic_person_count,
            case_count=s.synthetic_case_count,
            phone_count=s.synthetic_phone_count,
            vehicle_count=s.synthetic_vehicle_count,
            location_count=s.synthetic_location_count,
            account_count=s.synthetic_account_count,
            organization_count=s.synthetic_organization_count,
            document_count=s.synthetic_document_count,
            call_count=s.synthetic_call_count,
            transaction_count=s.synthetic_transaction_count,
            bridge_count=s.synthetic_bridge_count,
            network_count=s.synthetic_network_count,
            missing_field_rate=s.synthetic_missing_field_rate,
            duplicate_rate=s.synthetic_duplicate_rate,
            name_variation_rate=s.synthetic_name_variation_rate,
        )


# ---------------------------------------------------------------------------
# Synthetic entity model
# ---------------------------------------------------------------------------


@dataclass
class SynthPerson:
    id: str
    pid: str
    canonical_name: str
    gender: str
    age: int | None
    dob: str | None
    father_or_spouse_label: str | None
    father_or_spouse_name: str | None
    addresses: list[str]
    district: str
    state_code: str
    state_name: str
    occupation: str | None
    phone_ids: list[str] = field(default_factory=list)
    vehicle_ids: list[str] = field(default_factory=list)
    account_ids: list[str] = field(default_factory=list)
    organization_ids: list[str] = field(default_factory=list)
    network: int | None = None
    is_bridge: bool = False


@dataclass
class SynthPhone:
    id: str
    number: str
    owner_ids: list[str] = field(default_factory=list)
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    is_burner: bool = False


@dataclass
class SynthVehicle:
    id: str
    plate: str
    owner_ids: list[str] = field(default_factory=list)
    rto: str = "RJ14"


@dataclass
class SynthLocation:
    id: str
    address: str
    district: str
    state_code: str


@dataclass
class SynthAccount:
    id: str
    number: str
    ifsc: str
    bank: str
    controller_ids: list[str] = field(default_factory=list)


@dataclass
class SynthOrganization:
    id: str
    name: str
    member_ids: list[str] = field(default_factory=list)


@dataclass
class SynthCase:
    id: str
    case_number: str
    title: str
    district: str
    state_code: str
    incident_date: datetime
    sections: list[str]
    person_ids: list[str] = field(default_factory=list)
    phone_ids: list[str] = field(default_factory=list)
    vehicle_ids: list[str] = field(default_factory=list)
    location_ids: list[str] = field(default_factory=list)
    account_ids: list[str] = field(default_factory=list)


@dataclass
class SynthCall:
    src_phone: str
    dst_phone: str
    ts: datetime
    duration_s: int


@dataclass
class SynthTransaction:
    id: str
    src_account: str
    dst_account: str
    amount: float
    ts: datetime
    remarks: str = ""


@dataclass
class SyntheticCorpus:
    """Configurable, deterministic synthetic development corpus."""

    # Schema version — bump when the shape changes, increment so tests that
    # depend on a particular record shape can pin.
    version: int = 1

    opts: CorpusOptions = field(default_factory=CorpusOptions)
    persons: list[SynthPerson] = field(default_factory=list)
    phones: list[SynthPhone] = field(default_factory=list)
    vehicles: list[SynthVehicle] = field(default_factory=list)
    locations: list[SynthLocation] = field(default_factory=list)
    accounts: list[SynthAccount] = field(default_factory=list)
    organizations: list[SynthOrganization] = field(default_factory=list)
    cases: list[SynthCase] = field(default_factory=list)
    calls: list[SynthCall] = field(default_factory=list)
    transactions: list[SynthTransaction] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    ground_truth: dict[str, Any] = field(default_factory=dict)
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    # ------------------------------------------------------------ generation

    def build(self) -> None:
        self.rng = random.Random(self.opts.seed)
        self._locations()
        self._organizations()
        self._persons()
        self._phones()
        self._vehicles()
        self._accounts()
        self._networks_and_bridges()
        self._cases()
        self._assign_entities_to_cases()
        self._calls()
        self._transactions()
        self._build_ground_truth()
        self._documents()

    # ----------------------------------------------------- entity factories

    def _locations(self) -> None:
        for i in range(self.opts.location_count):
            state_code, state_name = self.rng.choice(INDIAN_STATES)
            district = self.rng.choice(DISTRICTS)
            street = self.rng.choice(STREET_WORDS)
            number = self.rng.randint(1, 400)
            address = f"{number}, {street}, {district}"
            self.locations.append(
                SynthLocation(
                    id=f"LOC_{i+1:03d}",
                    address=address,
                    district=district,
                    state_code=state_code,
                )
            )

    def _organizations(self) -> None:
        for i in range(self.opts.organization_count):
            prefix = self.rng.choice(ORG_PREFIXES)
            suffix = self.rng.choice(ORG_SUFFIXES)
            name = f"{prefix} {suffix}"
            self.organizations.append(SynthOrganization(id=f"ORG_{i+1:03d}", name=name))

    def _persons(self) -> None:
        used_names: set[str] = set()
        for i in range(self.opts.person_count):
            gender = self.rng.choice(["M", "M", "M", "F", "F"])
            given_list = GIVEN_NAMES_M if gender == "M" else GIVEN_NAMES_F
            given = self.rng.choice(given_list)
            surname = self.rng.choice(SURNAMES)
            canonical = f"{given} {surname}"
            # ensure uniqueness
            n = 2
            base = canonical
            while canonical in used_names:
                canonical = f"{base} {chr(64 + n)}"
                n += 1
            used_names.add(canonical)
            age = self.rng.randint(19, 62) if self.rng.random() > self.opts.missing_field_rate / 3 else None
            dob = None
            if age is not None and self.rng.random() > self.opts.missing_field_rate:
                year = 2026 - age
                month = self.rng.randint(1, 12)
                day = self.rng.randint(1, 28)
                dob = f"{day:02d}/{month:02d}/{year}"
            # parent/spouse
            label: str | None = None
            rel_name: str | None = None
            if self.rng.random() > self.opts.missing_field_rate:
                if gender == "M":
                    label = self.rng.choice(FATHER_HONORIFICS)
                    rel = self.rng.choice(GIVEN_NAMES_M)
                    rel_sur = self.rng.choice(SURNAMES)
                    rel_name = f"{rel} {rel_sur}"
                else:
                    label = self.rng.choice(SPOUSE_HONORIFICS + FATHER_HONORIFICS)
                    if label in FATHER_HONORIFICS:
                        rel = self.rng.choice(GIVEN_NAMES_M)
                    else:
                        rel = self.rng.choice(GIVEN_NAMES_M)
                    rel_sur = self.rng.choice(SURNAMES)
                    rel_name = f"{rel} {rel_sur}"
            # addresses
            locs = self.rng.sample(self.locations, k=min(self.rng.randint(1, 2), len(self.locations)))
            addresses = [loc.address for loc in locs]
            district = locs[0].district if locs else self.rng.choice(DISTRICTS)
            state_code = locs[0].state_code if locs else "RJ"
            state_name = next((s[1] for s in INDIAN_STATES if s[0] == state_code), "Rajasthan")
            occupation = self.rng.choice(OCCUPATIONS) if self.rng.random() > self.opts.missing_field_rate / 2 else None
            pid = f"PERSON_{i+1:03d}"
            self.persons.append(
                SynthPerson(
                    id=pid,
                    pid=pid,
                    canonical_name=canonical,
                    gender=gender,
                    age=age,
                    dob=dob,
                    father_or_spouse_label=label,
                    father_or_spouse_name=rel_name,
                    addresses=addresses,
                    district=district,
                    state_code=state_code,
                    state_name=state_name,
                    occupation=occupation,
                )
            )

    def _phones(self) -> None:
        existing: set[str] = set()
        for i in range(self.opts.phone_count):
            while True:
                number = "+919" + "".join(str(self.rng.randint(0, 9)) for _ in range(9))
                if number not in existing:
                    existing.add(number)
                    break
            pid = f"PHONE_{i+1:03d}"
            self.phones.append(SynthPhone(id=pid, number=number))

    def _vehicles(self) -> None:
        existing: set[str] = set()
        for i in range(self.opts.vehicle_count):
            while True:
                rto = self.rng.choice(RTO_CODES)
                letters = "".join(chr(self.rng.randint(65, 90)) for _ in range(2))
                digits = self.rng.randint(1, 9999)
                plate = f"{rto}{letters}{digits:04d}"
                if plate not in existing:
                    existing.add(plate)
                    break
            self.vehicles.append(
                SynthVehicle(id=f"VEHICLE_{i+1:03d}", plate=plate, rto=rto)
            )

    def _accounts(self) -> None:
        existing: set[str] = set()
        for i in range(self.opts.account_count):
            ifsc = self.rng.choice(BANK_IFSC_PREFIXES) + "0" + str(self.rng.randint(10000, 99999))
            while True:
                number = str(self.rng.randint(1000000000, 999999999999))
                key = (ifsc, number)
                if key not in existing:
                    existing.add(key)
                    break
            self.accounts.append(
                SynthAccount(
                    id=f"ACCOUNT_{i+1:03d}",
                    number=number,
                    ifsc=ifsc,
                    bank=self.rng.choice(BANK_NAMES),
                )
            )

    # ------------------------------------------------------- network assembly

    def _networks_and_bridges(self) -> None:
        n_nets = max(1, self.opts.network_count)
        nets: list[list[int]] = [[] for _ in range(n_nets)]
        # assign core members per network
        person_idxs = list(range(len(self.persons)))
        self.rng.shuffle(person_idxs)
        core_per_net = max(3, self.opts.person_count // (n_nets * 3))
        pos = 0
        for n in range(n_nets):
            for _ in range(core_per_net):
                if pos >= len(person_idxs):
                    break
                idx = person_idxs[pos]
                self.persons[idx].network = n
                nets[n].append(idx)
                pos += 1
        # mark some as bridges — later assigned to multiple nets
        bridge_count = min(self.opts.bridge_count, len(self.persons) - pos)
        bridges = []
        for _ in range(bridge_count):
            if pos >= len(person_idxs):
                break
            idx = person_idxs[pos]
            self.persons[idx].is_bridge = True
            # attach to at least two networks
            self.persons[idx].network = 0
            nets[0].append(idx)
            if n_nets > 1:
                nets[-1].append(idx)
            bridges.append(idx)
            pos += 1
        # remaining persons get attached to a random network
        for idx in person_idxs[pos:]:
            net = self.rng.randrange(n_nets)
            self.persons[idx].network = net
            nets[net].append(idx)

        # Assign phones / vehicles / accounts / orgs
        for n, member_idxs in enumerate(nets):
            # Share a pool of phones/vehicles/accounts among network members
            n_phones = max(4, len(member_idxs) * 3 // 2)
            phones_sample = self.rng.sample(self.phones, min(n_phones, len(self.phones)))
            n_vehicles = max(2, len(member_idxs) // 3)
            vehicles_sample = self.rng.sample(self.vehicles, min(n_vehicles, len(self.vehicles)))
            n_accounts = max(2, len(member_idxs) // 3)
            accounts_sample = self.rng.sample(self.accounts, min(n_accounts, len(self.accounts)))
            orgs_sample = self.rng.sample(self.organizations, min(2, len(self.organizations)))
            for p_idx in member_idxs:
                p = self.persons[p_idx]
                # assign 1-3 phones
                p_phones = self.rng.sample(phones_sample, k=min(self.rng.randint(1, 3), len(phones_sample)))
                p.phone_ids = [ph.id for ph in p_phones]
                for ph in p_phones:
                    if p.id not in ph.owner_ids:
                        ph.owner_ids.append(p.id)
                # 0-2 vehicles
                if vehicles_sample and self.rng.random() > 0.35:
                    n_v = self.rng.randint(1, min(2, len(vehicles_sample)))
                    p_vehicles = self.rng.sample(vehicles_sample, k=n_v)
                    p.vehicle_ids = [v.id for v in p_vehicles]
                    for v in p_vehicles:
                        if p.id not in v.owner_ids:
                            v.owner_ids.append(p.id)
                # 0-2 accounts
                if accounts_sample and self.rng.random() > 0.4:
                    n_a = self.rng.randint(1, min(2, len(accounts_sample)))
                    p_accounts = self.rng.sample(accounts_sample, k=n_a)
                    p.account_ids = [a.id for a in p_accounts]
                    for a in p_accounts:
                        if p.id not in a.controller_ids:
                            a.controller_ids.append(p.id)
                # 0-1 organization
                if orgs_sample and self.rng.random() > 0.5:
                    org = self.rng.choice(orgs_sample)
                    p.organization_ids = [org.id]
                    if p.id not in org.member_ids:
                        org.member_ids.append(p.id)

        # Some phones are "burner": very few owners and later used heavily for
        # a short burst
        burner_count = max(2, self.opts.phone_count // 20)
        for ph in self.rng.sample(self.phones, burner_count):
            ph.is_burner = True
            ph.activated_at = utcnow() - timedelta(days=self.rng.randint(7, 60))
            ph.deactivated_at = ph.activated_at + timedelta(days=self.rng.randint(5, 21))

        self.ground_truth["networks"] = [
            {"network": n, "person_ids": [self.persons[i].id for i in members]}
            for n, members in enumerate(nets)
        ]
        self.ground_truth["bridge_person_ids"] = [self.persons[i].id for i in bridges]

    # ---------------------------------------------------------------- cases

    def _cases(self) -> None:
        base_ts = utcnow() - timedelta(days=self.opts.time_window_days)
        for i in range(self.opts.case_count):
            state_code, state_name = self.rng.choice(INDIAN_STATES)
            district = self.rng.choice(DISTRICTS)
            incident_ts = base_ts + timedelta(
                days=self.rng.randint(1, max(2, self.opts.time_window_days - 5))
            )
            case_number = (
                f"FIR/{incident_ts.year}/{(i+1):04d}/PS-{district[:3].upper()}"
            )
            title = f"{self.rng.choice(CASE_TYPES)} — {district}"
            sections = self.rng.sample(IPC_SECTIONS, k=self.rng.randint(2, 5))
            self.cases.append(
                SynthCase(
                    id=f"case:{uuid4().hex}",
                    case_number=case_number,
                    title=title,
                    district=district,
                    state_code=state_code,
                    incident_date=incident_ts,
                    sections=sections,
                )
            )

    def _assign_entities_to_cases(self) -> None:
        """Cross-case assignment: overlap entities so cross-case analytics fires."""
        # shuffle persons/phones/vehicles/accounts/locations across cases
        for c in self.cases:
            # choose a core network
            net = self.rng.randrange(max(1, self.opts.network_count))
            members = [p for p in self.persons if p.network == net]
            if not members:
                members = self.persons
            selected_persons = self.rng.sample(
                members, k=min(self.rng.randint(4, 8), len(members))
            )
            # sprinkle some cross-case people (the bridges and others)
            bridges = [p for p in self.persons if p.is_bridge and p not in selected_persons]
            if bridges and self.rng.random() > 0.4:
                selected_persons.append(self.rng.choice(bridges))
            c.person_ids = [p.id for p in selected_persons]
            c.phone_ids = []
            c.vehicle_ids = []
            c.account_ids = []
            for p in selected_persons:
                c.phone_ids.extend(p.phone_ids)
                c.vehicle_ids.extend(p.vehicle_ids)
                c.account_ids.extend(p.account_ids)
            c.phone_ids = list(set(c.phone_ids))
            c.vehicle_ids = list(set(c.vehicle_ids))
            c.account_ids = list(set(c.account_ids))
            # locations — each case gets a few relevant locations including a
            # "safe house" style shared location
            locs = self.rng.sample(
                self.locations, k=min(self.rng.randint(2, 4), len(self.locations))
            )
            c.location_ids = [l.id for l in locs]

    # ----------------------------------------------------- calls & transfers

    def _calls(self) -> None:
        base_ts = utcnow() - timedelta(days=self.opts.time_window_days)
        # For each case, generate calls between phones mentioned in that case,
        # plus cross-network calls via bridge phones (the hidden connection).
        phone_by_id = {p.id: p for p in self.phones}
        for c in self.cases:
            phones = [phone_by_id[pid] for pid in c.phone_ids if pid in phone_by_id]
            if len(phones) < 2:
                continue
            n_case_calls = self.opts.call_count // self.opts.case_count
            for _ in range(n_case_calls):
                src, dst = self.rng.sample(phones, 2)
                ts = c.incident_date + timedelta(
                    days=self.rng.randint(-20, 5),
                    hours=self.rng.randint(0, 23),
                    minutes=self.rng.randint(0, 59),
                )
                # clusters of rapid contact before/after incident
                if self.rng.random() < 0.25:
                    ts = c.incident_date + timedelta(
                        hours=self.rng.randint(-48, 48),
                        minutes=self.rng.randint(0, 59),
                    )
                self.calls.append(
                    SynthCall(
                        src_phone=src.id,
                        dst_phone=dst.id,
                        ts=ts,
                        duration_s=self.rng.randint(10, 900),
                    )
                )
        # Burner fanout calls
        for ph in self.phones:
            if ph.is_burner and ph.activated_at and ph.deactivated_at:
                others = [p for p in self.phones if p.id != ph.id]
                if not others:
                    continue
                fanout = self.rng.randint(15, 30)
                targets = self.rng.sample(others, k=min(fanout, len(others)))
                for tgt in targets:
                    span = (ph.deactivated_at - ph.activated_at).total_seconds()
                    off = self.rng.uniform(0, span)
                    ts = ph.activated_at + timedelta(seconds=off)
                    self.calls.append(
                        SynthCall(
                            src_phone=ph.id, dst_phone=tgt.id, ts=ts,
                            duration_s=self.rng.randint(5, 120),
                        )
                    )

    def _transactions(self) -> None:
        account_by_id = {a.id: a for a in self.accounts}
        # Chain transfers through mule accounts (structuring signature)
        for _ in range(self.opts.transaction_count):
            src_acc = self.rng.choice(self.accounts)
            # 80% same-case, 20% mule chain (different account / network)
            if self.rng.random() < 0.7:
                candidates = [
                    a for a in self.accounts
                    if a.id != src_acc.id
                    and (set(a.controller_ids) & set(src_acc.controller_ids) == set())
                ] or [a for a in self.accounts if a.id != src_acc.id]
            else:
                candidates = [a for a in self.accounts if a.id != src_acc.id]
            if not candidates:
                continue
            dst_acc = self.rng.choice(candidates)
            # structuring: many sub-50k amounts
            if self.rng.random() < 0.35:
                amount = self.rng.randint(20000, 49000) * 100 / 100
            else:
                amount = round(self.rng.uniform(5000, 500000), 2)
            # temporal — most around case incident dates
            c = self.rng.choice(self.cases) if self.cases else None
            if c:
                ts = c.incident_date + timedelta(
                    days=self.rng.randint(-30, 10),
                    hours=self.rng.randint(0, 23),
                )
            else:
                ts = utcnow() - timedelta(days=self.rng.randint(1, 90))
            self.transactions.append(
                SynthTransaction(
                    id=f"TXN_{len(self.transactions)+1:05d}",
                    src_account=src_acc.id,
                    dst_account=dst_acc.id,
                    amount=float(amount),
                    ts=ts,
                    remarks=self.rng.choice(["cash", "neft", "imps", "rtgs", "transfer", ""]),
                )
            )

    # ------------------------------------------------------------ documents

    def _vary_name(self, person: SynthPerson) -> str:
        """Produce a name variation for entity resolution."""
        if self.rng.random() > self.opts.name_variation_rate:
            return person.canonical_name
        parts = person.canonical_name.split()
        if len(parts) < 2:
            return person.canonical_name
        first, last = parts[0], parts[-1]
        initial = first[0] + "."
        choice = self.rng.choice([
            f"{first} {last[0]}.",
            f"{initial} {last}",
            f"{first.upper()} {last.upper()}",
            f"{first} {last} ",           # trailing whitespace
            f"{first} {last}",             # same
            f"{first[0]} {last}",
        ])
        return choice

    def _format_phone(self, phone_number: str) -> str:
        """Introduce occasional partial/abbreviated formatting."""
        if self.rng.random() < 0.15:
            return phone_number[-10:]  # drop +91
        if self.rng.random() < 0.1:
            return phone_number.replace("+91", "")
        return phone_number

    def _firm(self, name: str) -> str:
        return name if self.rng.random() > self.opts.missing_field_rate / 2 else ""

    def _documents(self) -> None:
        """Generate a set of document records (FIR/CDR/FINANCIAL/SURVEILLANCE).

        These are rendered as text/CSV content strings that the existing
        document adapters will parse.  Duplicates are emitted at
        ``duplicate_rate`` to exercise de-duplication.
        """
        phone_by_id = {p.id: p for p in self.phones}
        vehicle_by_id = {v.id: v for v in self.vehicles}
        account_by_id = {a.id: a for a in self.accounts}
        location_by_id = {l.id: l for l in self.locations}
        person_by_id = {p.id: p for p in self.persons}

        doc_id_counter = 0

        def new_doc_id() -> str:
            nonlocal doc_id_counter
            doc_id_counter += 1
            return f"SYN-DOC-{doc_id_counter:04d}"

        # One FIR per case
        for c in self.cases:
            lines: list[str] = []
            lines.append("FIRST INFORMATION REPORT")
            lines.append(f"Police Station: {c.district}")
            lines.append(
                f"FIR No. {c.case_number}, dated {c.incident_date.strftime('%d/%m/%Y')}."
                f" Sections {', '.join(c.sections)} IPC."
            )
            # complainant — pick a person not in the accused list, fall back to any
            accused = [person_by_id[pid] for pid in c.person_ids if pid in person_by_id]
            complainant_name = "Complainant, resident of " + (
                location_by_id[c.location_ids[0]].address
                if c.location_ids and c.location_ids[0] in location_by_id
                else c.district
            )
            lines.append(f"Complainant: {complainant_name}.")
            # accused
            if accused:
                principal = accused[0]
                lines.append(
                    f"Accused {self._vary_name(principal)} alias {principal.canonical_name.split()[0]},"
                )
                if principal.father_or_spouse_name and self.rng.random() > self.opts.missing_field_rate:
                    lines.append(
                        f" {principal.father_or_spouse_label} {principal.father_or_spouse_name},"
                    )
                for ext in accused[1 : min(3, len(accused))]:
                    lines.append(
                        f" along with his associate {self._vary_name(ext)},"
                    )
                # phone
                if principal.phone_ids:
                    ph = phone_by_id.get(principal.phone_ids[0])
                    if ph is not None:
                        lines.append(
                            f" used mobile number {self._format_phone(ph.number)}"
                        )
                # vehicle
                if principal.vehicle_ids:
                    v = vehicle_by_id.get(principal.vehicle_ids[0])
                    if v is not None:
                        lines.append(f" and vehicle {v.plate}.")
                else:
                    lines.append(".")
                # account
                if principal.account_ids:
                    a = account_by_id.get(principal.account_ids[0])
                    if a is not None and self.rng.random() > self.opts.missing_field_rate:
                        lines.append(
                            f" The amount was to be transferred to account {a.number} (IFSC {a.ifsc})."
                        )
            lines.append(f"Recorded by Inspector {self.rng.choice(GIVEN_NAMES_M)} {self.rng.choice(SURNAMES)}.")
            content = "\n".join(l for l in lines if l is not None)
            self.documents.append({
                "doc_id": new_doc_id(),
                "case": c,
                "document_type": DocumentType.FIR,
                "filename": f"{c.case_number.replace('/', '-')}-FIR.txt",
                "content_type": "text/plain",
                "content": content,
                "language": "en",
            })

        # CDR document per case — CSV
        for c in self.cases:
            case_phones = [phone_by_id[pid] for pid in c.phone_ids if pid in phone_by_id]
            lines = ["calling_number,called_number,timestamp,duration_seconds,direction,imei"]
            for call in self.calls:
                if call.src_phone in [p.id for p in case_phones] or call.dst_phone in [p.id for p in case_phones]:
                    src_p = phone_by_id[call.src_phone]
                    dst_p = phone_by_id[call.dst_phone]
                    lines.append(
                        f"{self._format_phone(src_p.number)},"
                        f"{self._format_phone(dst_p.number)},"
                        f"{call.ts.isoformat()},"
                        f"{call.duration_s},"
                        f"{'OUTGOING' if src_p in case_phones else 'INCOMING'},"
                        f"355710{self.rng.randint(0, 999999):06d}"
                    )
                if len(lines) > 120:
                    break
            content = "\n".join(lines)
            self.documents.append({
                "doc_id": new_doc_id(),
                "case": c,
                "document_type": DocumentType.CDR,
                "filename": f"{c.case_number.replace('/', '-')}-CDR.csv",
                "content_type": "text/csv",
                "content": content,
                "language": "en",
            })

        # Bank / financial document per case
        for c in self.cases:
            case_accounts = {aid for aid in c.account_ids}
            lines = ["txn_id,date,from_account,to_account,amount,ifsc,remarks"]
            tx_count = 0
            for t in self.transactions:
                if t.src_account in case_accounts or t.dst_account in case_accounts:
                    src_acc = account_by_id.get(t.src_account)
                    dst_acc = account_by_id.get(t.dst_account)
                    if not src_acc or not dst_acc:
                        continue
                    lines.append(
                        f"{t.id},{t.ts.strftime('%Y-%m-%d')},"
                        f"{src_acc.number},{dst_acc.number},"
                        f"{t.amount:.2f},{src_acc.ifsc},{t.remarks}"
                    )
                    tx_count += 1
                    if tx_count > 80:
                        break
            if tx_count == 0:
                continue
            content = "\n".join(lines)
            self.documents.append({
                "doc_id": new_doc_id(),
                "case": c,
                "document_type": DocumentType.FINANCIAL,
                "filename": f"{c.case_number.replace('/', '-')}-BANK.csv",
                "content_type": "text/csv",
                "content": content,
                "language": "en",
            })

        # Surveillance document for some cases — vehicle/location sightings
        for c in self.cases[: max(1, len(self.cases) // 2)]:
            lines = ["date,vehicle,location,observer"]
            case_vehicles = [vehicle_by_id[vid] for vid in c.vehicle_ids if vid in vehicle_by_id]
            case_locations = [location_by_id[lid] for lid in c.location_ids if lid in location_by_id]
            if not case_vehicles or not case_locations:
                continue
            n_sightings = self.rng.randint(3, 10)
            for _ in range(n_sightings):
                v = self.rng.choice(case_vehicles)
                loc = self.rng.choice(case_locations)
                ts = c.incident_date + timedelta(days=self.rng.randint(-15, 5))
                lines.append(
                    f"{ts.strftime('%Y-%m-%d %H:%M')},{v.plate},"
                    f"{loc.address} {loc.district},SI-{self.rng.randint(10,99)}"
                )
            content = "\n".join(lines)
            self.documents.append({
                "doc_id": new_doc_id(),
                "case": c,
                "document_type": DocumentType.SURVEILLANCE,
                "filename": f"{c.case_number.replace('/', '-')}-SURVEILLANCE.csv",
                "content_type": "text/csv",
                "content": content,
                "language": "en",
            })

        # Add intentional duplicates (duplicate uploads should be rejected by
        # UNIQUE(case_id, content_hash), but the synthetic content is trivially
        # different so we duplicate a document record with slightly different
        # filename to simulate multi-source reporting).
        dup_count = int(len(self.documents) * self.opts.duplicate_rate)
        for d in self.rng.sample(self.documents, k=min(dup_count, len(self.documents))):
            dup = dict(d)
            dup["doc_id"] = new_doc_id()
            dup["filename"] = d["filename"].replace(".", "-DUP.")
            self.documents.append(dup)

        # Truncate / corrupt to requested doc count
        if len(self.documents) > self.opts.document_count:
            self.documents = self.documents[: self.opts.document_count]

    # ----------------------------------------------------------- ground truth

    def _build_ground_truth(self) -> None:
        # intended scenarios (the analytics *should* discover these if wired up)
        scenarios: list[dict] = []
        # 1) burner phones
        burners = [p.id for p in self.phones if p.is_burner]
        if burners:
            scenarios.append({
                "type": "burner_phone",
                "phone_ids": burners,
                "description": "Short-lifespan, high-fanout phones used near incident dates.",
            })
        # 2) bridges
        bridges = [p.id for p in self.persons if p.is_bridge]
        if bridges:
            scenarios.append({
                "type": "bridge_individual",
                "person_ids": bridges,
                "description": "People appearing in multiple otherwise separate networks.",
            })
        # 3) structuring candidate — chains of sub-50k transfers
        scenarios.append({
            "type": "structuring",
            "description": "Clusters of sub-50,000 INR transfers moving through shared accounts.",
        })
        # 4) rapid movement / vehicle-location pattern
        scenarios.append({
            "type": "vehicle_location",
            "description": "Vehicles repeatedly observed at the same locations near incident dates.",
        })
        # 5) cross-case person (bridges already capture this)
        # 6) identity-resolution ambiguity — name variations
        scenarios.append({
            "type": "identity_resolution",
            "description": "Intentional name variations across FIR/documents create ER proposals.",
        })
        # 7) financial mule pattern
        scenarios.append({
            "type": "financial_mule",
            "description": "Accounts receiving from many sources then forwarding to single targets.",
        })
        # 8) noisy relationships — people sharing phones/vehicles over time
        scenarios.append({
            "type": "noise",
            "description": "Phones/vehicles shared across members create low-confidence indirect links.",
        })
        self.ground_truth.update({
            "version": self.opts.version,
            "seed": self.opts.seed,
            "generated_at": utcnow().isoformat(),
            "counts": {
                "persons": len(self.persons),
                "phones": len(self.phones),
                "vehicles": len(self.vehicles),
                "locations": len(self.locations),
                "accounts": len(self.accounts),
                "organizations": len(self.organizations),
                "cases": len(self.cases),
                "calls": len(self.calls),
                "transactions": len(self.transactions),
                "documents": len(self.documents),
            },
            "scenarios": scenarios,
            "persons": [
                {
                    "id": p.id,
                    "canonical_name": p.canonical_name,
                    "is_bridge": p.is_bridge,
                    "network": p.network,
                    "phone_ids": p.phone_ids,
                    "vehicle_ids": p.vehicle_ids,
                    "account_ids": p.account_ids,
                }
                for p in self.persons
            ],
        })


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def _ensure_cases(corpus: SyntheticCorpus) -> dict[str, str]:
    """Write the synthetic cases into PostgreSQL if they don't exist yet.

    Returns a mapping from corpus case.id to database case.id.
    """
    from app.db.models import Case as DBCase

    created = {}
    async with async_session() as session:
        from sqlalchemy import select

        for c in corpus.cases:
            existing = (
                (await session.execute(select(DBCase).where(DBCase.case_number == c.case_number)))
                .scalar_one_or_none()
            )
            if existing is not None:
                created[c.id] = existing.id
                continue
            db_case = DBCase(
                id=new_uuid(),
                case_number=c.case_number,
                title=f"[SYNTHETIC] {c.title}",
                jurisdiction_id=f"{c.state_code}-{c.district[:3].upper()}",
                status=CaseStatus.OPEN,
            )
            session.add(db_case)
            await session.flush()
            created[c.id] = db_case.id
    return created


async def _ensure_system_user() -> str:
    """Create an internal ADMIN user that synthetic-corpus actions are attributed to."""
    from sqlalchemy import select
    from app.db.models import User
    from app.security.passwords import hash_password

    badge = "SYN-0000"
    async with async_session() as session:
        existing = (
            (await session.execute(select(User).where(User.badge_number == badge)))
            .scalar_one_or_none()
        )
        if existing is not None:
            return existing.id
        user = User(
            id=new_uuid(),
            badge_number=badge,
            full_name="Synthetic Corpus (system)",
            hashed_password=hash_password("synthetic-corpus-system-password-DO-NOT-USE-314"),
            role=Role.ADMIN,
            station_id="SYNTHETIC",
            jurisdiction_id="SYN-DEV",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _ingest_documents(corpus: SyntheticCorpus, case_id_map: dict[str, str], user_id: str) -> int:
    """Write each synthetic document through the existing ``upload_document`` service.

    Re-using the service keeps synthetic corpus ingestion *byte-for-byte identical*
    to a real investigator upload (object-store-first, metadata row, broker
    dispatch, UNIQUE(case_id, content_hash) de-duplication).
    """
    from app.container import get_container
    from app.db.models import User
    from app.security.deps import Principal
    from app.services import documents as doc_service

    container = get_container()
    ingested = 0
    skipped = 0
    async with async_session() as session:
        system_user = await session.get(User, user_id)
        if system_user is None:
            raise RuntimeError("Synthetic system user not found")
        principal = Principal(system_user)
        for d in corpus.documents:
            case = d["case"]
            db_case_id = case_id_map[case.id]
            case_obj = await session.get(DBCase, db_case_id)
            if case_obj is None:
                continue
            payload = d["content"].encode("utf-8") if isinstance(d["content"], str) else d["content"]
            try:
                await doc_service.upload_document(
                    session,
                    container=container,
                    case=case_obj,
                    principal=principal,
                    filename=d["filename"],
                    payload=payload,
                    document_type=d["document_type"],
                    source_confidence=SourceConfidence.UNVERIFIED,
                    mime_type=d["content_type"],
                    language_hint=d.get("language", "en"),
                )
                ingested += 1
            except Exception as exc:  # expected: duplicates/already-exists
                skipped += 1
                log.debug("synthetic.doc_skipped", doc=d["doc_id"], error=str(exc))
                await session.rollback()
    log.info("synthetic.ingest_result", ingested=ingested, skipped=skipped)
    return ingested


async def generate_corpus(opts: CorpusOptions | None = None, *,
                          safety_confirmed: bool = False) -> dict[str, Any]:
    """Generate and ingest the synthetic corpus.

    Safety: refuses to run against production DSNs unless ``safety_confirmed``
    is true.  In embedded profile (the default) it always runs.
    """
    from app.container import get_container
    settings = get_settings()
    opts = opts or CorpusOptions.from_settings(settings)
    if settings.environment == "production" and not safety_confirmed:
        raise RuntimeError(
            "Refusing to generate synthetic corpus against a production environment. "
            "Pass --yes-i-am-sure (safety_confirmed=True) if you really want to proceed."
        )
    configure_logging(settings.log_level, json_logs=False)
    log.info("synthetic.generating", seed=opts.seed, persons=opts.person_count,
             cases=opts.case_count, calls=opts.call_count)
    corpus = SyntheticCorpus(opts=opts)
    t0 = time.time()
    corpus.build()
    log.info("synthetic.built", persons=len(corpus.persons), cases=len(corpus.cases),
             phones=len(corpus.phones), docs=len(corpus.documents),
             elapsed=round(time.time() - t0, 2))
    # Ensure DB is initialised before writing cases
    await init_db()
    # prime container (broker, object store)
    get_container()
    user_id = await _ensure_system_user()
    case_map = await _ensure_cases(corpus)
    ingested = await _ingest_documents(corpus, case_map, user_id)
    # write ground truth JSON (NOT in the operational DB/graph)
    gt_path = settings.data_dir / "synthetic_ground_truth.json"
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps(corpus.ground_truth, indent=2, default=str), encoding="utf-8")
    log.info("synthetic.ingested", documents=ingested, ground_truth=str(gt_path))
    return {
        "seed": opts.seed,
        "version": opts.version,
        "counts": corpus.ground_truth["counts"],
        "ingested_documents": ingested,
        "ground_truth_path": str(gt_path),
    }


async def clear_corpus(*, safety_confirmed: bool = False) -> dict[str, Any]:
    """Remove all documents/cases/entities tagged source_environment=synthetic.

    This ONLY removes synthetic-tagged rows.  It refuses to drop anything in
    a production environment unless the safety flag is set.
    """
    settings = get_settings()
    if settings.environment == "production" and not safety_confirmed:
        raise RuntimeError(
            "Refusing to clear synthetic corpus in production. Pass --yes-i-am-sure."
        )
    log.warning("synthetic.clear_requested")
    # We keep it simple in embedded profile: wipe the entire database file
    # and graph snapshot only if the environment is dev.  In production with
    # mixed data, caller must use the provenance metadata.
    removed = {"documents": 0, "cases": 0}
    async with async_session() as session:
        from sqlalchemy import delete, func, select
        from app.db.models import CaseDocument as DCD, Case as DC
        # count first
        removed["documents"] = int(
            (await session.execute(select(func.count(DCD.id)))).scalar() or 0
        )
        removed["cases"] = int(
            (await session.execute(select(func.count(DC.id)))).scalar() or 0
        )
        await session.execute(delete(DCD))
        await session.execute(delete(DC))
        await session.commit()
    # also reset the embedded graph store
    from app.container import get_container
    container = get_container()
    if hasattr(container.graph_store, "reset"):
        container.graph_store.reset()
    elif settings.effective_graph_backend == "embedded":
        p = settings.graph_snapshot_path
        if p.exists():
            p.unlink()
    log.warning("synthetic.cleared", **removed)
    return removed


async def get_corpus_stats() -> dict[str, Any]:
    """Return live counts from PostgreSQL and the graph store for the admin UI."""
    from sqlalchemy import func, select, text
    from app.db.models import (
        AuditChainHead,
        Case,
        CaseDocument,
        DetectedPattern,
        EntityResolutionItem,
        User,
    )

    stats: dict[str, Any] = {"postgres": {}, "graph": {}, "infra": {}}
    async with async_session() as session:
        stats["postgres"] = {
            "users": int((await session.execute(select(func.count(User.id)))).scalar() or 0),
            "cases": int((await session.execute(select(func.count(Case.id)))).scalar() or 0),
            "documents": int(
                (await session.execute(select(func.count(CaseDocument.id)))).scalar() or 0
            ),
            "pending_resolutions": int(
                (await session.execute(
                    select(func.count(EntityResolutionItem.id)).where(
                        EntityResolutionItem.status == "PENDING"
                    )
                )).scalar() or 0
            ),
            "new_patterns": int(
                (await session.execute(
                    select(func.count(DetectedPattern.id)).where(
                        DetectedPattern.status == "NEW"
                    )
                )).scalar() or 0
            ),
        }
    from app.container import get_container
    container = get_container()
    try:
        stats["graph"] = container.graph_store.stats()
    except Exception as exc:  # pragma: no cover
        stats["graph"] = {"error": str(exc)}
    # infra ping
    stats["infra"] = {
        "profile": get_settings().profile,
        "relational_backend": get_settings().effective_relational_backend,
        "graph_backend": get_settings().effective_graph_backend,
        "object_store_backend": get_settings().effective_object_store_backend,
        "broker_backend": get_settings().effective_broker_backend,
        "nlp_provider": container.nlp.name,
        "broker_alive": True,
        "ai_extraction_available": get_settings().ai_role_available("extraction"),
        "ai_reasoning_available": get_settings().ai_role_available("reasoning"),
    }
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(stats: dict) -> None:
    print("\n=== CrimeLink Database Summary ===")
    print("PostgreSQL (relational store):")
    for k, v in stats.get("postgres", {}).items():
        print(f"  {k:22s} {v}")
    print("Graph store:")
    for k, v in stats.get("graph", {}).items():
        if isinstance(v, dict):
            print(f"  {k:22s} {v}")
        else:
            print(f"  {k:22s} {v}")
    print("Infrastructure:")
    for k, v in stats.get("infra", {}).items():
        print(f"  {k:22s} {v}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CrimeLink synthetic corpus generator")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic RNG seed")
    parser.add_argument("--persons", type=int, default=None)
    parser.add_argument("--cases", type=int, default=None)
    parser.add_argument("--clear", action="store_true", help="Clear all data first (DEV ONLY)")
    parser.add_argument("--regenerate", action="store_true", help="Clear then regenerate")
    parser.add_argument("--stats", action="store_true", help="Show current DB counts and exit")
    parser.add_argument("--yes-i-am-sure", action="store_true",
                        help="Safety override for non-dev environments")
    args = parser.parse_args(argv)

    # Prime settings (env vars loaded) before constructing options
    settings = get_settings()
    opts = CorpusOptions.from_settings(settings)
    if args.seed is not None:
        opts.seed = args.seed
    if args.persons is not None:
        opts.person_count = args.persons
    if args.cases is not None:
        opts.case_count = args.cases

    async def _run() -> dict:
        await init_db()
        if args.stats:
            return await get_corpus_stats()
        if args.clear or args.regenerate:
            await clear_corpus(safety_confirmed=args.yes_i_am_sure)
            if args.clear and not args.regenerate:
                return {"cleared": True}
        return await generate_corpus(opts, safety_confirmed=args.yes_i_am_sure)

    try:
        result = asyncio.run(_run())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.stats:
        _print_summary(result)
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
