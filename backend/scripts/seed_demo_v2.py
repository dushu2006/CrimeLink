"""
DEMO-DATASET-002 — Comprehensive interconnected synthetic dataset for CrimeLink.

Deterministic, idempotent, development-only seed.
Creates ~25 cases, 120 people, phones, addresses, vehicles, organizations,
bank accounts, transactions, communications, events, surveillance, intelligence,
300+ evidence documents, sources, findings, and a dense relationship graph.

Every document-type evidence record has a real corresponding object-store file.
The graph is built from relational data (not independent).

DO NOT run in production.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdf_canvas
from faker import Faker

from app.config import get_settings, reload_settings
from app.db.base import utcnow
from app.db.models import (
    AuditAnchor,
    AuditChainHead,
    Base,
    Case,
    CaseDocument,
    Dataset,
    DatasetFile,
    DetectedPattern,
    EvidenceCustodyEvent,
    InvestigationFinding,
    InvestigationSession,
    InvestigationStageRun,
    InvestigationTask,
    SourceReference,
    User,
)
from app.db.session import (
    dispose_engines,
    get_sync_engine,
    get_sync_sessionmaker,
)
from app.domain.enums import (
    CaseStatus,
    DocumentType,
    InformationClassification,
    IngestionStatus,
    PatternStatus,
    PatternType,
    Role,
    SourceConfidence,
    TaskPriority,
    TaskStatus,
)
from app.domain.models import GraphEdge, GraphNode
from app.security.passwords import hash_password

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMO_DATASET_ID = "demo-dataset-002"
DATASET_NAME = "CrimeLink Demo Dataset v2"

# Deterministic ID prefixes
CASE_PREFIX = "case-d2-"
USER_PREFIX = "demo-user-"
DOC_PREFIX = "doc-d2-"
SOURCE_DOC_PREFIX = "doc-s2-"
EVID_PREFIX = "ev-d2-"
SRC_PREFIX = "src-d2-"
FINDING_PREFIX = "find-d2-"
SESSION_PREFIX = "sess-d2-"
TASK_PREFIX = "task-d2-"
PATTERN_PREFIX = "pat-d2-"
STAGE_RUN_PREFIX = "stage-d2-"

# Demo users
DEMO_USERS = [
    {
        "id": USER_PREFIX + "admin",
        "badge_number": "DEMO-ADMIN",
        "full_name": "Demo Admin",
        "password": "DemoAdmin@2026",
        "role": Role.ADMIN,
        "station_id": "STATION-01",
        "jurisdiction_id": "METRO-CENTRAL",
    },
    {
        "id": USER_PREFIX + "inv",
        "badge_number": "DEMO-INVESTIGATOR",
        "full_name": "Inspector Priya Sharma",
        "password": "DemoInvestigator@2026",
        "role": Role.INVESTIGATOR,
        "station_id": "STATION-01",
        "jurisdiction_id": "METRO-CENTRAL",
    },
    {
        "id": USER_PREFIX + "viewer",
        "badge_number": "DEMO-VIEWER",
        "full_name": "Demo Viewer",
        "password": "DemoViewer@2026",
        "role": Role.VIEWER,
        "station_id": "STATION-01",
        "jurisdiction_id": "METRO-CENTRAL",
    },
]

JURISDICTION = "METRO-CENTRAL"
# Note: All demo cases are assigned to METRO-CENTRAL so that the demo admin
# (jursidiction METRO-CENTRAL) can see everything. Cross-jurisdiction requests
# remain fully functional — an investigator from METRO-NORTH would be scoped
# correctly.
JURISDICTIONS = ["METRO-CENTRAL", "METRO-CENTRAL", "METRO-CENTRAL", "METRO-CENTRAL", "METRO-CENTRAL"]

# ---------------------------------------------------------------------------
# Deterministic fake data
# ---------------------------------------------------------------------------

fake = Faker()
Faker.seed(20260916)
fake.seed_instance(20260916)


def _stable_uuid(namespace: str, key: str) -> str:
    """Deterministic UUIDv5 from namespace+key."""
    ns = uuid.uuid5(uuid.NAMESPACE_DNS, namespace)
    return str(uuid.uuid5(ns, key))


# Build entity IDs deterministically
def person_id(idx: int) -> str:
    return _stable_uuid("person", f"P{idx:04d}")


def phone_id(idx: int) -> str:
    return _stable_uuid("phone", f"PH{idx:04d}")


def vehicle_id(idx: int) -> str:
    return _stable_uuid("vehicle", f"V{idx:04d}")


def address_id(idx: int) -> str:
    return _stable_uuid("address", f"A{idx:04d}")


def org_id(idx: int) -> str:
    return _stable_uuid("org", f"O{idx:04d}")


def account_id(idx: int) -> str:
    return _stable_uuid("account", f"AC{idx:04d}")


def event_id(idx: int) -> str:
    return _stable_uuid("event", f"EV{idx:04d}")


def evidence_id(idx: int) -> str:
    return f"E-{idx:04d}"


def source_id(idx: int) -> str:
    return f"S-{idx:04d}"


def finding_id(idx: int) -> str:
    return f"INV-{idx:04d}"


# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------

CASE_COUNT = 25
PERSON_COUNT = 120
PHONE_COUNT = 90
VEHICLE_COUNT = 40
ADDRESS_COUNT = 50
ORG_COUNT = 15
ACCOUNT_COUNT = 35
EVENT_COUNT = 200
EVIDENCE_COUNT = 320
SOURCE_COUNT = 40
FINDING_COUNT = 30
COMMUNICATION_COUNT = 400
TRANSACTION_COUNT = 250

CASE_NUMBERS = [f"CR-{2001 + i}" for i in range(CASE_COUNT)]

# Indian-style name generation for realism (CrimeLink appears India-oriented)
FIRST_NAMES_M = [
    "Rajesh", "Amit", "Vikram", "Suresh", "Deepak", "Manoj", "Rahul", "Sanjay",
    "Arjun", "Karan", "Rohit", "Ajay", "Anil", "Sunil", "Prakash", "Dinesh",
    "Sachin", "Mahesh", "Ravi", "Mukesh", "Nitin", "Yogesh", "Shyam", "Harish",
    "Kamal", "Varun", "Krishna", "Ishwar", "Devendra", "Hemant", "Jagdish",
]
FIRST_NAMES_F = [
    "Priya", "Pooja", "Neha", "Anjali", "Sunita", "Meena", "Kavita", "Rekha",
    "Smriti", "Deepika", "Sneha", "Divya", "Shreya", "Aarti", "Kiran", "Anita",
    "Rina", "Lata", "Manju", "Geeta", "Rashmi", "Shilpa", "Sangeeta", "Nisha",
    "Payal", "Swati", "Radha", "Lakshmi", "Meera", "Sarita",
]
LAST_NAMES = [
    "Kumar", "Sharma", "Verma", "Singh", "Patel", "Yadav", "Gupta", "Reddy",
    "Jain", "Mehta", "Chopra", "Kapoor", "Malhotra", "Bose", "Chatterjee",
    "Desai", "Iyer", "Menon", "Pillai", "Rao", "Naidu", "Choudhary", "Thakur",
    "Pandey", "Mishra", "Tiwari", "Dubey", "Sinha", "Saxena", "Agarwal",
    "Khan", "Shaikh", "Ansari", "Hussain",
]

ALIAS_POOL = [
    "Baba", "Pandit", "Kale", "Gora", "Lambu", "Chhota", "Bada", "Bablu",
    "Pappu", "Guddu", "Sikander", "Raju Bhai", "Kala", "Dada", "Don",
    "Rocky", "Tony", "Bunty", "Chintu", "Munna", "Bullet", "Singham",
    "Bhai", "Boss", "Ustaad", "Maalik",
]

ADDRESSES = [
    ("12, Sector 17, Navi Mumbai", "RESIDENCE"),
    ("B-44, Andheri West, Mumbai", "RESIDENCE"),
    ("Flat 301, Bandra Kurla Complex", "OFFICE"),
    ("Warehouse No. 7, Wadala Truck Terminal", "WAREHOUSE"),
    ("Royal Palms, Goregaon East", "RESIDENCE"),
    ("C-15, Dadar West", "RESIDENCE"),
    ("Shop No. 22, Crawford Market", "BUSINESS"),
    ("Hotel Sea View, Juhu Tara Road", "HOTEL"),
    ("Parking Lot B, Chhatrapati Shivaji Terminus", "PARKING"),
    ("A/5, MIDC Andheri", "OFFICE"),
    ("B-102, Powai Lake View Apartments", "RESIDENCE"),
    ("Linking Road, Bandra West", "MEETING"),
    ("Metro Junction Mall, Kalyan", "LOCATION"),
    ("A-7, Saki Naka, Andheri East", "RESIDENCE"),
    ("21, Marine Drive", "RESIDENCE"),
    ("Godown No. 3, Taloja MIDC", "STORAGE"),
    ("Charni Road Garden", "MEETING"),
    ("Malad Mindspace, Building 4", "OFFICE"),
    ("LBS Marg, Kurla West", "INCIDENT_LOCATION"),
    ("Express Highway Toll Plaza, Vashi", "SURVEILLANCE_POINT"),
    ("R City Mall, Ghatkopar West", "LOCATION"),
    ("Bandra-Worli Sea Link Toll", "SURVEILLANCE_POINT"),
    ("Hotel Sahara Star, Vile Parle", "HOTEL"),
    ("Dharavi Redevelopment Site, Sector 4", "LOCATION"),
    ("Chhatrapati Shivaji Maharaj International Airport", "LOCATION"),
    ("Mumbai Central Railway Station", "LOCATION"),
    ("Byculla Jail Visiting Area", "INSTITUTION"),
    ("Axis Bank Branch, Fort Area", "FINANCIAL"),
    ("HDFC Bank, Colaba Causeway", "FINANCIAL"),
    ("ICICI Bank, Andheri Lokhandwala", "FINANCIAL"),
    ("SBI Main Branch, Ballard Estate", "FINANCIAL"),
    ("Yes Bank, BKC Branch", "FINANCIAL"),
    ("Kotak Mahindra, Lower Parel", "FINANCIAL"),
    ("Casino Paradise, Majestic Hotel", "BUSINESS"),
    ("Garage Complex, Sewri", "WORKSHOP"),
    ("A-24, Antop Hill Slum Redevelopment", "RESIDENCE"),
    ("Film City, Goregaon East", "LOCATION"),
    ("Backbay Reclamation, Nariman Point", "OFFICE"),
    ("Cuffe Parade, Tower 4", "RESIDENCE"),
    ("Pali Hill, Bandra West", "RESIDENCE"),
    ("Versova Jetty", "LOCATION"),
    ("Dockyard Road, Yellow Gate", "INCIDENT_LOCATION"),
    ("St. Xavier's College Campus", "LOCATION"),
    ("Bandra Reclamation Promenade", "MEETING"),
    ("Mahalaxmi Race Course", "LOCATION"),
    ("BMC Ward Office, H West", "INSTITUTION"),
    ("MRA Marg Police Station", "INSTITUTION"),
    ("Crime Branch Unit 3, Crawford Market", "INSTITUTION"),
    ("Forensic Science Lab, Kalina", "INSTITUTION"),
    ("Customs Warehouse, JNPT", "STORAGE"),
]

VEHICLE_DATA = [
    ("MH02AB1234", "Hyundai", "Creta", "White"), ("MH02CD5678", "Toyota", "Innova Crysta", "Silver"),
    ("MH02EF9012", "Maruti Suzuki", "Swift", "Red"), ("MH02GH3456", "Honda", "City", "Black"),
    ("MH02IJ7890", "Mahindra", "Scorpio", "Black"), ("MH02KL2345", "Tata", "Harrier", "Blue"),
    ("MH02MN6789", "Ford", "EcoSport", "Grey"), ("MH02OP0123", "Kia", "Seltos", "Red"),
    ("MH02QR4567", "Renault", "Duster", "White"), ("MH02ST8901", "Volkswagen", "Polo", "Silver"),
    ("MH02UV2345", "BMW", "3 Series", "Black"), ("MH02WX6789", "Mercedes", "C-Class", "White"),
    ("MH02YZ0123", "Audi", "A4", "Silver"), ("MH02AA4567", "Hyundai", "Verna", "Blue"),
    ("MH02BB8901", "Toyota", "Fortuner", "White"), ("MH02CC2345", "Maruti Suzuki", "Ertiga", "Silver"),
    ("MH02DD6789", "Honda", "Civic", "Red"), ("MH02EE0123", "Mahindra", "XUV700", "Black"),
    ("MH02FF4567", "Tata", "Nexon", "Blue"), ("MH02GG8901", "Suzuki", "Access 125", "Black"),
    ("MH02HH2345", "Bajaj", "Pulsar 220", "Red"), ("MH02II6789", "Royal Enfield", "Classic 350", "Black"),
    ("MH02JJ0123", "TVS", "Apache RTR", "White"), ("MH02KK4567", "Yamaha", "FZ-S", "Blue"),
    ("MH02LL8901", "Hero", "Splendor Plus", "Black"), ("MH02MM2345", "Hyundai", "i20", "Red"),
    ("MH02NN6789", "Toyota", "Glanza", "White"), ("MH02OO0123", "Maruti Suzuki", "Baleno", "Silver"),
    ("MH02PP4567", "Honda", "Activa 6G", "White"), ("MH02QQ8901", "Tata", "Tiago", "Blue"),
    ("MH02RR2345", "Mahindra", "Thar", "Red"), ("MH02SS6789", "Force Motors", "Trax Cruiser", "White"),
    ("MH02TT0123", "Isuzu", "D-Max V-Cross", "Silver"), ("MH02UU4567", "Ashok Leyland", "Partner Truck", "White"),
    ("MH02VV8901", "Piaggio", "Ape Auto", "Yellow"), ("MH02WW2345", "Volvo", "Bus", "White"),
    ("MH02XX6789", "Eicher", "Canter", "White"), ("MH02YY0123", "Tata", "Ace", "Blue"),
    ("MH02ZZ4567", "Skoda", "Octavia", "Grey"), ("MH02AAA890", "MG", "Hector", "Red"),
]

ORGANIZATION_NAMES = [
    ("Horizon Exports Pvt Ltd", "TRADING"),
    ("Om Sai Construction Co", "CONSTRUCTION"),
    ("Starlight Impex", "IMPORT_EXPORT"),
    ("Metro Distributors", "DISTRIBUTION"),
    ("Sahyadri Trading Co", "TRADING"),
    ("Bharat Logistics Ltd", "LOGISTICS"),
    ("Royal Catering Services", "CATERING"),
    ("Neelkanth Jewellers", "RETAIL"),
    ("Aegis Security Solutions", "SECURITY"),
    ("Crystal Hotel & Resorts", "HOSPITALITY"),
    ("Shree Balaji Motors", "AUTOMOTIVE"),
    ("Dhanlakshmi Finance", "FINANCIAL_SERVICES"),
    ("Unity Welfare Trust", "NGO"),
    ("Precision Engineering Works", "MANUFACTURING"),
    ("Global Exchange Bureau", "FINANCIAL_SERVICES"),
]

BANKS = [
    ("State Bank of India", "SBIN"),
    ("HDFC Bank", "HDFC"),
    ("ICICI Bank", "ICIC"),
    ("Axis Bank", "UTIB"),
    ("Kotak Mahindra Bank", "KKBK"),
    ("Yes Bank", "YESB"),
    ("Bank of Baroda", "BARB"),
    ("Punjab National Bank", "PUNB"),
    ("Bank of India", "BKID"),
    ("Canara Bank", "CNRB"),
]

CASE_TITLES = [
    ("Armed Robbery at Jewelry Store", "ROBBERY", "HIGH", "OPEN"),
    ("Organized Vehicle Theft Ring", "AUTO_THEFT", "HIGH", "ACTIVE_INVESTIGATION"),
    ("Drug Trafficking Network Bust", "NARCOTICS", "CRITICAL", "ACTIVE_INVESTIGATION"),
    ("Financial Fraud / Embezzlement Scheme", "FRAUD", "HIGH", "OPEN"),
    ("Extortion Racket Operation", "EXTORTION", "HIGH", "ACTIVE_INVESTIGATION"),
    ("Kidnapping for Ransom", "KIDNAPPING", "CRITICAL", "OPEN"),
    ("Arms Smuggling Network", "ARMS", "CRITICAL", "UNDER_REVIEW"),
    ("Cyber Financial Fraud Scam", "CYBER_FRAUD", "MEDIUM", "OPEN"),
    ("Illegal Land Grab and Forgery", "PROPERTY", "HIGH", "ACTIVE_INVESTIGATION"),
    ("Counterfeit Currency Distribution", "COUNTERFEIT", "CRITICAL", "OPEN"),
    ("Human Trafficking Investigation", "TRAFFICKING", "CRITICAL", "ACTIVE_INVESTIGATION"),
    ("Contract Killing / Murder for Hire", "HOMICIDE", "CRITICAL", "UNDER_REVIEW"),
    ("Illegal Gambling and Betting Racket", "GAMBLING", "MEDIUM", "OPEN"),
    ("Smuggling of Contraband Goods", "SMUGGLING", "HIGH", "ACTIVE_INVESTIGATION"),
    ("Insurance Fraud Conspiracy", "INSURANCE_FRAUD", "MEDIUM", "OPEN"),
    ("Criminal Intimidation and Rivalry", "GANG_ACTIVITY", "HIGH", "ACTIVE_INVESTIGATION"),
    ("ATM Theft and Skimming Ring", "THEFT", "MEDIUM", "OPEN"),
    ("Illegal Liquor Distribution", "BOOTLEGGING", "MEDIUM", "OPEN"),
    ("Wildlife Trafficking Ring", "WILDLIFE", "HIGH", "CLOSED"),
    ("Bribery and Corruption Case", "CORRUPTION", "HIGH", "UNDER_REVIEW"),
    ("Hit and Run Vehicular Homicide", "HIT_AND_RUN", "HIGH", "OPEN"),
    ("Money Laundering Operation", "MONEY_LAUNDERING", "CRITICAL", "ACTIVE_INVESTIGATION"),
    ("Fake Educational Certificates Racket", "FORGERY", "MEDIUM", "CLOSED"),
    ("Chain Snatching Operation", "THEFT", "MEDIUM", "OPEN"),
    ("Harassment and Criminal Conspiracy", "CONSPIRACY", "HIGH", "OPEN"),
]


def gen_person(idx: int) -> dict[str, Any]:
    """Generate a deterministic person."""
    is_male = (idx % 3) != 0  # ~2/3 male for realistic distribution
    first = (FIRST_NAMES_M if is_male else FIRST_NAMES_F)[idx % len(FIRST_NAMES_M if is_male else FIRST_NAMES_F)]
    last = LAST_NAMES[idx % len(LAST_NAMES)]
    full_name = f"{first} {last}"
    pid = person_id(idx)
    dob = fake.date_of_birth(minimum_age=20, maximum_age=65)
    roles = ["SUSPECT", "PERSON_OF_INTEREST", "WITNESS", "VICTIM", "ASSOCIATE", "INFORMANT"]
    role_idx = (idx * 7 + 3) % len(roles)
    # Hero: PERSON-001 is suspect in hero case (index 0)
    if idx == 0:
        role = "SUSPECT"
    elif idx in (1, 2):
        role = "ACCOMPLICE"
    elif idx in (3, 4):
        role = "VICTIM"
    elif idx < 15:
        role = "PERSON_OF_INTEREST"
    else:
        role = roles[role_idx] if roles[role_idx] not in ("SUSPECT",) else "ASSOCIATE"

    # Aliases
    alias_count = (idx % 3)
    aliases = []
    for a in range(alias_count):
        aliases.append(ALIAS_POOL[(idx + a * 3) % len(ALIAS_POOL)])

    # Phone numbers: each person has 1-2 phones
    phone_idx_start = (idx * 2) % PHONE_COUNT
    phones = [phone_id(phone_idx_start)]
    if idx % 2 == 0:
        phones.append(phone_id((phone_idx_start + 1) % PHONE_COUNT))

    # Address: primary + sometimes secondary
    addr_idx = idx % ADDRESS_COUNT
    addresses = [address_id(addr_idx)]
    if idx % 4 == 0:
        addresses.append(address_id((addr_idx + 7) % ADDRESS_COUNT))

    # Vehicle ownership: ~30%
    vehicles = []
    if idx % 3 == 0:
        vehicles.append(vehicle_id(idx % VEHICLE_COUNT))
    if idx == 1:
        vehicles.append(vehicle_id(0))  # Share hero vehicle
    if idx == 2:
        vehicles.append(vehicle_id(5))

    # Organization: ~40% members
    org = None
    if idx % 5 == 0 or idx in (0, 1, 2, 5, 8):
        org = org_id(idx % ORG_COUNT)

    # Bank accounts: ~45% have an account
    accounts = []
    if idx % 2 == 0 or idx in (0, 1, 3, 5, 7):
        accounts.append(account_id(idx % ACCOUNT_COUNT))
        if idx % 7 == 0:
            accounts.append(account_id((idx + 10) % ACCOUNT_COUNT))

    return {
        "id": pid,
        "full_name": full_name,
        "aliases": aliases,
        "dob": dob.isoformat(),
        "gender": "M" if is_male else "F",
        "role": role,
        "occupation": fake.job(),
        "phone_ids": phones,
        "address_ids": addresses,
        "vehicle_ids": vehicles,
        "org_id": org,
        "account_ids": accounts,
    }


def gen_phone(idx: int) -> dict[str, Any]:
    return {
        "id": phone_id(idx),
        "number": f"+91{9000000000 + idx * 137 % 999999999}",
        "carrier": ["Airtel", "Jio", "Vi", "BSNL"][idx % 4],
        "type": ["MOBILE", "LANDLINE", "SATELLITE"][idx % 3],
    }


def gen_vehicle(idx: int) -> dict[str, Any]:
    reg, make, model, color = VEHICLE_DATA[idx % len(VEHICLE_DATA)]
    return {
        "id": vehicle_id(idx),
        "registration": reg,
        "make": make,
        "model": model,
        "color": color,
        "type": ["CAR", "MOTORCYCLE", "SUV", "TRUCK", "COMMERCIAL"][idx % 5],
    }


def gen_address(idx: int) -> dict[str, Any]:
    addr, cat = ADDRESSES[idx % len(ADDRESSES)]
    city_areas = ["Mumbai", "Navi Mumbai", "Thane", "Kalyan"]
    return {
        "id": address_id(idx),
        "address": addr,
        "city": city_areas[idx % len(city_areas)],
        "category": cat,
    }


def gen_org(idx: int) -> dict[str, Any]:
    name, cat = ORGANIZATION_NAMES[idx % len(ORGANIZATION_NAMES)]
    addr_idx = idx % ADDRESS_COUNT
    return {
        "id": org_id(idx),
        "name": name,
        "category": cat,
        "address_id": address_id(addr_idx),
    }


def gen_account(idx: int) -> dict[str, Any]:
    bank_name, ifsc = BANKS[idx % len(BANKS)]
    acct_no = f"{50000000000 + idx * 104729 % 99999999:011d}"
    full_ifsc = f"{ifsc}0{idx % 10000:04d}"
    return {
        "id": account_id(idx),
        "bank": bank_name,
        "ifsc": full_ifsc,
        "account_number": acct_no,
    }


# ---------------------------------------------------------------------------
# Build canonical data
# ---------------------------------------------------------------------------

def build_dataset() -> dict[str, Any]:
    persons = [gen_person(i) for i in range(PERSON_COUNT)]
    phones = [gen_phone(i) for i in range(PHONE_COUNT)]
    vehicles = [gen_vehicle(i) for i in range(VEHICLE_COUNT)]
    addresses = [gen_address(i) for i in range(ADDRESS_COUNT)]
    orgs = [gen_org(i) for i in range(ORG_COUNT)]
    accounts = [gen_account(i) for i in range(ACCOUNT_COUNT)]

    # Cases
    cases = []
    base_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(CASE_COUNT):
        title, classification, priority, status = CASE_TITLES[i % len(CASE_TITLES)]
        opened = base_date + timedelta(days=i * 7 + 14)
        incident = opened - timedelta(days=2 + (i % 5))
        cases.append({
            "id": f"{CASE_PREFIX}{i:03d}",
            "case_number": CASE_NUMBERS[i],
            "title": title,
            "description": f"Investigation into {title.lower()} under jurisdiction {JURISDICTION}.",
            "classification": classification,
            "priority": priority,
            "status": status,
            "jurisdiction_id": JURISDICTIONS[i % len(JURISDICTIONS)],
            "opened_at": opened,
            "incident_date": incident,
        })

    # Assign people to cases: first N people spread across cases, with overlap
    case_persons: dict[str, list[str]] = {c["id"]: [] for c in cases}
    case_vehicles: dict[str, list[str]] = {c["id"]: [] for c in cases}
    case_addresses: dict[str, list[str]] = {c["id"]: [] for c in cases}
    case_phones: dict[str, list[str]] = {c["id"]: [] for c in cases}
    case_accounts: dict[str, list[str]] = {c["id"]: [] for c in cases}

    # Hero case (CR-2001, index 0): has PERSON-001 (idx0), PERSON-002 (idx1), PERSON-003 (idx2),
    # victims PERSON-004 (idx3), PERSON-005 (idx4), key witnesses, etc.
    hero_idx = 0
    hero_case_id = cases[hero_idx]["id"]
    hero_person_indices = list(range(0, 12))  # key cast
    for pidx in hero_person_indices:
        case_persons[hero_case_id].append(persons[pidx]["id"])
    for pidx in hero_person_indices[:5]:
        p = persons[pidx]
        for vid in p["vehicle_ids"]:
            if vid not in case_vehicles[hero_case_id]:
                case_vehicles[hero_case_id].append(vid)
        for aid in p["address_ids"]:
            if aid not in case_addresses[hero_case_id]:
                case_addresses[hero_case_id].append(aid)
        for phid in p["phone_ids"]:
            if phid not in case_phones[hero_case_id]:
                case_phones[hero_case_id].append(phid)
        for acid in p["account_ids"]:
            if acid not in case_accounts[hero_case_id]:
                case_accounts[hero_case_id].append(acid)

    # Other cases: assign 4-12 people each, with intentional cross-case overlap
    for ci in range(1, CASE_COUNT):
        cid = cases[ci]["id"]
        # Assign people; include some overlap with previous cases
        primary_indices = [ci * 4 % PERSON_COUNT, (ci * 4 + 1) % PERSON_COUNT, (ci * 4 + 2) % PERSON_COUNT, (ci * 4 + 3) % PERSON_COUNT]
        # Add 2-8 more people
        extra_count = (ci * 3) % 6 + 2
        extra = []
        for j in range(extra_count):
            idx = (ci * 7 + j * 13 + 5) % PERSON_COUNT
            if idx not in primary_indices:
                extra.append(idx)
        # Add overlap: pull 1-2 people from previous cases
        overlap_count = 1 + (ci % 3)
        for j in range(overlap_count):
            prev_ci = (ci - 1 - j) % CASE_COUNT
            prev_p = case_persons[cases[prev_ci]["id"]]
            if prev_p:
                # pick a person from previous case to cross over
                overlap_p = prev_p[j % len(prev_p)]
                if overlap_p not in primary_indices and overlap_p not in extra:
                    extra.append(next(i for i, p in enumerate(persons) if p["id"] == overlap_p))
        all_idxs = list(set(primary_indices + extra))
        for pidx in all_idxs:
            case_persons[cid].append(persons[pidx]["id"])

        # Assign vehicles/addresses/phones/accounts based on case people
        for pidx in all_idxs:
            p = persons[pidx]
            for vid in p["vehicle_ids"]:
                if vid not in case_vehicles[cid]:
                    case_vehicles[cid].append(vid)
            for aid in p["address_ids"]:
                if aid not in case_addresses[cid]:
                    case_addresses[cid].append(aid)
            for phid in p["phone_ids"]:
                if phid not in case_phones[cid]:
                    case_phones[cid].append(phid)
            for acid in p["account_ids"]:
                if acid not in case_accounts[cid]:
                    case_accounts[cid].append(acid)

    # Also assign crime locations per case
    for ci, c in enumerate(cases):
        loc_idx = (ci * 5 + 3) % ADDRESS_COUNT
        lid = address_id(loc_idx)
        if lid not in case_addresses[c["id"]]:
            case_addresses[c["id"]].append(lid)

    # Build relationships
    relationships: list[dict[str, Any]] = []

    # PERSON-PERSON: ASSOCIATE_OF between people in same case
    for c in cases:
        cid = c["id"]
        plist = case_persons[cid]
        # Ring leader connections
        if len(plist) >= 3:
            ringleader = plist[0]
            for other in plist[1:min(len(plist), 8)]:
                relationships.append({
                    "source": ringleader,
                    "target": other,
                    "rel_type": "ASSOCIATE_OF",
                    "case_id": cid,
                    "confidence": 0.90,
                    "evidence_ref": None,
                })
        # Pairwise associate connections among core members
        for i, pi in enumerate(plist[:6]):
            for pj in plist[i + 1:min(7, len(plist))]:
                if (hash(pi + pj) % 100) < 40:  # ~40% density
                    relationships.append({
                        "source": pi,
                        "target": pj,
                        "rel_type": "ASSOCIATE_OF",
                        "case_id": cid,
                        "confidence": 0.80,
                    })
        # RELATIVE_OF for some pairs
        for i in range(0, len(plist) - 1, 5):
            relationships.append({
                "source": plist[i],
                "target": plist[(i + 1) % len(plist)],
                "rel_type": "RELATIVE_OF",
                "case_id": cid,
                "confidence": 0.95,
            })

    # PERSON-PHONE: USES_PHONE
    for c in cases:
        cid = c["id"]
        for pid in case_persons[cid]:
            pidx = next(i for i, p in enumerate(persons) if p["id"] == pid)
            for phid in persons[pidx]["phone_ids"]:
                if phid in case_phones[cid] or True:
                    relationships.append({
                        "source": pid,
                        "target": phid,
                        "rel_type": "USES_PHONE",
                        "case_id": cid,
                        "confidence": 0.98,
                    })

    # PERSON-VEHICLE: OWNS_VEHICLE
    for c in cases:
        cid = c["id"]
        for pid in case_persons[cid]:
            pidx = next(i for i, p in enumerate(persons) if p["id"] == pid)
            for vid in persons[pidx]["vehicle_ids"]:
                if vid in case_vehicles[cid] or True:
                    relationships.append({
                        "source": pid,
                        "target": vid,
                        "rel_type": "OWNS_VEHICLE",
                        "case_id": cid,
                        "confidence": 0.92,
                    })

    # PERSON-ADDRESS: LOCATED_AT
    for c in cases:
        cid = c["id"]
        for pid in case_persons[cid]:
            pidx = next(i for i, p in enumerate(persons) if p["id"] == pid)
            for aid in persons[pidx]["address_ids"]:
                relationships.append({
                    "source": pid,
                    "target": aid,
                    "rel_type": "LOCATED_AT",
                    "case_id": cid,
                    "confidence": 0.88,
                })

    # PERSON-ORG: MEMBER_OF
    for c in cases:
        cid = c["id"]
        for pid in case_persons[cid]:
            pidx = next(i for i, p in enumerate(persons) if p["id"] == pid)
            org = persons[pidx]["org_id"]
            if org:
                relationships.append({
                    "source": pid,
                    "target": org,
                    "rel_type": "MEMBER_OF",
                    "case_id": cid,
                    "confidence": 0.85,
                })

    # PERSON-ACCOUNT: OWNS_ACCOUNT
    for c in cases:
        cid = c["id"]
        for pid in case_persons[cid]:
            pidx = next(i for i, p in enumerate(persons) if p["id"] == pid)
            for acid in persons[pidx]["account_ids"]:
                relationships.append({
                    "source": pid,
                    "target": acid,
                    "rel_type": "OWNS_ACCOUNT",
                    "case_id": cid,
                    "confidence": 0.90,
                })

    # PHONE-PHONE: CALLED (communications)
    communications = []
    for i in range(COMMUNICATION_COUNT):
        from_phone_idx = i % PHONE_COUNT
        # Target: mostly within same case circle
        target_offset = (i * 17 + 3) % (PHONE_COUNT - 1) + 1
        to_phone_idx = (from_phone_idx + target_offset) % PHONE_COUNT
        ts = base_date + timedelta(minutes=i * 37)
        call_count = 1 + (i % 5)
        communications.append({
            "from": phone_id(from_phone_idx),
            "to": phone_id(to_phone_idx),
            "timestamp": ts.isoformat(),
            "duration_s": 30 + (i * 23) % 600,
            "call_type": ["VOICE", "SMS", "MISSED"][i % 3],
        })
        # Add CALLED relationship (aggregate per pair)
        relationships.append({
            "source": phone_id(from_phone_idx),
            "target": phone_id(to_phone_idx),
            "rel_type": "CALLED",
            "case_id": cases[i % CASE_COUNT]["id"],
            "confidence": 0.95,
            "call_count": call_count,
            "first_ts": ts.isoformat(),
            "last_ts": (ts + timedelta(hours=1)).isoformat(),
        })

    # ACCOUNT-ACCOUNT: TRANSFER_TO (financial transactions)
    transactions = []
    for i in range(TRANSACTION_COUNT):
        from_idx = i % ACCOUNT_COUNT
        to_idx = (i * 7 + 11) % ACCOUNT_COUNT
        if from_idx == to_idx:
            to_idx = (to_idx + 1) % ACCOUNT_COUNT
        ts = base_date + timedelta(hours=i * 6 + 2)
        amount = 5000 + (i * 13451) % 495000
        if (i % 7 == 0):
            amount = 45000 + (i * 731) % 5000  # structuring pattern: just under 50k
        transactions.append({
            "from": account_id(from_idx),
            "to": account_id(to_idx),
            "timestamp": ts.isoformat(),
            "amount": amount,
            "txn_type": ["NEFT", "RTGS", "IMPS", "UPI", "CASH"][i % 5],
            "reference": f"TXN{i:06d}",
        })
        relationships.append({
            "source": account_id(from_idx),
            "target": account_id(to_idx),
            "rel_type": "TRANSFER_TO",
            "case_id": cases[i % CASE_COUNT]["id"],
            "confidence": 0.97,
            "amount": amount,
            "ts": ts.isoformat(),
        })

    # Timeline events
    events = []
    for i in range(EVENT_COUNT):
        c = cases[i % CASE_COUNT]
        cid = c["id"]
        c_people = case_persons[cid]
        c_vehicles = case_vehicles[cid]
        c_addresses = case_addresses[cid]
        event_types = [
            ("INCIDENT", "Reported incident"),
            ("CALL", "Communication event"),
            ("MEETING", "Observed meeting"),
            ("TRANSACTION", "Financial transaction"),
            ("VEHICLE_SIGHTING", "Vehicle sighted"),
            ("SURVEILLANCE", "Surveillance observation"),
            ("EVIDENCE_COLLECTION", "Evidence collected"),
            ("INTERVIEW", "Witness interview"),
            ("ARREST", "Subject arrested"),
            ("SEARCH", "Premises searched"),
            ("TRAVEL", "Travel movement"),
        ]
        etype_idx = i % len(event_types)
        etype, edesc = event_types[etype_idx]
        ts = c["incident_date"] + timedelta(hours=i * 4)
        participants = []
        for j in range(min(3, len(c_people))):
            participants.append(c_people[(i + j) % len(c_people)])
        location = c_addresses[i % len(c_addresses)] if c_addresses else None
        events.append({
            "id": event_id(i),
            "event_type": etype,
            "title": f"{edesc} — {c['case_number']}",
            "description": f"{edesc} related to case {c['case_number']} ({c['title']}).",
            "timestamp": ts.isoformat(),
            "case_id": cid,
            "participants": participants,
            "location_id": location,
        })
        # Add PARTICIPATED_IN edges
        for pid in participants:
            relationships.append({
                "source": pid,
                "target": event_id(i),
                "rel_type": "PARTICIPATED_IN",
                "case_id": cid,
                "confidence": 0.85,
            })

    # Evidence documents — deterministic mapping
    evidence_list: list[dict[str, Any]] = []
    sources_list: list[dict[str, Any]] = []
    # Map evidence to case/people
    evidence_idx = 0
    source_idx = 0

    # Create evidence for each case: mix of document types
    doc_categories = [
        (DocumentType.FIR, "First Information Report", "application/pdf", True),
        (DocumentType.CDR, "Call Detail Record", "text/csv", False),
        (DocumentType.SURVEILLANCE_REPORT, "Surveillance Report", "application/pdf", True),
        (DocumentType.FINANCIAL, "Financial Statement", "text/csv", False),
        (DocumentType.WITNESS_STATEMENT, "Witness Statement", "application/pdf", True),
        (DocumentType.SCENE_REPORT, "Scene of Crime Report", "application/pdf", True),
        (DocumentType.INTELLIGENCE_REPORT, "Intelligence Report", "application/pdf", True),
        (DocumentType.ANPR, "ANPR Vehicle Sighting Log", "text/csv", False),
        (DocumentType.ARREST_RECORD, "Arrest Memo", "application/pdf", True),
        (DocumentType.FORENSIC, "Forensic Analysis Report", "application/pdf", True),
        (DocumentType.PATROL_REPORT, "Patrol Officer Report", "application/pdf", True),
        (DocumentType.CCTV, "CCTV Footage Log", "text/csv", False),
        (DocumentType.BAIL_RECORD, "Bail Order Document", "application/pdf", True),
        (DocumentType.LEGAL_RECORD, "Legal Proceedings Memo", "application/pdf", True),
        (DocumentType.CASE_DIARY, "Case Diary Entry", "application/pdf", True),
    ]

    ev_per_case = EVIDENCE_COUNT // CASE_COUNT
    for ci, c in enumerate(cases):
        cid = c["id"]
        for ei in range(ev_per_case):
            cat = doc_categories[(ci + ei) % len(doc_categories)]
            doc_type, doc_label, mime, is_pdf = cat
            eid = evidence_id(evidence_idx)
            evidence_idx += 1
            ext = "pdf" if is_pdf else "csv"
            filename = f"{c['case_number']}_{doc_type.value}_{ei + 1:02d}.{ext}"
            storage_key = f"evidence/{c['case_number']}/{filename}"
            evidence_list.append({
                "evidence_id": eid,
                "case_id": cid,
                "doc_type": doc_type,
                "filename": filename,
                "storage_key": storage_key,
                "mime_type": mime,
                "title": f"{doc_label} — {c['case_number']} ({ei + 1})",
                "is_pdf": is_pdf,
                "doc_label": doc_label,
            })

        # Also create source documents for each case (S-xxxx)
        src_types = [
            (DocumentType.INTELLIGENCE_REPORT, "Source Intelligence", "application/pdf"),
            (DocumentType.SURVEILLANCE, "Confidential Source Report", "application/pdf"),
        ]
        if ci < SOURCE_COUNT // CASE_COUNT + 1 and source_idx < SOURCE_COUNT:
            stype, slabel, smime = src_types[ci % len(src_types)]
            sid = source_id(source_idx)
            source_idx += 1
            sfilename = f"SOURCE-{sid[-4:]}.pdf"
            skey = f"sources/{c['case_number']}/{sfilename}"
            sources_list.append({
                "source_id": sid,
                "case_id": cid,
                "doc_type": stype,
                "filename": sfilename,
                "storage_key": skey,
                "mime_type": smime,
                "title": f"{slabel} for {c['case_number']}",
                "is_pdf": True,
            })

    # Top up evidence to EVIDENCE_COUNT exactly
    while evidence_idx < EVIDENCE_COUNT:
        ci = evidence_idx % CASE_COUNT
        c = cases[ci]
        cid = c["id"]
        cat = doc_categories[evidence_idx % len(doc_categories)]
        doc_type, doc_label, mime, is_pdf = cat
        eid = evidence_id(evidence_idx)
        evidence_idx += 1
        ext = "pdf" if is_pdf else "csv"
        filename = f"{c['case_number']}_{doc_type.value}_{evidence_idx:03d}.{ext}"
        storage_key = f"evidence/{c['case_number']}/{filename}"
        evidence_list.append({
            "evidence_id": eid,
            "case_id": cid,
            "doc_type": doc_type,
            "filename": filename,
            "storage_key": storage_key,
            "mime_type": mime,
            "title": f"{doc_label} — {c['case_number']}",
            "is_pdf": is_pdf,
            "doc_label": doc_label,
        })

    # Top up sources to SOURCE_COUNT
    while source_idx < SOURCE_COUNT:
        ci = source_idx % CASE_COUNT
        c = cases[ci]
        cid = c["id"]
        stype = DocumentType.INTELLIGENCE_REPORT
        sid = source_id(source_idx)
        source_idx += 1
        sfilename = f"SOURCE-{sid[-4:]}.pdf"
        skey = f"sources/{c['case_number']}/{sfilename}"
        sources_list.append({
            "source_id": sid,
            "case_id": cid,
            "doc_type": stype,
            "filename": sfilename,
            "storage_key": skey,
            "mime_type": "application/pdf",
            "title": f"Additional Source Intelligence for {c['case_number']}",
            "is_pdf": True,
        })

    # Findings
    findings = []
    finding_idx = 0

    # Hero finding: the main investigation chain for CR-2001
    hero_ev_ids = [evidence_list[i]["evidence_id"] for i in range(8)]
    hero_keys = [persons[0]["id"], persons[1]["id"], persons[2]["id"],
                 persons[0]["phone_ids"][0], persons[1]["phone_ids"][0],
                 persons[0]["vehicle_ids"][0] if persons[0]["vehicle_ids"] else vehicles[0]["id"],
                 addresses[0]["id"]]
    findings.append({
        "id": finding_id(0),
        "case_id": cases[0]["id"],
        "finding_type": "NETWORK_CONVERGENCE",
        "title": "Primary Conspiracy Link: Rajesh Kumar Coordinates Criminal Network",
        "narrative": (
            "Multiple independent evidence sources converge indicating that Rajesh Kumar (PERSON-001) "
            "is the central coordinator of the criminal network responsible for the armed robbery "
            "at Neelkanth Jewellers (CR-2001). Call data records establish direct communication "
            "between Kumar and both known accomplices (Amit Sharma, Vikram Singh) in the 72 hours "
            "preceding the incident. Vehicle MH02AB1234, registered to Kumar, was captured on ANPR "
            "cameras at three locations along the escape route. Financial analysis reveals large "
            "cash deposits into Kumar's accounts in the days following the robbery, followed by "
            "structured transfers to accounts controlled by associates."
        ),
        "confidence": 0.92,
        "confidence_band": "HIGH",
        "entity_keys": hero_keys,
        "evidence": hero_ev_ids[:6],
        "details": {
            "subject": persons[0]["full_name"],
            "evidence_strength": "MULTI_SOURCE_CONVERGENCE",
            "classification": "OPERATIONAL",
            "connection_path": [
                "CASE:CR-2001", f"PERSON:{persons[0]['id']}", f"PERSON:{persons[1]['id']}",
                "CALL:CDR", "EVIDENCE:CCTV", "VEHICLE:MH02AB1234",
                "FINANCIAL:TRANSFER", "FINDING"
            ],
            "limitations": [
                "Accomplice statements yet to be formally recorded",
                "Some burner phone communications not yet traced"
            ],
            "completed_at": cases[0]["opened_at"].isoformat(),
            "case_number": cases[0]["case_number"],
            "investigator_badge": "DEMO-INVESTIGATOR",
            "investigator_name": "Inspector Priya Sharma",
        },
        "status": "CONFIRMED",
    })
    finding_idx += 1

    # Additional findings per case
    for ci, c in enumerate(cases):
        if ci == 0:
            # Already added hero finding
            for _ in range(2):  # add a couple more for CR-2001
                fid = finding_id(finding_idx)
                finding_idx += 1
                findings.append({
                    "id": fid,
                    "case_id": c["id"],
                    "finding_type": ["COMMUNICATION_CLUSTER", "FINANCIAL_ANOMALY"][finding_idx % 2],
                    "title": f"Investigative finding #{finding_idx} for {c['case_number']}",
                    "narrative": f"Analytical finding derived from evidence review in case {c['case_number']}. Multiple sources corroborate this observation.",
                    "confidence": 0.70 + (finding_idx % 3) * 0.08,
                    "confidence_band": "MEDIUM",
                    "entity_keys": [case_persons[c["id"]][0] if case_persons[c["id"]] else persons[0]["id"]],
                    "evidence": [evidence_list[(ci * 7 + finding_idx) % len(evidence_list)]["evidence_id"]],
                    "details": {
                        "subject": c["title"],
                        "investigator_badge": "DEMO-INVESTIGATOR",
                        "investigator_name": "Inspector Priya Sharma",
                        "completed_at": c["opened_at"].isoformat(),
                        "case_number": c["case_number"],
                    },
                    "status": "NEW",
                })
            continue
        num_findings_for_case = 1 + (ci % 2)
        for _ in range(num_findings_for_case):
            if finding_idx >= FINDING_COUNT:
                break
            fid = finding_id(finding_idx)
            finding_idx += 1
            ftypes = ["CROSS_CASE_LINK", "BRIDGE_ENTITY", "COMMUNICATION_CLUSTER", "TEMPORAL_PATTERN",
                      "MULE_PATTERN", "BURNER_PATTERN", "GENERAL"]
            ftype = ftypes[finding_idx % len(ftypes)]
            c_people = case_persons[c["id"]]
            findings.append({
                "id": fid,
                "case_id": c["id"],
                "finding_type": ftype,
                "title": f"{ftype.replace('_', ' ').title()} — {c['case_number']}",
                "narrative": (
                    f"Investigation of {c['title']} ({c['case_number']}) has identified {ftype.lower().replace('_', ' ')} "
                    f"patterns supported by evidence. The convergence of {min(3, len(c_people))} persons, communication "
                    f"records, and financial documentation supports this observation."
                ),
                "confidence": 0.60 + (finding_idx % 4) * 0.10,
                "confidence_band": ["LOW", "MEDIUM", "MEDIUM", "HIGH"][finding_idx % 4],
                "entity_keys": c_people[:4] if c_people else [persons[0]["id"]],
                "evidence": [evidence_list[(ci * 13 + finding_idx) % len(evidence_list)]["evidence_id"]],
                "details": {
                    "subject": c["title"],
                    "investigator_badge": "DEMO-INVESTIGATOR",
                    "investigator_name": "Inspector Priya Sharma",
                    "completed_at": c["opened_at"].isoformat(),
                    "case_number": c["case_number"],
                },
                "status": "NEW" if finding_idx % 3 != 0 else "REVIEWED",
            })

    # DetectedPatterns
    patterns = []
    for pi in range(15):
        c = cases[pi % CASE_COUNT]
        cid = c["id"]
        ptypes = list(PatternType)
        ptype = ptypes[pi % len(ptypes)]
        patterns.append({
            "id": f"{PATTERN_PREFIX}{pi:03d}",
            "case_id": cid,
            "pattern_type": ptype,
            "confidence": 0.65 + (pi % 3) * 0.10,
            "entity_keys": case_persons[cid][:3] if case_persons[cid] else [persons[0]["id"]],
            "evidence_doc_ids": [evidence_list[pi % len(evidence_list)]["evidence_id"]],
            "explanation": f"Detected {ptype.value} pattern in case {c['case_number']} based on transactional and communication analysis.",
            "details": {"pattern_analysis": f"Analytical detection of {ptype.value}"},
            "status": PatternStatus.NEW,
        })

    # Investigation tasks (a few per case)
    tasks = []
    for ci, c in enumerate(cases[:10]):
        for ti in range(3):
            tid = f"{TASK_PREFIX}{ci:02d}{ti:02d}"
            tasks.append({
                "id": tid,
                "case_id": c["id"],
                "title": [
                    "Collect CCTV footage from surrounding cameras",
                    "Interview primary witness",
                    "Obtain call detail records for suspect phones",
                    "Forensic analysis of recovered device",
                    "Financial transaction analysis",
                    "Surveillance of suspect residence",
                    "Record suspect statement under warning",
                    "Cross-reference vehicle registration database",
                ][ti % 8],
                "description": "Investigative task assigned as part of standard investigation workflow.",
                "owner_id": DEMO_USERS[1]["id"],
                "creator_id": DEMO_USERS[1]["id"],
                "priority": [TaskPriority.MEDIUM, TaskPriority.HIGH, TaskPriority.CRITICAL][ti % 3],
                "status": [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED][(ci + ti) % 3],
                "linked_evidence": [evidence_list[(ci + ti) % len(evidence_list)]["evidence_id"]],
                "linked_entities": case_persons[c["id"]][:2] if case_persons[c["id"]] else [],
            })

    return {
        "cases": cases,
        "persons": persons,
        "phones": phones,
        "vehicles": vehicles,
        "addresses": addresses,
        "orgs": orgs,
        "accounts": accounts,
        "events": events,
        "communications": communications,
        "transactions": transactions,
        "relationships": relationships,
        "evidence": evidence_list,
        "sources": sources_list,
        "findings": findings,
        "patterns": patterns,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# Document generation helpers
# ---------------------------------------------------------------------------

def make_pdf_bytes(title: str, content_lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, title)
    c.setFont("Helvetica", 10)
    y = 690
    for line in content_lines:
        if y < 72:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = 720
        # Wrap long lines crudely
        while len(line) > 90:
            c.drawString(72, y, line[:90])
            line = line[90:]
            y -= 14
        c.drawString(72, y, line)
        y -= 14
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(72, 36, "CrimeLink Demo Dataset v2 — SYNTHETIC document. Not a real record.")
    c.save()
    return buf.getvalue()


def make_csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow([str(v) for v in row])
    # Add synthetic notice
    buf.write("\n# CrimeLink Demo Dataset v2 — SYNTHETIC data. Not real records.\n")
    return buf.getvalue().encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gen_evidence_bytes(ev: dict[str, Any], dataset: dict[str, Any]) -> bytes:
    case = next(c for c in dataset["cases"] if c["id"] == ev["case_id"])
    nonce = f"{ev['evidence_id']}|{case['case_number']}|{ev['storage_key']}"
    if ev["is_pdf"]:  # type: ignore[unreachable]
        lines = [
            f"Document Type: {ev['doc_type'].value}",
            f"Case Number: {case['case_number']}",
            f"Case Title: {case['title']}",
            f"Classification: CONFIDENTIAL",
            f"Document ID: {ev['evidence_id']}",
            f"Storage Key: {ev['storage_key']}",
            f"Nonce: {nonce}",
            "",
            f"{ev['title']}",
            "=" * 60,
            "",
            f"This document is part of case {case['case_number']} regarding {case['title']}.",
            "",
            "Investigative Notes:",
            f"- Case opened on {case['opened_at'].date().isoformat()}",
            f"- Incident occurred on {case['incident_date'].date().isoformat()}",
            f"- Jurisdiction: {case['jurisdiction_id']}",
            f"- Priority: {case['priority']}",
            "",
            "Summary of Content:",
            f"  This {ev['doc_label']} contains official records related to the investigation.",
            "  All identifying information within is synthetic and used solely for demonstration.",
            "",
            "Related Persons:",
        ]
        c_people = dataset["case_persons_dict"].get(case["id"], [])[:6]
        for pid in c_people:
            p = next(p for p in dataset["persons"] if p["id"] == pid)
            lines.append(f"  - {p['full_name']} (role: {p['role']})")
        lines += [
            "",
            "Document is provided for CrimeLink platform demonstration only.",
            "Not associated with any real person or investigation.",
        ]
        return make_pdf_bytes(ev["title"], lines)
    else:
        # CSV
        # Make content unique per evidence via nonce/seed rows
        offset = int(ev["evidence_id"].replace("E-", "")) if ev["evidence_id"].replace("E-", "").isdigit() else 0
        if ev["doc_type"] == DocumentType.CDR:
            header = ["call_id", "caller_number", "callee_number", "timestamp", "duration_s", "call_type", "cell_id", "imei", "record_nonce"]
            rows = []
            for i in range(20):
                g_idx = (offset + i) % len(dataset["communications"])
                co = dataset["communications"][g_idx]
                caller = next(p for p in dataset["phones"] if p["id"] == co["from"])
                callee = next(p for p in dataset["phones"] if p["id"] == co["to"])
                rows.append([
                    f"CDR{offset:05d}-{i:03d}", caller["number"], callee["number"],
                    co["timestamp"], co["duration_s"], co["call_type"],
                    f"CELL-{(offset + i) % 20:04d}", f"IMEI{123456789012345 + offset + i}",
                    nonce,
                ])
            return make_csv_bytes(header, rows)
        elif ev["doc_type"] == DocumentType.FINANCIAL or ev["doc_type"] == DocumentType.FINANCIAL_TRANSACTION:
            header = ["txn_id", "from_account", "to_account", "timestamp", "amount", "txn_type", "reference", "narrative", "record_nonce"]
            rows = []
            for i in range(20):
                t_idx = (offset + i) % len(dataset["transactions"])
                tx = dataset["transactions"][t_idx]
                facct = next(a for a in dataset["accounts"] if a["id"] == tx["from"])
                tacct = next(a for a in dataset["accounts"] if a["id"] == tx["to"])
                rows.append([
                    f"{tx['reference']}-{offset:04d}", facct["account_number"], tacct["account_number"],
                    tx["timestamp"], tx["amount"], tx["txn_type"], f"{tx['reference']}-{offset}-{i}",
                    "Transfer per transaction record", nonce,
                ])
            return make_csv_bytes(header, rows)
        elif ev["doc_type"] == DocumentType.ANPR:
            header = ["sighting_id", "registration", "timestamp", "location", "camera_id", "direction", "record_nonce"]
            rows = []
            for i in range(20):
                v = dataset["vehicles"][(offset + i) % len(dataset["vehicles"])]
                loc = dataset["addresses"][(offset + i) % len(dataset["addresses"])]
                ts = case["incident_date"] + timedelta(hours=(offset + i) * 2 - 120)
                rows.append([
                    f"ANPR{offset:05d}-{i:03d}", v["registration"], ts.isoformat(),
                    loc["address"], f"CAM-{(offset + i) % 10:03d}", ["INBOUND", "OUTBOUND"][(offset + i) % 2],
                    nonce,
                ])
            return make_csv_bytes(header, rows)
        elif ev["doc_type"] == DocumentType.CCTV:
            header = ["clip_id", "camera_location", "timestamp_start", "timestamp_end", "persons_observed", "vehicle_reg", "record_nonce"]
            rows = []
            for i in range(15):
                v = dataset["vehicles"][(offset + i + 3) % len(dataset["vehicles"])]
                loc = dataset["addresses"][(offset + i + 5) % len(dataset["addresses"])]
                ts = case["incident_date"] + timedelta(minutes=(offset + i) * 18 - 120)
                rows.append([
                    f"CCTV{offset:04d}-{i:03d}", loc["address"], ts.isoformat(),
                    (ts + timedelta(minutes=2)).isoformat(),
                    len(dataset["case_persons_dict"].get(case["id"], [])) if i < 5 else "unknown",
                    v["registration"] if i % 2 == 0 else "",
                    nonce,
                ])
            return make_csv_bytes(header, rows)
        else:
            # Generic CSV
            header = ["record_id", "field_a", "field_b", "field_c", "timestamp", "notes", "record_nonce"]
            rows = [[f"REC{offset:04d}-{i:03d}", f"value_a_{offset}_{i}", f"value_b_{offset}_{i}", f"value_c_{offset}_{i}",
                     (case["incident_date"] + timedelta(hours=i)).isoformat(), f"Record for {ev['title']}", nonce]
                    for i in range(20)]
            return make_csv_bytes(header, rows)


def gen_source_bytes(src: dict[str, Any], dataset: dict[str, Any]) -> bytes:
    case = next(c for c in dataset["cases"] if c["id"] == src["case_id"])
    lines = [
        f"Source Document — CONFIDENTIAL",
        f"Source ID: {src['source_id']}",
        f"Case: {case['case_number']} — {case['title']}",
        f"Source Type: {src['doc_type'].value}",
        "",
        f"{src['title']}",
        "=" * 60,
        "",
        "This is a confidential intelligence/source document. Information contained",
        "herein is for law enforcement investigative use only.",
        "",
        "Source Reliability: Usually reliable",
        "Information Content: Confirmed by other sources",
        "",
        "Summary:",
        f"  Information provided suggests involvement of identified subjects in {case['title']}.",
        "",
        "Handling Instructions:",
        "  This document must not be disclosed to unauthorized personnel.",
        "  All copies must be tracked in the evidence management system.",
        "",
        "CrimeLink Demo Dataset v2 — SYNTHETIC document.",
    ]
    return make_pdf_bytes(src["title"], lines)


# ---------------------------------------------------------------------------
# Database seeding
# ---------------------------------------------------------------------------

def doc_id_for(eid: str) -> str:
    """CaseDocument id for an evidence/source id (``E-0042`` → ``doc-d2-0042``).

    Module level so the graph builder, the relational seeder and the
    verification tests all resolve evidence ids the same way.

    Evidence and source records keep **distinct namespaces**
    (``doc-d2-`` / ``doc-s2-``).  They used to share one, so ``E-0000`` and
    ``S-0000`` both produced ``doc-d2-0000``: the source document was never
    inserted (its id already existed), and every graph node and edge that
    cited a source resolved to an unrelated evidence file instead.  A
    provenance pointer that resolves to the wrong document is worse than one
    that does not resolve.
    """
    key = eid.lower()
    if key.startswith("s-"):
        return f"{SOURCE_DOC_PREFIX}{key.replace('s-', '')}"
    return f"{DOC_PREFIX}{key.replace('e-', '')}"


def derive_case_persons(dataset: dict[str, Any]) -> dict[str, list[str]]:
    """Deterministic case → person membership for the v2 demo dataset.

    Recomputed from ``build_dataset()`` output rather than threaded through it,
    so the relational rows, the generated documents and the graph all agree on
    who belongs to which case.
    """
    case_persons_dict: dict[str, list[str]] = {c["id"]: [] for c in dataset["cases"]}
    for ci, c in enumerate(dataset["cases"]):
        cid = c["id"]
        primary_indices = [ci * 4 % PERSON_COUNT, (ci * 4 + 1) % PERSON_COUNT, (ci * 4 + 2) % PERSON_COUNT, (ci * 4 + 3) % PERSON_COUNT]
        extra_count = (ci * 3) % 6 + 2
        extra = []
        for j in range(extra_count):
            idx = (ci * 7 + j * 13 + 5) % PERSON_COUNT
            if idx not in primary_indices:
                extra.append(idx)
        overlap_count = 1 + (ci % 3)
        for j in range(overlap_count):
            prev_ci = (ci - 1 - j) % CASE_COUNT
            prev_p = case_persons_dict[dataset["cases"][prev_ci]["id"]]
            if prev_p:
                overlap_p = prev_p[j % len(prev_p)]
                if overlap_p not in [dataset["persons"][pi]["id"] for pi in primary_indices + extra if pi < len(dataset["persons"])]:
                    # get index
                    overlap_idx = next(i for i, p in enumerate(dataset["persons"]) if p["id"] == overlap_p)
                    if overlap_idx not in extra:
                        extra.append(overlap_idx)
        all_idxs = list(set(primary_indices + extra))
        for pidx in all_idxs:
            if pidx < len(dataset["persons"]):
                case_persons_dict[cid].append(dataset["persons"][pidx]["id"])

    # Hero case must include its cast
    hero_case_id = dataset["cases"][0]["id"]
    hero_indices = list(range(0, 12))
    case_persons_dict[hero_case_id] = [
        dataset["persons"][i]["id"] for i in hero_indices if i < len(dataset["persons"])
    ]
    return case_persons_dict


def seed_all() -> bool:
    print(f"[seed] Building {DEMO_DATASET_ID}...")
    settings = get_settings()
    settings.ensure_directories()

    # Build canonical data
    dataset = build_dataset()
    dataset["case_persons_dict"] = derive_case_persons(dataset)

    # Open DB session
    engine = get_sync_engine(settings)
    Session = get_sync_sessionmaker()
    session = Session()

    try:
        # Clear any existing data with this dataset_id (idempotency)
        from sqlalchemy import delete as sa_delete
        session.execute(sa_delete(InvestigationStageRun).where(InvestigationStageRun.case_id.like(f"{CASE_PREFIX}%")))
        session.execute(sa_delete(InvestigationTask).where(InvestigationTask.case_id.like(f"{CASE_PREFIX}%")))
        session.execute(sa_delete(DetectedPattern).where(DetectedPattern.case_id.like(f"{CASE_PREFIX}%")))
        session.execute(sa_delete(EvidenceCustodyEvent).where(EvidenceCustodyEvent.case_id.like(f"{CASE_PREFIX}%")))
        session.execute(sa_delete(SourceReference).where(SourceReference.dataset_id == DEMO_DATASET_ID))
        session.execute(sa_delete(DatasetFile).where(DatasetFile.dataset_id == DEMO_DATASET_ID))
        session.execute(sa_delete(InvestigationFinding).where(InvestigationFinding.case_id.like(f"{CASE_PREFIX}%")))
        session.execute(sa_delete(InvestigationSession).where(InvestigationSession.dataset_id == DEMO_DATASET_ID))
        session.execute(sa_delete(CaseDocument).where(CaseDocument.dataset_id == DEMO_DATASET_ID))
        session.execute(sa_delete(Case).where(Case.dataset_id == DEMO_DATASET_ID))
        existing_ds = session.query(Dataset).filter(Dataset.id == DEMO_DATASET_ID).one_or_none()
        if existing_ds:
            session.delete(existing_ds)
        # Also remove demo users if they exist (we'll re-create)
        for ud in DEMO_USERS:
            u = session.query(User).filter(User.badge_number == ud["badge_number"]).one_or_none()
            if u:
                session.delete(u)
        # Clear audit chain for clean start
        session.execute(sa_delete(AuditChainHead))
        session.execute(sa_delete(AuditAnchor))
        session.commit()

        # Initialize audit chain head
        head = AuditChainHead(id=1, last_id=0, last_hash="0" * 64)
        session.add(head)
        session.flush()

        # Create users
        for ud in DEMO_USERS:
            user = User(
                id=ud["id"],
                badge_number=ud["badge_number"],
                full_name=ud["full_name"],
                hashed_password=hash_password(ud["password"]),
                role=ud["role"],
                station_id=ud["station_id"],
                jurisdiction_id=ud["jurisdiction_id"],
                max_classification=InformationClassification.CONFIDENTIAL,
                is_active=True,
            )
            session.add(user)
        session.flush()
        print("  Users created.")

        # Create Dataset row
        ds = Dataset(
            id=DEMO_DATASET_ID,
            name=DATASET_NAME,
            version="2.0",
            status="READY",
            # Activate only after all relational rows are ready. The registry
            # deactivates every competing dataset in the same transaction.
            is_active=False,
            source_kind="builtin",
            root_path=str(settings.data_dir / "datasets" / DEMO_DATASET_ID),
            origin_note="CrimeLink demo dataset v2 — synthetic interconnected data",
            stage_detail={"stage": "READY", "steps": [{"stage": "READY", "detail": "Dataset ready"}]},
            stats={"cases": len(dataset["cases"]), "persons": len(dataset["persons"])},
            graph_built_at=utcnow(),
            search_indexed_at=utcnow(),
            created_by=DEMO_USERS[0]["id"],
        )
        session.add(ds)
        session.flush()
        print("  Dataset registered.")

        # Ensure local object store has buckets
        from app.container import Container
        container = Container(settings)
        object_store = container.object_store
        bucket = settings.minio_bucket_documents

        # Seed all files into object store first, then DB records.
        #
        # Each file's bytes are generated EXACTLY ONCE and reused for the
        # stored object, the recorded content_hash and the size.  Regenerating
        # them later — which this seeder used to do — silently breaks chain of
        # custody: ReportLab stamps a creation timestamp into every PDF, so the
        # second generation produces different bytes and the recorded SHA-256
        # no longer matches the stored object.  GET /evidence/{doc}/verify
        # exists precisely to catch that, and it did.
        evidence_bytes: dict[str, bytes] = {}
        evidence_file_map: dict[str, str] = {}
        for ev in dataset["evidence"]:
            key = ev["storage_key"]
            existing = None
            try:
                if object_store.stat(bucket, key):
                    existing = object_store.get(bucket, key)
            except Exception:
                existing = None
            file_bytes = existing if existing is not None else gen_evidence_bytes(ev, dataset)
            if existing is None:
                object_store.put(bucket, key, file_bytes, content_type=ev["mime_type"])
            evidence_bytes[ev["evidence_id"]] = file_bytes
            evidence_file_map[ev["evidence_id"]] = key

        source_bytes: dict[str, bytes] = {}
        for src in dataset["sources"]:
            key = src["storage_key"]
            existing = None
            try:
                if object_store.stat(bucket, key):
                    existing = object_store.get(bucket, key)
            except Exception:
                existing = None
            file_bytes = existing if existing is not None else gen_source_bytes(src, dataset)
            if existing is None:
                object_store.put(bucket, key, file_bytes, content_type=src["mime_type"])
            source_bytes[src["source_id"]] = file_bytes

        print("  Object store populated.")

        # Create cases
        for c in dataset["cases"]:
            status_map = {
                "OPEN": CaseStatus.OPEN,
                "ACTIVE_INVESTIGATION": CaseStatus.ACTIVE_INVESTIGATION,
                "CLOSED": CaseStatus.CLOSED,
                "DRAFT": CaseStatus.DRAFT,
                "UNDER_REVIEW": CaseStatus.UNDER_REVIEW,
            }
            case = Case(
                id=c["id"],
                case_number=c["case_number"],
                title=c["title"],
                jurisdiction_id=c["jurisdiction_id"],
                dataset_id=DEMO_DATASET_ID,
                dataset_case_key=c["case_number"],
                status=status_map.get(c["status"], CaseStatus.OPEN),
                classification=InformationClassification.CONFIDENTIAL,
                created_by=DEMO_USERS[0]["id"],
            )
            session.add(case)
        session.flush()
        print(f"  {len(dataset['cases'])} cases created.")

        # Create evidence documents
        for ev in dataset["evidence"]:
            file_bytes = evidence_bytes[ev["evidence_id"]]
            content_hash = sha256(file_bytes)
            did = doc_id_for(ev["evidence_id"])
            doc = CaseDocument(
                id=did,
                case_id=ev["case_id"],
                dataset_id=DEMO_DATASET_ID,
                document_type=ev["doc_type"],
                filename=ev["filename"],
                storage_key=ev["storage_key"],
                content_hash=content_hash,
                size_bytes=len(file_bytes),
                mime_type=ev["mime_type"],
                ingestion_status=IngestionStatus.COMPLETE,
                ingestion_stage=6,
                source_confidence=SourceConfidence.VERIFIED,
                classification=InformationClassification.CONFIDENTIAL,
                quarantined=False,
                uploaded_by=DEMO_USERS[1]["id"],
                source_metadata={
                    "evidence_id": ev["evidence_id"],
                    "title": ev["title"],
                    "doc_label": ev["doc_label"],
                    "demo": True,
                    "dataset": DEMO_DATASET_ID,
                },
            )
            session.add(doc)
            # Custody event
            custody = EvidenceCustodyEvent(
                evidence_id=did,
                case_id=ev["case_id"],
                event_type="STORED",
                actor_id=DEMO_USERS[1]["id"],
                object_hash=content_hash,
                location=ev["storage_key"],
                details={"filename": ev["filename"], "demo": True},
            )
            session.add(custody)
        session.flush()
        print(f"  {len(dataset['evidence'])} evidence documents created.")

        # Create source documents
        for src in dataset["sources"]:
            file_bytes = source_bytes[src["source_id"]]
            content_hash = sha256(file_bytes)
            did = doc_id_for(src["source_id"])
            if session.query(CaseDocument).filter(CaseDocument.id == did).one_or_none():
                continue
            doc = CaseDocument(
                id=did,
                case_id=src["case_id"],
                dataset_id=DEMO_DATASET_ID,
                document_type=src["doc_type"],
                filename=src["filename"],
                storage_key=src["storage_key"],
                content_hash=content_hash,
                size_bytes=len(file_bytes),
                mime_type=src["mime_type"],
                ingestion_status=IngestionStatus.COMPLETE,
                ingestion_stage=6,
                source_confidence=SourceConfidence.VERIFIED,
                classification=InformationClassification.CONFIDENTIAL,
                uploaded_by=DEMO_USERS[1]["id"],
                source_metadata={
                    "source_id": src["source_id"],
                    "title": src["title"],
                    "demo": True,
                    "dataset": DEMO_DATASET_ID,
                },
            )
            session.add(doc)
            custody = EvidenceCustodyEvent(
                evidence_id=did,
                case_id=src["case_id"],
                event_type="STORED",
                actor_id=DEMO_USERS[1]["id"],
                object_hash=content_hash,
                location=src["storage_key"],
                details={"filename": src["filename"], "demo": True, "source": True},
            )
            session.add(custody)
        session.flush()
        print(f"  {len(dataset['sources'])} source documents created.")

        # Create DatasetFile entries
        all_files = dataset["evidence"] + dataset["sources"]
        seen_keys = set()
        for f in all_files:
            sk = f["storage_key"]
            if sk in seen_keys:
                continue
            seen_keys.add(sk)
            try:
                if object_store.stat(bucket, sk):
                    file_bytes = object_store.get(bucket, sk)
                    size = len(file_bytes)
                    fhash = sha256(file_bytes)
                else:
                    size = 0
                    fhash = ""
            except Exception:
                size = 0
                fhash = ""
            df = DatasetFile(
                dataset_id=DEMO_DATASET_ID,
                relative_path=sk,
                filename=f["filename"],
                extension=Path(sk).suffix.lstrip("."),
                media_type=f["mime_type"],
                file_kind="document" if f.get("is_pdf", True) else "table",
                size_bytes=size,
                sha256=fhash,
                semantic_type=f["doc_type"].value if hasattr(f["doc_type"], "value") else str(f["doc_type"]),
                classification_confidence=1.0,
                status="INGESTED",
                doc_id=doc_id_for(f.get("evidence_id") or f.get("source_id")),
            )
            session.add(df)
        session.flush()

        # SourceReference for each evidence doc
        for ev in dataset["evidence"]:
            did = doc_id_for(ev["evidence_id"])
            srctype = "pdf" if ev["is_pdf"] else "csv"
            ref = SourceReference(
                doc_id=did,
                case_id=ev["case_id"],
                dataset_id=DEMO_DATASET_ID,
                origin_file=ev["storage_key"],
                source_type=srctype,
                record_id=ev["evidence_id"],
                field_names=[],
                field_values={},
                excerpt=ev["title"],
            )
            session.add(ref)
        session.flush()

        # Findings
        for f in dataset["findings"]:
            # Map evidence codes to doc_ids
            ev_doc_ids = []
            for ecode in f.get("evidence", []):
                if isinstance(ecode, str):
                    ev_doc_ids.append(doc_id_for(ecode))
            finding = InvestigationFinding(
                id=f["id"],
                case_id=f["case_id"],
                finding_type=f["finding_type"],
                title=f["title"],
                narrative=f["narrative"],
                reason=f"Evidence-based finding derived from multi-source convergence",
                confidence=f["confidence"],
                confidence_band=f["confidence_band"],
                method="deterministic",
                entity_keys=f["entity_keys"],
                evidence=[{"doc_id": did, "evidence_id": ecode} for did, ecode in zip(ev_doc_ids, f.get("evidence", []))],
                details=f["details"],
                status=f["status"],
                reviewed_by=DEMO_USERS[1]["id"] if f["status"] == "CONFIRMED" else None,
                review_note=f["title"] if f["status"] == "CONFIRMED" else None,
            )
            if f["details"].get("completed_at"):
                try:
                    finding.created_at = datetime.fromisoformat(f["details"]["completed_at"])
                except Exception:
                    pass
            session.add(finding)
        session.flush()
        print(f"  {len(dataset['findings'])} investigation findings created.")

        # Investigation Sessions
        for f in dataset["findings"]:
            sess_id = f"{SESSION_PREFIX}{f['id'].lower().replace('inv-', '')}"
            sess = InvestigationSession(
                id=sess_id,
                dataset_id=DEMO_DATASET_ID,
                case_id=f["case_id"],
                scope="case",
                title=f["title"][:200],
                state={
                    "investigation_id": f["id"],
                    "finding": f["title"],
                    "evidence": f.get("evidence", []),
                    "entity_keys": f["entity_keys"],
                    "completed_at": f["details"].get("completed_at"),
                },
                created_by=DEMO_USERS[1]["id"],
            )
            session.add(sess)

        # Tasks
        for t in dataset["tasks"]:
            task = InvestigationTask(
                id=t["id"],
                case_id=t["case_id"],
                title=t["title"],
                description=t["description"],
                owner_id=t["owner_id"],
                creator_id=t["creator_id"],
                priority=t["priority"],
                status=t["status"],
                linked_evidence=[doc_id_for(e) if isinstance(e, str) and e.startswith("E-") else e for e in t["linked_evidence"]],
                linked_entities=t["linked_entities"],
                linked_findings=[],
                comments=[],
            )
            session.add(task)

        # Stage runs (one per case showing completed)
        for ci, c in enumerate(dataset["cases"]):
            for stage_num in range(1, 5):
                run = InvestigationStageRun(
                    id=f"{STAGE_RUN_PREFIX}{ci:02d}{stage_num}",
                    case_id=c["id"],
                    stage=stage_num,
                    status="COMPLETED" if stage_num <= 3 else "PENDING",
                    detail={"entities": 8, "evidence": 12, "findings": 1},
                )
                session.add(run)

        # Patterns
        for p in dataset["patterns"]:
            ev_doc_ids = [doc_id_for(e) if isinstance(e, str) and e.startswith("E-") else e for e in p["evidence_doc_ids"]]
            pat = DetectedPattern(
                id=p["id"],
                case_id=p["case_id"],
                pattern_type=p["pattern_type"],
                confidence=p["confidence"],
                entity_keys=p["entity_keys"],
                evidence_doc_ids=ev_doc_ids,
                explanation=p["explanation"],
                details=p["details"],
                status=p["status"],
            )
            session.add(pat)

        # The final step is centralized registry activation: all legacy,
        # production-demo and upload rows become inactive, while their data is
        # preserved. This is idempotent and commits atomically with the seed.
        from app.datasets.registry import set_only_active_sync

        set_only_active_sync(session, ds)
        session.commit()
        print(f"  Database seeding complete. Active dataset: {DEMO_DATASET_ID}")

        # Now build the graph
        print("  Building graph...")
        build_graph(dataset, container, doc_id_for)
        print("  Graph built.")

        # Summary
        counts = {
            "users": len(DEMO_USERS),
            "cases": len(dataset["cases"]),
            "people": len(dataset["persons"]),
            "phones": len(dataset["phones"]),
            "vehicles": len(dataset["vehicles"]),
            "addresses": len(dataset["addresses"]),
            "orgs": len(dataset["orgs"]),
            "accounts": len(dataset["accounts"]),
            "events": len(dataset["events"]),
            "communications": len(dataset["communications"]),
            "transactions": len(dataset["transactions"]),
            "evidence": len(dataset["evidence"]),
            "sources": len(dataset["sources"]),
            "findings": len(dataset["findings"]),
            "relationships": len(dataset["relationships"]),
        }
        print("\n[seed] DEMO-DATASET-002 seeded successfully.")
        for k, v in counts.items():
            print(f"  {k:20s}: {v}")

        return True
    except Exception as exc:
        import traceback
        traceback.print_exc()
        session.rollback()
        print(f"[seed] FAILED: {exc}")
        return False
    finally:
        session.close()
        engine.dispose()


#: Authoritative criminal-status vocabulary.
#:
#: CrimeLink's authoritative field is ``criminal_status`` — it is the column the
#: ingest pipeline maps (``app/datasets/schema_map.py`` → ``PERSON.criminal_status``),
#: the property ``GraphService._node_row`` reads to set ``is_criminal``, and the
#: property the console's ``isConfirmedCriminal`` checks before drawing the ★.
#: Only values in ``CONFIRMED_CRIMINAL_STATUSES`` earn the star.
CONFIRMED_CRIMINAL_STATUSES = {"CONFIRMED", "CONVICTED", "ACCUSED", "CHARGESHEETED", "CRIMINAL"}

#: Person roles this dataset asserts as *actual criminal participation*.
#:
#: Deliberately absent: SUSPECT, PERSON_OF_INTEREST, WITNESS, VICTIM,
#: ASSOCIATE and INFORMANT.  Suspicion, involvement, or appearing in a case,
#: in evidence or in a witness list is **not** a confirmed criminal status and
#: must never earn a ★.
CRIMINAL_STATUS_BY_ROLE: dict[str, str] = {
    "ACCOMPLICE": "CONFIRMED",
}


def confirmed_finding_person_keys(dataset: dict[str, Any]) -> set[str]:
    """Person keys the dataset itself marks as CONFIRMED.

    An ``InvestigationFinding`` whose ``status`` is ``CONFIRMED`` is this
    dataset's explicit, reviewed assertion about the people it names — the
    CR-2001 conspiracy finding, for example, confirms Rajesh Kumar and his two
    named accomplices as the criminal network's coordinators.  Non-person
    entity keys on the same finding (phones, vehicles, addresses) are ignored
    by the caller, which only consults this set for PERSON nodes.
    """
    keys: set[str] = set()
    for finding in dataset.get("findings", []):
        if str(finding.get("status", "")).upper() != "CONFIRMED":
            continue
        for key in finding.get("entity_keys", []):
            keys.add(str(key))
    return keys


def criminal_status_for_person(person: dict[str, Any], confirmed_keys: set[str]) -> str | None:
    """The dataset's own criminal status for a person, or ``None``.

    Two explicit sources only: the person's asserted criminal role, and being
    named by a CONFIRMED investigation finding.  Everything else stays ``None``
    so the graph renders an honest ★-free circle.
    """
    role = str(person.get("role", "")).upper()
    status = "CONFIRMED" if person.get("id") in confirmed_keys else CRIMINAL_STATUS_BY_ROLE.get(role)
    # Guard the vocabulary: a typo here would silently drop the star rather
    # than fail, and a star is a legal statement.
    if status is not None and status not in CONFIRMED_CRIMINAL_STATUSES:
        raise ValueError(f"{person.get('id')}: {status!r} is not a confirmed criminal status")
    return status


#: Which document types plausibly evidence which entity/relationship.  Used to
#: pick a *real* source document per graph record instead of pointing the whole
#: graph at one file.
_SOURCE_TYPE_PREFERENCE: dict[str, tuple[str, ...]] = {
    "PERSON": ("FIR", "WITNESS_STATEMENT", "ARREST_RECORD", "INTELLIGENCE_REPORT"),
    "PHONE": ("CDR", "SURVEILLANCE", "FORENSIC"),
    "BANK_ACCOUNT": ("FINANCIAL", "FINANCIAL_TRANSACTION"),
    "VEHICLE": ("ANPR", "CCTV", "SEIZURE"),
    "LOCATION": ("SCENE_REPORT", "GEO_EVENT", "PATROL_REPORT", "CCTV"),
    "ORGANIZATION": ("INTELLIGENCE_REPORT", "LEGAL_RECORD", "FIR"),
    "EVENT": ("SCENE_REPORT", "CASE_DIARY", "PATROL_REPORT"),
    "USES_PHONE": ("CDR", "SURVEILLANCE"),
    "CALLED": ("CDR", "SURVEILLANCE"),
    "OWNS_ACCOUNT": ("FINANCIAL", "FINANCIAL_TRANSACTION"),
    "TRANSFER_TO": ("FINANCIAL_TRANSACTION", "FINANCIAL"),
    "OWNS_VEHICLE": ("ANPR", "CCTV", "SEIZURE"),
    "LOCATED_AT": ("SCENE_REPORT", "GEO_EVENT", "PATROL_REPORT"),
    "MEMBER_OF": ("INTELLIGENCE_REPORT", "LEGAL_RECORD"),
    "ASSOCIATE_OF": ("WITNESS_STATEMENT", "INTELLIGENCE_REPORT", "FIR"),
    "RELATIVE_OF": ("FIR", "WITNESS_STATEMENT"),
    "PARTICIPATED_IN": ("FIR", "CASE_DIARY"),
}


def build_evidence_index(dataset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """case_id -> that case's evidence documents, in a stable order."""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for ev in dataset.get("evidence", []):
        by_case.setdefault(ev["case_id"], []).append(ev)
    for rows in by_case.values():
        rows.sort(key=lambda e: e["evidence_id"])
    return by_case


def _stable_pick(rows: list[Any], salt: str) -> Any:
    """Deterministic pick that does not depend on PYTHONHASHSEED."""
    import zlib

    return rows[zlib.crc32(salt.encode("utf-8")) % len(rows)]


def source_doc_for(
    index: dict[str, list[dict[str, Any]]],
    case_ids: list[str],
    kind: str,
    salt: str,
) -> str:
    """A real evidence document id for a graph record.

    Picks from the record's own case(s), preferring a document type that could
    plausibly evidence that kind of record.  Every graph node and edge used to
    point at one hardcoded ``doc-d2-0000``, which made the whole provenance
    chain decorative: opening "the source" of a phone in CR-2007 produced an
    FIR from CR-2001.
    """
    candidates: list[dict[str, Any]] = []
    for cid in case_ids:
        candidates.extend(index.get(cid, []))
    if not candidates:
        for rows in index.values():
            candidates.extend(rows)
        if not candidates:
            return doc_id_for("E-0000")
    for wanted in _SOURCE_TYPE_PREFERENCE.get(kind, ()):
        matching = [ev for ev in candidates if ev["doc_type"].value == wanted]
        if matching:
            return doc_id_for(_stable_pick(matching, f"{kind}|{salt}")["evidence_id"])
    return doc_id_for(_stable_pick(candidates, f"{kind}|{salt}")["evidence_id"])



def build_graph(dataset: dict[str, Any], container, doc_id_for):
    """Build the graph from canonical data — nodes and edges."""
    from app.domain.models import GraphNode, GraphEdge
    from app.domain.enums import REL_TYPES

    graph_store = container.graph_store
    # Reset graph for this dataset
    graph_store.purge_dataset(DEMO_DATASET_ID)

    # case_id -> that case's real evidence documents, so every graph record can
    # cite a source that actually exists and actually belongs to its case.
    evidence_index = build_evidence_index(dataset)

    def src(case_ids: list[str], kind: str, salt: str) -> str:
        return source_doc_for(evidence_index, case_ids, kind, salt)

    nodes: list[GraphNode] = []

    # Case nodes
    for c in dataset["cases"]:
        nodes.append(GraphNode(
            provenance_key=f"case:{c['id']}",
            label="Case",
            properties={
                "name": c["case_number"],
                "case_number": c["case_number"],
                "title": c["title"],
                "case_id": c["id"],
                "case_ids": [c["id"]],
                "confidence": 1.0,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "CASE",
                "canonical_id": f"CASE:{c['case_number']}",
            }
        ))

    # Person nodes
    confirmed_keys = confirmed_finding_person_keys(dataset)
    criminal_count = 0
    for p in dataset["persons"]:
        # Determine case_ids for this person
        case_ids = [cid for cid, plist in dataset["case_persons_dict"].items() if p["id"] in plist]
        # A real evidence document from one of this person's cases.
        source_doc = src(case_ids, "PERSON", p["id"])
        criminal_status = criminal_status_for_person(p, confirmed_keys)
        if criminal_status:
            criminal_count += 1
        nodes.append(GraphNode(
            provenance_key=p["id"],
            label="Person",
            properties={
                "name": p["full_name"],
                "display_name": p["full_name"],
                "full_name": p["full_name"],
                "aliases": p["aliases"],
                "gender": p["gender"],
                "occupation": p["occupation"],
                "role": p["role"],
                # Authoritative criminal status.  Set only where this dataset
                # explicitly establishes it; ``None`` otherwise, so the ★ in
                # the console is never inferred from network position.  The
                # graph layer derives ``is_criminal`` from this one field.
                "criminal_status": criminal_status,
                "case_id": case_ids[0] if case_ids else None,
                "case_ids": case_ids,
                "source_doc_id": source_doc,
                "source_doc_ids": [source_doc],
                "confidence": 0.95,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "PERSON",
                "canonical_id": f"PERSON:{p['full_name']}",
                "search_text": f"{p['full_name']} {' '.join(p['aliases'])} {p['role']} {p['occupation']}",
            }
        ))

    # Phone nodes
    for ph in dataset["phones"]:
        # Find cases via person owners
        related_case_ids = set()
        for p in dataset["persons"]:
            if ph["id"] in p["phone_ids"]:
                for cid, plist in dataset["case_persons_dict"].items():
                    if p["id"] in plist:
                        related_case_ids.add(cid)
        _src_doc = src(sorted(related_case_ids), "PHONE", ph["id"])
        nodes.append(GraphNode(
            provenance_key=ph["id"],
            label="Phone",
            properties={
                "name": ph["number"],
                "number": ph["number"],
                "carrier": ph["carrier"],
                "type": ph["type"],
                "case_id": next(iter(related_case_ids)) if related_case_ids else None,
                "case_ids": list(related_case_ids),
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 1.0,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "PHONE",
                "canonical_id": f"PHONE:{ph['number']}",
                "search_text": f"{ph['number']} {ph['carrier']}",
            }
        ))

    # Vehicle nodes
    for v in dataset["vehicles"]:
        related_case_ids = set()
        for p in dataset["persons"]:
            if v["id"] in p["vehicle_ids"]:
                for cid, plist in dataset["case_persons_dict"].items():
                    if p["id"] in plist:
                        related_case_ids.add(cid)
        _src_doc = src(sorted(related_case_ids), "VEHICLE", v["id"])
        nodes.append(GraphNode(
            provenance_key=v["id"],
            label="Vehicle",
            properties={
                "name": v["registration"],
                "plate": v["registration"],
                "make": v["make"],
                "model": v["model"],
                "color": v["color"],
                "case_id": next(iter(related_case_ids)) if related_case_ids else None,
                "case_ids": list(related_case_ids),
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 1.0,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "VEHICLE",
                "canonical_id": f"VEHICLE:{v['registration']}",
                "search_text": f"{v['registration']} {v['make']} {v['model']} {v['color']}",
            }
        ))

    # Address / Location nodes
    for a in dataset["addresses"]:
        # Find cases referencing this address
        related_case_ids = set()
        for p in dataset["persons"]:
            if a["id"] in p["address_ids"]:
                for cid, plist in dataset["case_persons_dict"].items():
                    if p["id"] in plist:
                        related_case_ids.add(cid)
        _src_doc = src(sorted(related_case_ids), "LOCATION", a["id"])
        nodes.append(GraphNode(
            provenance_key=a["id"],
            label="Location",
            properties={
                "name": a["address"],
                "address": a["address"],
                "city": a["city"],
                "category": a["category"],
                "case_id": next(iter(related_case_ids)) if related_case_ids else None,
                "case_ids": list(related_case_ids),
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 0.9,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "LOCATION",
                "canonical_id": f"LOCATION:{a['address'][:30]}",
                "search_text": f"{a['address']} {a['city']} {a['category']}",
            }
        ))

    # Organization nodes
    for o in dataset["orgs"]:
        related_case_ids = set()
        for p in dataset["persons"]:
            if p["org_id"] == o["id"]:
                for cid, plist in dataset["case_persons_dict"].items():
                    if p["id"] in plist:
                        related_case_ids.add(cid)
        _src_doc = src(sorted(related_case_ids), "ORGANIZATION", o["id"])
        nodes.append(GraphNode(
            provenance_key=o["id"],
            label="Organization",
            properties={
                "name": o["name"],
                "org_name": o["name"],
                "category": o["category"],
                "case_id": next(iter(related_case_ids)) if related_case_ids else None,
                "case_ids": list(related_case_ids),
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 0.85,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "ORGANIZATION",
                "canonical_id": f"ORG:{o['name']}",
                "search_text": f"{o['name']} {o['category']}",
            }
        ))

    # Bank Account nodes
    for acct in dataset["accounts"]:
        related_case_ids = set()
        for p in dataset["persons"]:
            if acct["id"] in p["account_ids"]:
                for cid, plist in dataset["case_persons_dict"].items():
                    if p["id"] in plist:
                        related_case_ids.add(cid)
        _src_doc = src(sorted(related_case_ids), "BANK_ACCOUNT", acct["id"])
        nodes.append(GraphNode(
            provenance_key=acct["id"],
            label="BankAccount",
            properties={
                "name": f"{acct['bank']} a/c {acct['account_number'][-4:]}",
                "account_number": acct["account_number"],
                "bank": acct["bank"],
                "ifsc": acct["ifsc"],
                "case_id": next(iter(related_case_ids)) if related_case_ids else None,
                "case_ids": list(related_case_ids),
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 0.98,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "BANK_ACCOUNT",
                "canonical_id": f"ACCOUNT:{acct['account_number']}",
                "search_text": f"{acct['account_number']} {acct['bank']} {acct['ifsc']}",
            }
        ))

    # Event nodes
    for ev in dataset["events"]:
        _src_doc = src([ev["case_id"]], "EVENT", ev["id"])
        nodes.append(GraphNode(
            provenance_key=ev["id"],
            label="Event",
            properties={
                "name": ev["title"],
                "title": ev["title"],
                "event_type": ev["event_type"],
                "timestamp": ev["timestamp"],
                "observed_at": ev["timestamp"],
                "description": ev["description"],
                "case_id": ev["case_id"],
                "case_ids": [ev["case_id"]],
                "source_doc_id": _src_doc,
                "source_doc_ids": [_src_doc],
                "confidence": 0.9,
                "is_active": True,
                "is_document_artifact": False,
                "dataset_id": DEMO_DATASET_ID,
                "entity_type": "EVENT",
                "canonical_id": f"EVENT:{ev['id']}",
                "search_text": f"{ev['title']} {ev['description']} {ev['event_type']}",
            }
        ))

    # Add case nodes link — PERSON -> MEMBER_OF -> CASE
    edges: list[GraphEdge] = []
    allowed_rel_types = set(REL_TYPES)
    for cid, plist in dataset["case_persons_dict"].items():
        for pid in plist:
            part_doc = src([cid], "PARTICIPATED_IN", f"{pid}|{cid}")
            edges.append(GraphEdge(
                source_key=pid,
                target_key=f"case:{cid}",
                rel_type="PARTICIPATED_IN",
                key=f"{pid}-case-{cid}",
                properties={
                    "confidence": 1.0,
                    "source_doc_id": part_doc,
                    "source_doc_ids": [part_doc],
                    "case_id": cid,
                    "case_ids": [cid],
                    "dataset_id": DEMO_DATASET_ID,
                }
            ))

    for rel in dataset["relationships"]:
        rt = rel["rel_type"]
        if rt not in allowed_rel_types:
            continue
        # Every relationship cites a real document from its own case.  An edge
        # with no source at all is refused by the graph store, and an edge that
        # cites the wrong case's file is worse than no provenance.
        rel_doc = rel.get("source_doc_id") or src(
            [rel["case_id"]] if rel.get("case_id") else [],
            rt,
            f"{rel['source']}|{rt}|{rel['target']}|{rel.get('case_id', 'cross')}",
        )
        props = {
            "confidence": rel.get("confidence", 0.85),
            "source_doc_id": rel_doc,
            "source_doc_ids": [rel_doc],
            "case_id": rel.get("case_id"),
            "case_ids": [rel.get("case_id")] if rel.get("case_id") else [],
            "dataset_id": DEMO_DATASET_ID,
        }
        for k in ("call_count", "first_ts", "last_ts", "amount", "ts", "duration_s"):
            if k in rel:
                props[k] = rel[k]
        edge_key = f"{rel['source']}-{rt}-{rel['target']}-{rel.get('case_id', 'cross')}"
        edges.append(GraphEdge(
            source_key=rel["source"],
            target_key=rel["target"],
            rel_type=rt,
            key=edge_key,
            properties=props,
        ))

    # Upsert to graph store
    graph_store.upsert_nodes(nodes)
    graph_store.upsert_edges(edges)
    stats = graph_store.stats()
    print(f"    Graph stats: {stats}")
    print(
        f"    Confirmed criminal persons (authoritative criminal_status): {criminal_count}"
    )


if __name__ == "__main__":
    import asyncio
    os.makedirs(REPO_ROOT / "var" / "data", exist_ok=True)
    os.makedirs(REPO_ROOT / "var" / "objects", exist_ok=True)
    ok = seed_all()
    try:
        asyncio.run(dispose_engines())
    except Exception:
        pass
    sys.exit(0 if ok else 1)
