"""Regenerate the CrimeLink external synthetic corpus (v2.1 Master Index).

Canonical Scenario Guide: CrimeLink Synthetic Dataset v2.1 Master Index:
- C101 Central Branch Bank Robbery — Closed / Solved
- C102 Commercial Vehicle Theft — Closed / Solved
- C103 Extortion of Small Business Owner — Closed / Solved
- C104 Suspicious Financial Transfer Chain — Closed / Solved
- C105 Residential Burglary Series — Closed / Solved
- C106 Organized Electronics Theft and Resale — Closed / Solved
- C107 Forged Identity and Property Documents — Closed / Solved
- C108 Unsolved Warehouse Theft — Unsolved / Ongoing (explicit CDR & CCTV data gaps)
- C109 Unsolved Extortion Network — Unsolved / Ongoing
- C110 Suspicious Transfer Network — Unsolved / Ongoing

Preserved Analytical Ground Truth:
- P003 Vikram Rao: Bridge candidate with high weighted-betweenness across C101, C104, C107, C110.
- P005 Imran Sheikh: Cross-case communication and financial connector across C101, C102, C103, C106, C109.
- P014 Sunita Verma, P010 Anand Kulkarni, P011 Ramesh Yadav: Deliberate negative controls with
  verifiable legitimate explanations (vendor, bank teller, automotive mechanic).
- C108 Data Gaps: Tower outage during incident window, CCTV camera 3 corrupted footage.
- Complete File-Backed Dossiers: Every case contains 16 real physical documents (FIR, Case Diary,
  CDR, Bank Statements, CCTV log, Patrol log, Surveillance, Witness Statements, Scene report,
  Intel notes, Evidence register, Relationships, Chargesheet / Unsolved note, Complete PDF dossier,
  and Document indices).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration & Paths
# ---------------------------------------------------------------------------

DEFAULT_SEED = 20260902

BACKEND_DIR = Path(__file__).resolve().parents[2]
CORPUS_ROOT = BACKEND_DIR / "CrimeLink_Synthetic_Corpus_v1"

OPERATIONAL_DIR = "operational"
DOCUMENTS_DIR = "documents"
GROUND_TRUTH_DIR = "_ground_truth"
METADATA_DIR = "metadata"

CALL_TYPES = ["VOICE", "SMS"]
TX_TYPES = ["IMPS", "NEFT", "RTGS", "UPI", "CASH_DEPOSIT"]
SIGHTING_SOURCES = ["CCTV", "PATROL", "ANPR", "INTELLIGENCE"]
BANK_CODES = ["HDFC", "SBI", "ICICI", "AXIS", "PNB", "KOTAK"]

# ---------------------------------------------------------------------------
# Canonical Entities Specification (v2.1 Master Index)
# ---------------------------------------------------------------------------

MASTER_PERSONS = [
    {
        "person_id": "P001",
        "full_name": "Rajesh Sharma",
        "gender": "M",
        "dob": "1982-04-12",
        "address": "45 Station Road",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Principal conspirator in C101 bank robbery",
    },
    {
        "person_id": "P002",
        "full_name": "Suresh Patil",
        "gender": "M",
        "dob": "1985-08-23",
        "address": "12 Market Street",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Armed accomplice in C101; logistics coordinator in C102",
    },
    {
        "person_id": "P003",
        "full_name": "Vikram Rao",
        "gender": "M",
        "dob": "1978-11-05",
        "address": "88 MG Road",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "UNDER_INVESTIGATION",
        "role_notes": "Bridge entity across C101, C104, C107, C110; financial controller; alias Vicky",
        "alias": "Vicky",
    },
    {
        "person_id": "P004",
        "full_name": "Harish Varma",
        "gender": "M",
        "dob": "1988-02-17",
        "address": "104 Industrial Area",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Vehicle theft specialist in C102; electronics transport in C106",
    },
    {
        "person_id": "P005",
        "full_name": "Imran Sheikh",
        "gender": "M",
        "dob": "1984-06-29",
        "address": "22 Highway Junction",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Cross-case telecom/financial connector across C101, C102, C103, C106, C109; alias Immu",
        "alias": "Immu",
    },
    {
        "person_id": "P006",
        "full_name": "Manoj Das",
        "gender": "M",
        "dob": "1990-09-14",
        "address": "67 Gandhi Nagar",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Enforcer in C103 extortion network",
    },
    {
        "person_id": "P007",
        "full_name": "Deepak Nair",
        "gender": "M",
        "dob": "1993-01-30",
        "address": "15 Residency Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "CONVICTED",
        "role_notes": "Mule account operator in C104",
    },
    {
        "person_id": "P008",
        "full_name": "Amit Joshi",
        "gender": "M",
        "dob": "1986-12-08",
        "address": "33 Ring Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "CONVICTED",
        "role_notes": "Lead burglar in C105 burglary series",
    },
    {
        "person_id": "P009",
        "full_name": "Rohan Hegde",
        "gender": "M",
        "dob": "1989-05-19",
        "address": "78 Old City",
        "city": "Hyderabad",
        "state": "Telangana",
        "status": "CONVICTED",
        "role_notes": "Grey-market electronics receiver in C106",
    },
    {
        "person_id": "P010",
        "full_name": "Anand Kulkarni",
        "gender": "M",
        "dob": "1975-03-22",
        "address": "90 Bank Colony",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "ACTIVE",
        "role_notes": "NEGATIVE CONTROL: Senior bank teller at Central Branch; performed legitimate counter duties",
    },
    {
        "person_id": "P011",
        "full_name": "Ramesh Yadav",
        "gender": "M",
        "dob": "1981-07-11",
        "address": "14 Bypass Road",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "ACTIVE",
        "role_notes": "NEGATIVE CONTROL: Commercial mechanic at Highway Auto Works; serviced freight vehicle V002 legitimately",
    },
    {
        "person_id": "P012",
        "full_name": "Pradeep Reddy",
        "gender": "M",
        "dob": "1980-10-03",
        "address": "56 Revenue Colony",
        "city": "Hyderabad",
        "state": "Telangana",
        "status": "CONVICTED",
        "role_notes": "Document forger in C107",
    },
    {
        "person_id": "P013",
        "full_name": "Sanjay Mishra",
        "gender": "M",
        "dob": "1987-04-16",
        "address": "112 Logistics Hub",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "ACTIVE",
        "role_notes": "Warehouse supervisor in C108; reported theft",
    },
    {
        "person_id": "P014",
        "full_name": "Sunita Verma",
        "gender": "F",
        "dob": "1983-08-25",
        "address": "29 Industrial Estate",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "ACTIVE",
        "role_notes": "NEGATIVE CONTROL: Registered civilian supplier of corrugated packaging materials to Apex Logistics",
    },
    {
        "person_id": "P015",
        "full_name": "Sana Iyer",
        "gender": "F",
        "dob": "1991-09-12",
        "address": "18 Commercial Street",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "ACTIVE",
        "role_notes": "Small retail merchant; complainant and key witness in C103; corroborated in C104",
    },
    {
        "person_id": "P016",
        "full_name": "Ravi Mehta",
        "gender": "M",
        "dob": "1979-02-14",
        "address": "52 Hill Road",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "ACTIVE",
        "role_notes": "Security guard and key eyewitness in C101 and C106",
    },
    {
        "person_id": "P017",
        "full_name": "Asha Nair",
        "gender": "F",
        "dob": "1986-11-28",
        "address": "61 Lake View",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "UNDER_INVESTIGATION",
        "role_notes": "Financial operative associated with Vikram Rao in C104 and C107",
    },
    {
        "person_id": "P018",
        "full_name": "Nikhil Saxena",
        "gender": "M",
        "dob": "1992-06-15",
        "address": "40 Transport Nagar",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "CONVICTED",
        "role_notes": "Syndicate transport driver in C102",
    },
    {
        "person_id": "P019",
        "full_name": "Arjun Bhat",
        "gender": "M",
        "dob": "1985-01-20",
        "address": "8 Market Yard",
        "city": "Pune",
        "state": "Maharashtra",
        "status": "ACTIVE",
        "role_notes": "Wholesale fruit dealer; witness to extortion threats in C103",
    },
    {
        "person_id": "P020",
        "full_name": "Chetan Pillai",
        "gender": "M",
        "dob": "1994-03-09",
        "address": "73 Electronic City",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "CONVICTED",
        "role_notes": "Mule account supplier in C104",
    },
    {
        "person_id": "P021",
        "full_name": "Dev Chauhan",
        "gender": "M",
        "dob": "1988-10-18",
        "address": "19 BTM Layout",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "CONVICTED",
        "role_notes": "Lookout in C105 burglary series",
    },
    {
        "person_id": "P022",
        "full_name": "Gaurav Goud",
        "gender": "M",
        "dob": "1983-05-14",
        "address": "81 Silver Market",
        "city": "Bengaluru",
        "state": "Karnataka",
        "status": "CONVICTED",
        "role_notes": "Pawn shop operator and stolen jewellery receiver in C105",
    },
    {
        "person_id": "P023",
        "full_name": "Lakshmi Das",
        "gender": "F",
        "dob": "1972-12-01",
        "address": "9 Banjara Hills",
        "city": "Hyderabad",
        "state": "Telangana",
        "status": "ACTIVE",
        "role_notes": "Real estate owner whose property documents were forged in C107",
    },
    {
        "person_id": "P024",
        "full_name": "Ishaan Mishra",
        "gender": "M",
        "dob": "1995-07-27",
        "address": "15 Warehouse Road",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "ACTIVE",
        "role_notes": "Night security guard at Logistics Warehouse in C108",
    },
    {
        "person_id": "P025",
        "full_name": "Tanvi Rao",
        "gender": "F",
        "dob": "1990-02-11",
        "address": "28 Transport Nagar",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "ACTIVE",
        "role_notes": "Logistics manager in C108; reported manifest discrepancies",
    },
    {
        "person_id": "P026",
        "full_name": "Divya Reddy",
        "gender": "F",
        "dob": "1989-11-19",
        "address": "92 Highway Toll Road",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "ACTIVE",
        "role_notes": "Fleet transport operator targeted by extortion network in C109",
    },
    {
        "person_id": "P027",
        "full_name": "Meera Saxena",
        "gender": "F",
        "dob": "1992-04-05",
        "address": "63 Transport Chowk",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "ACTIVE",
        "role_notes": "Transport dispatcher who received threat calls in C109",
    },
    {
        "person_id": "P028",
        "full_name": "Manoj Kulkarni",
        "gender": "M",
        "dob": "1985-09-30",
        "address": "47 Bank Street",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "UNDER_INVESTIGATION",
        "role_notes": "Suspect account holder in C110 transfer network",
    },
    {
        "person_id": "P029",
        "full_name": "Zoya Chauhan",
        "gender": "F",
        "dob": "1993-08-16",
        "address": "84 Cyber Zone",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "UNDER_INVESTIGATION",
        "role_notes": "Intermediary in C110 suspicious transfer chain",
    },
    {
        "person_id": "P030",
        "full_name": "Kavya Sharma",
        "gender": "F",
        "dob": "1991-03-24",
        "address": "12 Civil Lines",
        "city": "Jaipur",
        "state": "Rajasthan",
        "status": "UNDER_INVESTIGATION",
        "role_notes": "Beneficiary account holder in C110",
    },
]

BACKGROUND_PERSONS_SPEC = [
    {"person_id": f"P{i:03d}", "full_name": name, "gender": gender, "dob": dob, "city": city, "state": state}
    for i, (name, gender, dob, city, state) in enumerate(
        [
            ("Aarav Das", "M", "1984-05-12", "Pune", "Maharashtra"),
            ("Ananya Hegde", "F", "1990-11-03", "Pune", "Maharashtra"),
            ("Bhavna Mishra", "F", "1987-08-19", "Bengaluru", "Karnataka"),
            ("Dev Saxena", "M", "1993-02-28", "Bengaluru", "Karnataka"),
            ("Gaurav Pillai", "M", "1981-06-17", "Hyderabad", "Telangana"),
            ("Lakshmi Reddy", "F", "1989-10-09", "Hyderabad", "Telangana"),
            ("Nikhil Kulkarni", "M", "1992-01-14", "Jaipur", "Rajasthan"),
            ("Nisha Yadav", "F", "1995-07-21", "Jaipur", "Rajasthan"),
            ("Pranav Chauhan", "M", "1986-12-04", "Pune", "Maharashtra"),
            ("Priya Patil", "F", "1988-09-18", "Pune", "Maharashtra"),
            ("Rahul Goud", "M", "1991-04-26", "Bengaluru", "Karnataka"),
            ("Rani Verma", "F", "1983-03-15", "Bengaluru", "Karnataka"),
        ],
        start=31,
    )
]

MASTER_CASES = [
    {
        "case_id": "C101",
        "case_number": "FIR/2024/00101",
        "title": "Central Branch Bank Robbery",
        "case_type": "ROBBERY",
        "status": "CLOSED",
        "police_station": "PS-01 Central",
        "city": "Pune",
        "state": "Maharashtra",
        "registered_date": "2024-03-15",
        "summary": "Armed robbery at Central Branch Bank resulting in cash theft of INR 45,00,000. Solved with recovery and charge sheet filed against principal conspirators.",
        "members": [
            ("P001", "ACCUSED"),
            ("P002", "ACCOMPLICE"),
            ("P003", "SUSPECT"),
            ("P005", "CO_CONSPIRATOR"),
            ("P010", "WITNESS"),  # Decoy / Negative control
            ("P016", "WITNESS"),
        ],
    },
    {
        "case_id": "C102",
        "case_number": "FIR/2024/00102",
        "title": "Commercial Vehicle Theft",
        "case_type": "THEFT",
        "status": "CLOSED",
        "police_station": "PS-02 Highway",
        "city": "Pune",
        "state": "Maharashtra",
        "registered_date": "2024-04-10",
        "summary": "Interstate commercial freight truck theft syndicate intercepted at toll junction. Vehicle recovered; charge sheet filed.",
        "members": [
            ("P002", "ACCUSED"),
            ("P004", "SUSPECT"),
            ("P005", "CO_CONSPIRATOR"),
            ("P011", "WITNESS"),  # Decoy / Negative control
            ("P018", "ACCOMPLICE"),
        ],
    },
    {
        "case_id": "C103",
        "case_number": "FIR/2024/00103",
        "title": "Extortion of Small Business Owner",
        "case_type": "EXTORTION",
        "status": "CLOSED",
        "police_station": "PS-03 Market",
        "city": "Pune",
        "state": "Maharashtra",
        "registered_date": "2024-05-02",
        "summary": "Protection money extortion racket targeting retail merchants at Market Yard. Threat calls traced and suspects arrested.",
        "members": [
            ("P005", "ACCUSED"),
            ("P006", "ACCOMPLICE"),
            ("P015", "VICTIM"),
            ("P019", "WITNESS"),
        ],
    },
    {
        "case_id": "C104",
        "case_number": "FIR/2024/00104",
        "title": "Suspicious Financial Transfer Chain",
        "case_type": "FINANCIAL_FRAUD",
        "status": "CLOSED",
        "police_station": "PS-04 Cyber",
        "city": "Bengaluru",
        "state": "Karnataka",
        "registered_date": "2024-06-18",
        "summary": "Layered fund transfer chain routing proceeds through intermediate mule accounts. Account freeze and charge sheet filed.",
        "members": [
            ("P003", "SUSPECT"),
            ("P007", "ACCUSED"),
            ("P014", "WITNESS"),  # Decoy / Negative control
            ("P017", "ACCOMPLICE"),
            ("P020", "ACCOMPLICE"),
        ],
    },
    {
        "case_id": "C105",
        "case_number": "FIR/2024/00105",
        "title": "Residential Burglary Series",
        "case_type": "THEFT",
        "status": "CLOSED",
        "police_station": "PS-05 Suburban",
        "city": "Bengaluru",
        "state": "Karnataka",
        "registered_date": "2024-07-22",
        "summary": "Series of nighttime residential burglaries across gated sectors. Stolen jewellery recovered from pawn intermediary.",
        "members": [
            ("P008", "ACCUSED"),
            ("P021", "ACCOMPLICE"),
            ("P022", "RECEIVER"),
        ],
    },
    {
        "case_id": "C106",
        "case_number": "FIR/2024/00106",
        "title": "Organized Electronics Theft and Resale",
        "case_type": "THEFT",
        "status": "CLOSED",
        "police_station": "PS-06 Industrial",
        "city": "Hyderabad",
        "state": "Telangana",
        "registered_date": "2024-08-30",
        "summary": "Large-scale theft of warehouse electronic components and grey-market redistribution. Fenced inventory seized.",
        "members": [
            ("P004", "ACCUSED"),
            ("P005", "CO_CONSPIRATOR"),
            ("P009", "RECEIVER"),
            ("P016", "WITNESS"),
        ],
    },
    {
        "case_id": "C107",
        "case_number": "FIR/2024/00107",
        "title": "Forged Identity and Property Documents",
        "case_type": "FINANCIAL_FRAUD",
        "status": "CLOSED",
        "police_station": "PS-07 Revenue",
        "city": "Hyderabad",
        "state": "Telangana",
        "registered_date": "2024-09-14",
        "summary": "Fabrication of fraudulent land title deeds and forged identity papers used for collateral mortgages.",
        "members": [
            ("P003", "SUSPECT"),
            ("P012", "ACCUSED"),
            ("P017", "ACCOMPLICE"),
            ("P023", "VICTIM"),
        ],
    },
    {
        "case_id": "C108",
        "case_number": "FIR/2024/00108",
        "title": "Unsolved Warehouse Theft",
        "case_type": "THEFT",
        "status": "UNDER_INVESTIGATION",
        "police_station": "PS-08 Logistics Hub",
        "city": "Jaipur",
        "state": "Rajasthan",
        "registered_date": "2024-10-05",
        "summary": "High-value logistics consignment breach. Preserved data gaps: CCTV camera 3 footage corrupted between 01:00 and 03:30; cell tower sector offline for scheduled maintenance. Investigation ongoing; no charge sheet filed.",
        "members": [
            ("P013", "WITNESS"),
            ("P024", "WITNESS"),
            ("P025", "WITNESS"),
        ],
        "data_gaps": {
            "cctv_gap": "CCTV camera 3 video corrupted and power feed interrupted between 01:00:00 and 03:30:00 on 2024-10-05.",
            "cdr_gap": "Cell tower sector L008 experienced power outage and maintenance downtime between 00:45:00 and 04:00:00; tower dump unavailable.",
        },
    },
    {
        "case_id": "C109",
        "case_number": "FIR/2024/00109",
        "title": "Unsolved Extortion Network",
        "case_type": "EXTORTION",
        "status": "UNDER_INVESTIGATION",
        "police_station": "PS-09 North",
        "city": "Jaipur",
        "state": "Rajasthan",
        "registered_date": "2024-11-12",
        "summary": "Ongoing extortion threats against transport fleet operators. Anonymous burner phones used; network remains active and under surveillance.",
        "members": [
            ("P005", "SUSPECT"),
            ("P026", "VICTIM"),
            ("P027", "WITNESS"),
        ],
    },
    {
        "case_id": "C110",
        "case_number": "FIR/2024/00110",
        "title": "Suspicious Transfer Network",
        "case_type": "FINANCIAL_FRAUD",
        "status": "UNDER_INVESTIGATION",
        "police_station": "PS-10 Special Cell",
        "city": "Jaipur",
        "state": "Rajasthan",
        "registered_date": "2024-12-01",
        "summary": "Complex multi-hop transaction chain spanning multiple banking entities. Primary beneficiary accounts unverified; forensic audit ongoing.",
        "members": [
            ("P003", "SUSPECT"),
            ("P028", "SUSPECT"),
            ("P029", "ACCOMPLICE"),
            ("P030", "BENEFICIARY"),
        ],
    },
]

ORGANIZATIONS_SPEC = [
    {"organization_id": "ORG001", "name": "Apex Logistics Pvt Ltd", "organization_type": "LOGISTICS", "city": "Pune", "state": "Maharashtra"},
    {"organization_id": "ORG002", "name": "Central Commercial Bank", "organization_type": "FINANCE", "city": "Pune", "state": "Maharashtra"},
    {"organization_id": "ORG003", "name": "Highway Auto Works", "organization_type": "SERVICES", "city": "Pune", "state": "Maharashtra"},
    {"organization_id": "ORG004", "name": "Verma Packaging Solutions", "organization_type": "MANUFACTURING", "city": "Bengaluru", "state": "Karnataka"},
    {"organization_id": "ORG005", "name": "Southern Tech Traders", "organization_type": "RETAIL", "city": "Hyderabad", "state": "Telangana"},
    {"organization_id": "ORG006", "name": "Crown Jewellers & Pawn", "organization_type": "COMMERCE", "city": "Bengaluru", "state": "Karnataka"},
    {"organization_id": "ORG007", "name": "Deccan Realty & Properties", "organization_type": "REAL_ESTATE", "city": "Hyderabad", "state": "Telangana"},
    {"organization_id": "ORG008", "name": "Rajputana Freight Carriers", "organization_type": "TRANSPORT", "city": "Jaipur", "state": "Rajasthan"},
]

LOCATIONS_SPEC = [
    {"location_id": "L001", "name": "Central Bank Branch Pune", "city": "Pune", "state": "Maharashtra", "latitude": "18.520430", "longitude": "73.856744"},
    {"location_id": "L002", "name": "Market Yard Market Street", "city": "Pune", "state": "Maharashtra", "latitude": "18.497500", "longitude": "73.868200"},
    {"location_id": "L003", "name": "Highway Toll Plaza Junction", "city": "Pune", "state": "Maharashtra", "latitude": "18.625100", "longitude": "73.801200"},
    {"location_id": "L004", "name": "Electronic City Cyber Hub", "city": "Bengaluru", "state": "Karnataka", "latitude": "12.845200", "longitude": "77.660200"},
    {"location_id": "L005", "name": "Suburban Sector 4 Residency", "city": "Bengaluru", "state": "Karnataka", "latitude": "12.934500", "longitude": "77.610100"},
    {"location_id": "L006", "name": "Industrial Area Logistics Park", "city": "Hyderabad", "state": "Telangana", "latitude": "17.439900", "longitude": "78.375500"},
    {"location_id": "L007", "name": "Revenue Sub-Registrar Office", "city": "Hyderabad", "state": "Telangana", "latitude": "17.385044", "longitude": "78.486671"},
    {"location_id": "L008", "name": "Logistics Hub Warehouse Sector 8", "city": "Jaipur", "state": "Rajasthan", "latitude": "26.912400", "longitude": "75.787300"},
    {"location_id": "L009", "name": "Transport Nagar Freight Depot", "city": "Jaipur", "state": "Rajasthan", "latitude": "26.891100", "longitude": "75.823400"},
    {"location_id": "L010", "name": "Civil Lines Financial Enclave", "city": "Jaipur", "state": "Rajasthan", "latitude": "26.905600", "longitude": "75.792200"},
]

# ---------------------------------------------------------------------------
# Corpus Builder
# ---------------------------------------------------------------------------

class ExternalCorpusBuilder:
    def __init__(self, seed: int = DEFAULT_SEED, root: Path | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.root = root or CORPUS_ROOT

        self.cases: list[dict] = []
        self.persons: list[dict] = []
        self.phones: list[dict] = []
        self.vehicles: list[dict] = []
        self.accounts: list[dict] = []
        self.locations: list[dict] = []
        self.organizations: list[dict] = []
        self.case_members: list[dict] = []
        self.person_orgs: list[dict] = []
        self.cdr: list[dict] = []
        self.transactions: list[dict] = []
        self.sightings: list[dict] = []
        self.intel: list[dict] = []
        self.evidence: list[dict] = []
        self.documents: list[dict] = []

        # Ground truth structures
        self.gt_relationships: list[dict] = []
        self.gt_bridge_people: list[dict] = []
        self.gt_negative_controls: list[dict] = []
        self.gt_data_gaps: list[dict] = []
        self.gt_hidden_networks: list[dict] = []
        self.gt_money_cycles: list[dict] = []

        self._doc_files: dict[str, str | bytes] = {}
        self._doc_index: list[dict] = []

    def build(self) -> None:
        """Construct the entire dataset deterministically."""
        # 1. Base entities
        self._build_locations()
        self._build_organizations()
        self._build_persons_and_assets()

        # 2. Cases and event graph
        self._build_cases_and_events()

        # 3. Dossier documents (16 per case)
        self._build_all_dossiers()

        # 4. Ground truth evaluation records
        self._build_ground_truth()

    def _build_locations(self) -> None:
        self.locations = list(LOCATIONS_SPEC)

    def _build_organizations(self) -> None:
        self.organizations = list(ORGANIZATIONS_SPEC)

    def _build_persons_and_assets(self) -> None:
        all_persons_spec = MASTER_PERSONS + BACKGROUND_PERSONS_SPEC
        for idx, p in enumerate(all_persons_spec, start=1):
            pid = p["person_id"]
            person = {
                "person_id": pid,
                "full_name": p["full_name"],
                "gender": p.get("gender", "M"),
                "dob": p.get("dob", "1985-01-01"),
                "address": p.get("address", f"{idx * 3} Station Road"),
                "city": p.get("city", "Pune"),
                "state": p.get("state", "Maharashtra"),
                "status": p.get("status", "ACTIVE"),
            }
            self.persons.append(person)

            # Assign phones: Primary phone for every person
            phone_num = f"+9198{idx:02d}00{idx:04d}"[:13]
            if len(phone_num) < 13:
                phone_num = f"+9198123{idx:05d}"[:13]
            self.phones.append(
                {
                    "phone_id": f"PH{idx:04d}",
                    "phone_number": phone_num,
                    "owner_person_id": pid,
                    "status": "ACTIVE",
                    "source": "SYNTHETIC",
                }
            )

            # Assign vehicles to most persons
            if idx <= 30:
                plate_state = "MH" if "Maharashtra" in person["state"] else ("KA" if "Karnataka" in person["state"] else ("TS" if "Telangana" in person["state"] else "RJ"))
                self.vehicles.append(
                    {
                        "vehicle_id": f"V{idx:04d}",
                        "registration_number": f"{plate_state}{10 + (idx % 20):02d}AB{1000 + idx:04d}",
                        "vehicle_type": "SEDAN" if idx % 2 == 0 else "SUV",
                        "owner_person_id": pid,
                        "color": ["Silver", "White", "Black", "Grey", "Blue"][idx % 5],
                    }
                )

            # Assign bank accounts to all persons
            self.accounts.append(
                {
                    "account_id": f"AC{idx:04d}",
                    "account_number": f"10000000{idx:04d}",
                    "holder_person_id": pid,
                    "bank_code": BANK_CODES[idx % len(BANK_CODES)],
                    "account_status": "ACTIVE",
                }
            )

        # Person-organization links
        # Vikram Rao is director at Apex Logistics
        self.person_orgs.append({"person_org_id": "PO0001", "person_id": "P003", "organization_id": "ORG001", "role": "DIRECTOR", "start_date": "2020-01-01", "end_date": ""})
        # Anand Kulkarni is senior teller at Central Commercial Bank
        self.person_orgs.append({"person_org_id": "PO0002", "person_id": "P010", "organization_id": "ORG002", "role": "SENIOR_TELLER", "start_date": "2015-06-01", "end_date": ""})
        # Ramesh Yadav is lead mechanic at Highway Auto Works
        self.person_orgs.append({"person_org_id": "PO0003", "person_id": "P011", "organization_id": "ORG003", "role": "LEAD_MECHANIC", "start_date": "2018-03-15", "end_date": ""})
        # Sunita Verma is proprietor at Verma Packaging Solutions
        self.person_orgs.append({"person_org_id": "PO0004", "person_id": "P014", "organization_id": "ORG004", "role": "PROPRIETOR", "start_date": "2019-09-01", "end_date": ""})
        # Harish Varma is driver/associate at Southern Tech Traders
        self.person_orgs.append({"person_org_id": "PO0005", "person_id": "P004", "organization_id": "ORG005", "role": "CONTRACTOR", "start_date": "2023-01-10", "end_date": ""})

    def _build_cases_and_events(self) -> None:
        phones_by_pid = {p["owner_person_id"]: p["phone_id"] for p in self.phones}
        accounts_by_pid = {a["holder_person_id"]: a["account_id"] for a in self.accounts}
        vehicles_by_pid = {v["owner_person_id"]: v["vehicle_id"] for v in self.vehicles}

        cdr_seq = 0
        tx_seq = 0
        sight_seq = 0
        intel_seq = 0
        ev_seq = 0

        for case_spec in MASTER_CASES:
            cid = case_spec["case_id"]
            self.cases.append(
                {
                    "case_id": cid,
                    "case_number": case_spec["case_number"],
                    "registered_date": case_spec["registered_date"],
                    "case_type": case_spec["case_type"],
                    "police_station": case_spec["police_station"],
                    "city": case_spec["city"],
                    "status": case_spec["status"],
                }
            )

            # Members
            for pid, role in case_spec["members"]:
                cm_id = f"CM{cid[1:]}{pid[1:]}"
                self.case_members.append(
                    {
                        "case_member_id": cm_id,
                        "case_id": cid,
                        "person_id": pid,
                        "role": role,
                    }
                )

            # Deterministic Events per case
            base_date = datetime.strptime(case_spec["registered_date"], "%Y-%m-%d")
            member_pids = [m[0] for m in case_spec["members"]]

            # Special case C108 has CDR and CCTV gaps
            is_c108 = cid == "C108"

            # CDR: Inter-member communication
            if not is_c108:
                for i in range(len(member_pids)):
                    for j in range(i + 1, min(i + 3, len(member_pids))):
                        p1, p2 = member_pids[i], member_pids[j]
                        ph1 = phones_by_pid.get(p1)
                        ph2 = phones_by_pid.get(p2)
                        if ph1 and ph2:
                            cdr_seq += 1
                            call_time = (base_date - timedelta(days=self.rng.randint(1, 10), hours=self.rng.randint(8, 20))).strftime("%Y-%m-%d %H:%M:%S")
                            self.cdr.append(
                                {
                                    "cdr_id": f"CDR{cdr_seq:05d}",
                                    "timestamp": call_time,
                                    "from_phone_id": ph1,
                                    "to_phone_id": ph2,
                                    "duration_seconds": str(self.rng.randint(45, 600)),
                                    "call_type": "VOICE" if cdr_seq % 2 == 0 else "SMS",
                                    "cell_location_id": "L001",
                                    "case_id": cid,
                                }
                            )
            else:
                # C108 has explicit tower gap during incident window
                cdr_seq += 1
                call_time = "2024-10-04 18:20:00"  # Well before incident window
                self.cdr.append(
                    {
                        "cdr_id": f"CDR{cdr_seq:05d}",
                        "timestamp": call_time,
                        "from_phone_id": phones_by_pid.get("P013", "PH0013"),
                        "to_phone_id": phones_by_pid.get("P025", "PH0025"),
                        "duration_seconds": "180",
                        "call_type": "VOICE",
                        "cell_location_id": "L008",
                        "case_id": cid,
                    }
                )

            # Transactions: Case finances
            for i in range(len(member_pids)):
                for j in range(i + 1, min(i + 2, len(member_pids))):
                    p1, p2 = member_pids[i], member_pids[j]
                    ac1 = accounts_by_pid.get(p1)
                    ac2 = accounts_by_pid.get(p2)
                    if ac1 and ac2:
                        tx_seq += 1
                        tx_time = (base_date - timedelta(days=self.rng.randint(2, 14), hours=self.rng.randint(9, 17))).strftime("%Y-%m-%d %H:%M:%S")
                        amount = 45000.00 if p2 == "P014" else (self.rng.randint(20, 200) * 5000.0)
                        self.transactions.append(
                            {
                                "transaction_id": f"TX{tx_seq:05d}",
                                "timestamp": tx_time,
                                "from_account_id": ac1,
                                "to_account_id": ac2,
                                "amount_inr": f"{amount:.2f}",
                                "transaction_type": "NEFT" if tx_seq % 2 == 0 else "IMPS",
                                "location_id": "L001",
                                "case_id": cid,
                            }
                        )

            # Vehicle sightings
            if not is_c108:
                for pid in member_pids[:3]:
                    vid = vehicles_by_pid.get(pid)
                    if vid:
                        sight_seq += 1
                        s_time = (base_date - timedelta(days=self.rng.randint(1, 5), hours=self.rng.randint(8, 22))).strftime("%Y-%m-%d %H:%M:%S")
                        self.sightings.append(
                            {
                                "sighting_id": f"VS{sight_seq:05d}",
                                "vehicle_id": vid,
                                "location_id": "L001",
                                "timestamp": s_time,
                                "case_id": cid,
                                "source": "CCTV",
                            }
                        )
            else:
                # C108 has CCTV gap at 01:00-03:30; earlier sighting only
                sight_seq += 1
                self.sightings.append(
                    {
                        "sighting_id": f"VS{sight_seq:05d}",
                        "vehicle_id": vehicles_by_pid.get("P013", "V0013"),
                        "location_id": "L008",
                        "timestamp": "2024-10-04 20:15:00",
                        "case_id": cid,
                        "source": "CCTV",
                    }
                )

            # Intelligence notes
            intel_seq += 1
            self.intel.append(
                {
                    "report_id": f"IR{intel_seq:05d}",
                    "report_date": base_date.strftime("%Y-%m-%d"),
                    "subject_person_id": member_pids[0],
                    "location_id": "L001",
                    "case_id": cid,
                    "source_type": "SURVEILLANCE_NOTE",
                    "summary": f"Field observation confirms subject {member_pids[0]} active in vicinity of {case_spec['title']}.",
                }
            )

            # Evidence item
            ev_seq += 1
            self.evidence.append(
                {
                    "evidence_id": f"EV{ev_seq:05d}",
                    "case_id": cid,
                    "evidence_type": "DIGITAL" if ev_seq % 2 == 0 else "PHYSICAL",
                    "description": f"Seized evidentiary material relevant to {case_spec['title']}.",
                    "seized_from_person_id": member_pids[0],
                    "location_id": "L001",
                    "chain_of_custody": f"Seized by IO {case_spec['police_station']}, logged in register.",
                }
            )

        # Cross-case connector transactions and calls:
        # P003 Vikram Rao financial connector across C101, C104, C107, C110
        for target_p in ["P001", "P007", "P012", "P028"]:
            tx_seq += 1
            self.transactions.append(
                {
                    "transaction_id": f"TX{tx_seq:05d}",
                    "timestamp": "2024-06-20 14:30:00",
                    "from_account_id": accounts_by_pid["P003"],
                    "to_account_id": accounts_by_pid[target_p],
                    "amount_inr": "150000.00",
                    "transaction_type": "RTGS",
                    "location_id": "L001",
                    "case_id": "",
                }
            )

        # P005 Imran Sheikh telecom connector across C101, C102, C103, C106, C109
        for target_p in ["P001", "P002", "P004", "P006"]:
            cdr_seq += 1
            self.cdr.append(
                {
                    "cdr_id": f"CDR{cdr_seq:05d}",
                    "timestamp": "2024-05-01 11:15:00",
                    "from_phone_id": phones_by_pid["P005"],
                    "to_phone_id": phones_by_pid[target_p],
                    "duration_seconds": "320",
                    "call_type": "VOICE",
                    "cell_location_id": "L002",
                    "case_id": "",
                }
            )

    def _build_all_dossiers(self) -> None:
        """Create the 16 required physical dossier files for each case."""
        p_by_id = {p["person_id"]: p for p in self.persons}
        ph_by_id = {p["phone_id"]: p for p in self.phones}
        ac_by_id = {a["account_id"]: a for a in self.accounts}
        v_by_id = {v["vehicle_id"]: v for v in self.vehicles}

        doc_seq = 0

        for case in MASTER_CASES:
            cid = case["case_id"]
            cnum = case["case_number"]
            title = case["title"]
            members = [p_by_id[m[0]] for m in case["members"]]
            member_names = [m["full_name"] for m in members]
            p0 = members[0]["full_name"]

            # 1. Summary
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_summary = f"documents/case_metadata/{cid}_summary.txt"
            t_summary = (
                f"CASE SUMMARY — {title}\n"
                f"Case Number: {cnum}\n"
                f"Police Station: {case['police_station']}, {case['city']}\n"
                f"Registration Date: {case['registered_date']}\n"
                f"Status: {case['status']}\n\n"
                f"Investigation Summary:\n{case['summary']}\n"
                f"Key Parties Examined: {', '.join(member_names)}.\n"
                f"This document is an official case record under the Police Regulations.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_summary, t_summary)

            # 2. FIR
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_fir = f"documents/fir/{cid}_fir.txt"
            t_fir = (
                f"FIRST INFORMATION REPORT\n"
                f"Reference: {cnum}\n"
                f"Station: {case['police_station']}\n"
                f"Date of Registration: {case['registered_date']}\n"
                f"Incident Type: {case['case_type']}\n\n"
                f"Complaint Details:\n"
                f"An offence was reported under relevant sections of the Indian Penal Code at {case['city']}.\n"
                f"Persons named in report: {', '.join(member_names[:4])}.\n"
                f"Preliminary investigation initiated under officer in charge.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_fir, t_fir)

            # 3. Case Diary
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_diary = f"documents/investigation_reports/{cid}_case_diary.txt"
            t_diary = (
                f"CASE DIARY — SECTION 172 CrPC\n"
                f"Case: {cnum} ({title})\n"
                f"Investigating Officer: Inspector In-Charge, {case['police_station']}\n\n"
                f"Entry 1 ({case['registered_date']}): Registered FIR upon receipt of verified information.\n"
                f"Entry 2: Examined scene of occurrence; questioned witnesses including {p0}.\n"
                f"Entry 3: Requisitioned CDR records and bank transaction statements.\n"
                f"Entry 4: Corroborated statements of parties and verified alibis.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_diary, t_diary)

            # 4. CDR Export
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_cdr = f"documents/cdr/{cid}_cdr.csv"
            cdr_rows = [c for c in self.cdr if c["case_id"] == cid]
            if not cdr_rows:
                # Provide at least a header row
                cdr_rows = [{"cdr_id": "CDR00000", "timestamp": "2024-01-01 00:00:00", "from_phone_id": "PH0001", "to_phone_id": "PH0002", "duration_seconds": "0", "call_type": "VOICE", "cell_location_id": "L001", "case_id": cid}]
            formatted_cdr = [
                f"calling_number,called_number,timestamp,duration_seconds,direction,imei\n"
            ] + [
                f"{ph_by_id.get(r['from_phone_id'], {}).get('phone_number', '+919800000001')},"
                f"{ph_by_id.get(r['to_phone_id'], {}).get('phone_number', '+919800000002')},"
                f"{r['timestamp']},{r['duration_seconds']},{r['call_type']},358900000000001\n"
                for r in cdr_rows
            ]
            self._register_doc(doc_id, cid, "CDR", p_cdr, "".join(formatted_cdr))

            # 5. Bank Statement
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_bank = f"documents/bank_statements/{cid}_bank_statement.csv"
            tx_rows = [t for t in self.transactions if t["case_id"] == cid]
            if not tx_rows:
                tx_rows = [{"transaction_id": "TX00000", "timestamp": "2024-01-01 00:00:00", "from_account_id": "AC0001", "to_account_id": "AC0002", "amount_inr": "1000.00", "transaction_type": "NEFT", "case_id": cid}]
            formatted_tx = [
                f"transaction_id,date,from_account,to_account,amount,channel,reference\n"
            ] + [
                f"{r['transaction_id']},{r['timestamp']},"
                f"{ac_by_id.get(r['from_account_id'], {}).get('account_number', '100000000001')},"
                f"{ac_by_id.get(r['to_account_id'], {}).get('account_number', '100000000002')},"
                f"{r['amount_inr']},{r['transaction_type']},{r['transaction_id']}\n"
                for r in tx_rows
            ]
            self._register_doc(doc_id, cid, "FINANCIAL", p_bank, "".join(formatted_tx))

            # 6. CCTV Log
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_cctv = f"documents/surveillance_logs/{cid}_cctv_log.csv"
            s_rows = [s for s in self.sightings if s["case_id"] == cid]
            formatted_cctv = ["subject,observed_at,location,vehicle,remarks\n"]
            for s in s_rows:
                veh = v_by_id.get(s["vehicle_id"], {})
                owner = p_by_id.get(veh.get("owner_person_id", ""), {})
                formatted_cctv.append(
                    f"{owner.get('full_name', 'Unknown Subject')},{s['timestamp']},"
                    f"Camera Point 1,{veh.get('registration_number', 'MH12AB1234')},CCTV visual recorded\n"
                )
            if cid == "C108":
                formatted_cctv.append(
                    "DATA_GAP,2024-10-05 01:00:00,Camera Point 3,,RECORDING INTERRUPTED - POWER DISRUPTION BETWEEN 01:00 AND 03:30\n"
                )
            self._register_doc(doc_id, cid, "SURVEILLANCE", p_cctv, "".join(formatted_cctv))

            # 7. Patrol Log
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_patrol = f"documents/field_reports/{cid}_patrol_log.txt"
            t_patrol = (
                f"POLICE PATROL LOG — {cnum}\n"
                f"Station: {case['police_station']}\n"
                f"Shift Officer: Sub-Inspector In-Charge\n\n"
                f"Patrol unit covered designated sectors near {case['city']}.\n"
                f"Observed normal vehicular traffic. Recorded sightings of commercial vehicles in sector.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_patrol, t_patrol)

            # 8. Surveillance Report
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_surv = f"documents/surveillance_logs/{cid}_surveillance_report.txt"
            t_surv = (
                f"SURVEILLANCE REPORT — {title}\n"
                f"Target Subject: {p0}\n"
                f"Surveillance Team: Special Operation Unit, {case['city']}\n\n"
                f"Team maintained static surveillance. Subject was observed meeting associates at local market.\n"
                f"Communication devices noted in active use.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_surv, t_surv)

            # 9. Witness Statements (With rich natural language variations)
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_witness = f"documents/witness_statements/{cid}_witness_statements.txt"
            t_witness = self._render_rich_witness_statements(case, members)
            self._register_doc(doc_id, cid, "FIR", p_witness, t_witness)

            # 10. Scene Report
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_scene = f"documents/investigation_reports/{cid}_scene_report.txt"
            t_scene = (
                f"SCENE OF CRIME EXAMINATION REPORT\n"
                f"Case: {cnum} ({title})\n"
                f"Date of Inspection: {case['registered_date']}\n"
                f"Location: Primary incident site, {case['city']}\n\n"
                f"Physical inspection revealed point of entry and recovery of material objects.\n"
                f"Fingerprints lifted and physical items marked as exhibits for laboratory analysis.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_scene, t_scene)

            # 11. Intel Notes
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_intel = f"documents/intel_notes/{cid}_intel_notes.txt"
            t_intel = (
                f"[SYNTHETIC] Intelligence reports — {cnum}\n\n"
                f"Report IR-{cid}-01 ({case['registered_date']})\n"
                f"Subject: {p0}\n"
                f"Location: {case['city']} Central Market\n"
                f"Source: SOURCE_REPORT\n"
                f"Summary: Confidential informant reports recurring contact between {p0} and syndicate members.\n"
            )
            self._register_doc(doc_id, cid, "INTEL", p_intel, t_intel)

            # 12. Evidence Register
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_ev = f"documents/evidence_indexes/{cid}_evidence_register.txt"
            t_ev = (
                f"EVIDENCE REGISTER — {cnum}\n"
                f"Case: {title}\n"
                f"Investigating Officer: Inspector In-Charge\n\n"
                f"Exhibit 1: Digital storage drive containing CCTV exports.\n"
                f"Exhibit 2: CDR records provided by telecom service provider.\n"
                f"Exhibit 3: Verified bank account statements and transaction receipts.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_ev, t_ev)

            # 13. Relationships Summary
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_rel = f"documents/field_reports/{cid}_relationships.txt"
            t_rel = (
                f"FIELD RELATIONSHIPS ANALYSIS — {cnum}\n\n"
                f"Analysis of observed interactions among subjects in {title}:\n"
                f"1. Primary suspect {p0} maintained frequent communication with {member_names[1] if len(member_names) > 1 else 'associates'}.\n"
                f"2. Financial linkages established via verified bank account transfers.\n"
                f"3. Decoy subjects verified with legitimate civilian explanations.\n"
            )
            self._register_doc(doc_id, cid, "INTEL", p_rel, t_rel)

            # 14. Chargesheet (Closed cases) OR Unsolved Note (Ongoing cases)
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            if case["status"] == "CLOSED":
                p_final = f"documents/charge_memos/{cid}_chargesheet.txt"
                t_final = (
                    f"CHARGE SHEET SUMMARY — SECTION 173 CrPC\n"
                    f"Case: {cnum} ({title})\n"
                    f"Court: Competent Judicial Magistrate First Class, {case['city']}\n"
                    f"Status: SOLVED / CHARGE SHEET FILED\n\n"
                    f"Accused Persons Forwarded for Trial: {', '.join(member_names[:2])}.\n"
                    f"Findings: Investigation established prima facie case against accused persons.\n"
                    f"Stolen property recovered; financial and telecom evidence corroborated.\n"
                )
            else:
                p_final = f"documents/investigator_notes/{cid}_unsolved_note.txt"
                gap_info = case.get("data_gaps", {})
                t_final = (
                    f"INVESTIGATOR NOTE — UNSOLVED / ONGOING CASE\n"
                    f"Case: {cnum} ({title})\n"
                    f"Status: UNDER INVESTIGATION (UNSOLVED)\n\n"
                    f"Investigation Status Report:\n"
                    f"The investigation into {title} remains open and unsolved due to critical data gaps.\n"
                    f"Identified Data Gaps:\n"
                    f"- CDR Gap: {gap_info.get('cdr_gap', 'Telecom records incomplete during incident window.')}\n"
                    f"- CCTV Gap: {gap_info.get('cctv_gap', 'Surveillance video unavailable or disrupted.')}\n"
                    f"Outstanding Leads: Alibis of examined persons remain uncorroborated. No charge sheet filed.\n"
                )
            self._register_doc(doc_id, cid, "CRIMINAL_HISTORY" if case["status"] == "CLOSED" else "FIR", p_final, t_final)

            # 15. Complete PDF Dossier
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_pdf = f"documents/dossiers/{cid}_complete_dossier.pdf"
            pdf_bytes = self._generate_pdf_dossier(case, members)
            self._register_doc(doc_id, cid, "FIR", p_pdf, pdf_bytes, is_binary=True)

            # 16. Case Document Index
            doc_seq += 1
            doc_id = f"DOC{doc_seq:05d}"
            p_idx = f"documents/case_metadata/{cid}_document_index.txt"
            dossier_list_str = "\n".join(f"  - {df}" for df in [
                p_summary, p_fir, p_diary, p_cdr, p_bank, p_cctv,
                p_patrol, p_surv, p_witness, p_scene, p_intel, p_ev,
                p_rel, p_final, p_pdf
            ])
            t_idx = (
                f"DOCUMENT DOSSIER INDEX — {cid}\n"
                f"Case Number: {cnum}\n"
                f"Case Title: {title}\n"
                f"Registered Date: {case['registered_date']}\n"
                f"Police Station: {case['police_station']}\n\n"
                f"Physical Files in Dossier:\n"
                f"{dossier_list_str}\n\n"
                f"Total Physical Records: 15 case documents + 1 index = 16 records.\n"
            )
            self._register_doc(doc_id, cid, "FIR", p_idx, t_idx)

    def _render_rich_witness_statements(self, case: dict, members: list[dict]) -> str:
        """Inject realistic linguistic variations and negative controls."""
        cid = case["case_id"]
        names = [m["full_name"] for m in members]
        lines = [
            f"EXAMINATION OF WITNESSES UNDER SECTION 161 CrPC",
            f"Case: {case['case_number']} — {case['title']}",
            f"Police Station: {case['police_station']}\n",
        ]

        # Case specific natural language scenarios
        if cid == "C101":
            lines.extend([
                "Statement of Sri Ravi Mehta (Eyewitness / Security Guard):",
                "Question: Are Harish Varma and Suresh Patil associates of Rajesh Sharma?",
                "Answer: I saw Suresh Patil entering the branch premises with Rajesh Sharma on the morning of the incident.",
                "Investigators later linked the payment to Vikram Rao. Vikram Rao, also known as 'Vicky', was seen in the silver sedan.",
                "Vikram Rao's account was reportedly credited shortly after the incident.",
                "Rao was seen near the warehouse earlier that week.",
                "Regarding Anand Kulkarni: Sri Anand Kulkarni was the on-duty bank teller. Kulkarni performed routine cash counter receipts as part of his authorized employment duties, having no involvement in the robbery.",
            ])
        elif cid == "C102":
            lines.extend([
                "Statement of Sri Nikhil Saxena (Driver):",
                "Question: Did Suresh Patil instruct you regarding the transport route?",
                "Answer: Mr. Suresh Patil hired the freight truck. Later, Harish Varma of Apex Logistics coordinated the dispatch.",
                "Varma was accompanied by Imran Sheikh, also known as 'Immu'.",
                "Regarding Ramesh Yadav: Ramesh Yadav is the licensed mechanic at Highway Auto Works. Yadav serviced the commercial vehicle under standard job card JC-8821 for brake calibration; he had no knowledge of any theft syndicate.",
            ])
        elif cid == "C103":
            lines.extend([
                "Statement of Smt. Sana Iyer (Complainant & Retail Merchant):",
                "I operate my retail storefront at Market Yard. Imran Sheikh, known locally as 'Immu', approached my premises demanding illicit protection money.",
                "Sana Iyer's accounts and mobile phone records show repeated threatening calls from Sheikh.",
                "Imran Sheikh's phone was traced to the Market Yard tower location.",
                "The same Sheikh was identified by witness Arjun Bhat.",
            ])
        elif cid == "C104":
            lines.extend([
                "Statement of Investigator regarding Financial Ledger Analysis:",
                "Question: Are Harish Varma and Deepak Nair party to the transfer chain?",
                "Ledger examination reveals funds routed from Vikram Rao's account to intermediary accounts.",
                "Sri Vikram Rao authorized transfers to Asha Nair and Deepak Nair.",
                "Regarding Sunita Verma: Smt. Sunita Verma is a legitimate commercial vendor supplying corrugated boxes under tax invoice INV-4491. The payment of INR 45,000 received by Verma Packaging Solutions was legitimate vendor compensation for packaging goods, with zero connection to fraudulent proceeds.",
            ])
        elif cid == "C108":
            lines.extend([
                "Statement of Sri Sanjay Mishra (Warehouse Supervisor):",
                "On the night of 2024-10-04, logistics consignment breach occurred at Sector 8 Warehouse.",
                "CCTV camera 3 was disabled between 01:00 and 03:30 due to a power outage.",
                "Furthermore, local cell tower sector L008 was non-operational for maintenance.",
                "Due to absence of corroborative technical evidence, the case remains unsolved.",
            ])
        else:
            p0 = names[0]
            lines.extend([
                f"Statement of witness regarding {case['title']}:",
                f"I am familiar with Sri {p0}. {p0}'s activities were observed at the location.",
                f"Investigators reviewed the matter thoroughly.",
            ])

        return "\n\n".join(lines) + "\n"

    def _generate_pdf_dossier(self, case: dict, members: list[dict]) -> bytes:
        """Generate a valid, multi-page PDF document using ReportLab."""
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib import colors

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "CaseTitle",
            parent=styles["Heading1"],
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#1e293b"),
            spaceAfter=10,
        )
        body_style = ParagraphStyle(
            "CaseBody",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#334155"),
            spaceAfter=8,
        )

        elements = []
        elements.append(Paragraph(f"POLICE INVESTIGATION DOSSIER", title_style))
        elements.append(Paragraph(f"Case Reference: {case['case_number']} — {case['title']}", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        # Case Details Table
        details_data = [
            ["Case Number", case["case_number"], "Registration Date", case["registered_date"]],
            ["Police Station", case["police_station"], "City / State", f"{case['city']}, {case['state']}"],
            ["Case Type", case["case_type"], "Case Status", case["status"]],
        ]
        t = Table(details_data, colWidths=[110, 150, 110, 150])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 15))

        elements.append(Paragraph("<b>Case Narrative & Summary of Findings:</b>", body_style))
        elements.append(Paragraph(case["summary"], body_style))
        elements.append(Spacer(1, 10))

        elements.append(Paragraph("<b>Examined Subjects and Roles:</b>", body_style))
        member_table_data = [["Person ID", "Full Name", "Gender", "City", "Role / Relationship"]]
        for m, role in case["members"]:
            p = next((x for x in members if x["person_id"] == m), None)
            if p:
                member_table_data.append([p["person_id"], p["full_name"], p["gender"], p["city"], role])

        mt = Table(member_table_data, colWidths=[65, 140, 55, 90, 170])
        mt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ]))
        elements.append(mt)
        elements.append(Spacer(1, 15))

        elements.append(Paragraph("<b>Investigative Remarks:</b>", body_style))
        if case["status"] == "CLOSED":
            elements.append(Paragraph("All investigative leads completed. Corroborative technical evidence verified. Formal charge sheet prepared for judicial proceedings.", body_style))
        else:
            elements.append(Paragraph(f"Investigation remains ongoing. Unsolved status maintained. Technical data gaps: {case.get('data_gaps', {}).get('cctv_gap', 'None')}", body_style))

        elements.append(Spacer(1, 20))
        elements.append(Paragraph("OFFICIAL RECORD — CRIMELINK SYNTHETIC EVALUATION DATASET", styles["Italic"]))

        doc.build(elements)
        return buffer.getvalue()

    def _register_doc(
        self,
        doc_id: str,
        case_id: str,
        doc_type: str,
        file_path: str,
        content: str | bytes,
        is_binary: bool = False,
    ) -> None:
        self.documents.append(
            {
                "document_id": doc_id,
                "case_id": case_id,
                "document_type": doc_type,
                "file_path": file_path,
                "language": "en",
                "source_environment": "synthetic",
            }
        )
        self._doc_files[file_path] = content
        self._doc_index.append(
            {
                "document_id": doc_id,
                "case_id": case_id,
                "document_type": doc_type,
                "file_path": file_path,
                "byte_size": len(content) if is_binary else len(content.encode("utf-8")),
            }
        )

    def _build_ground_truth(self) -> None:
        """Construct ground-truth evaluation datasets."""
        # 1. Bridge people
        self.gt_bridge_people = [
            {
                "person_id": "P003",
                "name": "Vikram Rao",
                "alias": "Vicky",
                "cases": ["C101", "C104", "C107", "C110"],
                "role": "Financial and Document Bridge",
                "analytical_target": "Weighted Betweenness Centrality",
            },
            {
                "person_id": "P005",
                "name": "Imran Sheikh",
                "alias": "Immu",
                "cases": ["C101", "C102", "C103", "C106", "C109"],
                "role": "Telecom and Cross-Case Communication Connector",
                "analytical_target": "Cross-case Degree and Network Hub",
            },
        ]

        # 2. Negative controls
        self.gt_negative_controls = [
            {
                "person_id": "P010",
                "name": "Anand Kulkarni",
                "case_id": "C101",
                "observed_association": "Central Bank Cash Counter",
                "legitimate_explanation": "Authorized senior bank teller executing official banking counter duties; no criminal conspiracy.",
                "expected_classification": "LEGITIMATE_CIVILIAN",
            },
            {
                "person_id": "P011",
                "name": "Ramesh Yadav",
                "case_id": "C102",
                "observed_association": "Commercial Freight Truck Maintenance",
                "legitimate_explanation": "Licensed automotive mechanic servicing vehicle under valid job card JC-8821; no criminal conspiracy.",
                "expected_classification": "LEGITIMATE_CIVILIAN",
            },
            {
                "person_id": "P014",
                "name": "Sunita Verma",
                "case_id": "C104",
                "observed_association": "Commercial Invoiced Bank Transfer (INR 45,000)",
                "legitimate_explanation": "Registered supplier of corrugated packaging materials to Apex Logistics under invoice INV-4491; commercial vendor.",
                "expected_classification": "LEGITIMATE_CIVILIAN",
            },
        ]

        # 3. Data gaps (C108)
        self.gt_data_gaps = [
            {
                "case_id": "C108",
                "domain": "CCTV",
                "description": "Camera 3 power disruption and footage corruption between 01:00:00 and 03:30:00 on 2024-10-05.",
                "status": "UNRESOLVED_GAP",
            },
            {
                "case_id": "C108",
                "domain": "CDR",
                "description": "Cell tower sector L008 maintenance downtime between 00:45:00 and 04:00:00; tower dump unavailable.",
                "status": "UNRESOLVED_GAP",
            },
        ]

        # 4. Hidden networks
        self.gt_hidden_networks = [
            {
                "network_id": "NET-ROBBERY-EXTORTION",
                "cases": ["C101", "C102", "C103", "C106"],
                "key_members": ["P001", "P002", "P004", "P005"],
            },
            {
                "network_id": "NET-FINANCIAL-FORGERY",
                "cases": ["C101", "C104", "C107", "C110"],
                "key_members": ["P003", "P007", "P012", "P017"],
            },
        ]

        # 5. Money cycles
        self.gt_money_cycles = [
            {
                "cycle_id": "MC001",
                "case_id": "C104",
                "accounts": ["100000000003", "100000000007", "100000000017", "100000000003"],
                "note": "Circular money routing through intermediate mule accounts.",
            }
        ]

        # 6. Verified relationships ground truth
        for case in MASTER_CASES:
            members = [m[0] for m in case["members"]]
            for i in range(len(members)):
                for j in range(i + 1, min(i + 3, len(members))):
                    self.gt_relationships.append(
                        {
                            "source_person_id": members[i],
                            "target_person_id": members[j],
                            "relationship_type": "ASSOCIATE_OF",
                            "case_id": case["case_id"],
                        }
                    )

    def write(self) -> None:
        """Write all CSVs, document files, indices, and ground truth to disk."""
        root = self.root
        if root.exists():
            shutil.rmtree(root)

        op_dir = root / OPERATIONAL_DIR
        doc_dir = root / DOCUMENTS_DIR
        gt_dir = root / GROUND_TRUTH_DIR
        meta_dir = root / METADATA_DIR

        for d in [op_dir, doc_dir, gt_dir, meta_dir]:
            d.mkdir(parents=True, exist_ok=True)

        def _write_csv(filename: str, rows: list[dict], fields: list[str]) -> None:
            p = op_dir / filename
            with p.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for r in rows:
                    writer.writerow({k: r.get(k, "") for k in fields})

        # Operational CSVs (standard canonical tables)
        _write_csv("cases.csv", self.cases, ["case_id", "case_number", "registered_date", "case_type", "police_station", "city", "status"])
        _write_csv("persons.csv", self.persons, ["person_id", "full_name", "gender", "dob", "address", "city", "state", "status"])
        _write_csv("phones.csv", self.phones, ["phone_id", "phone_number", "owner_person_id", "status", "source"])
        _write_csv("vehicles.csv", self.vehicles, ["vehicle_id", "registration_number", "vehicle_type", "owner_person_id", "color"])
        _write_csv("accounts.csv", self.accounts, ["account_id", "account_number", "holder_person_id", "bank_code", "account_status"])
        _write_csv("locations.csv", self.locations, ["location_id", "name", "city", "state", "latitude", "longitude"])
        _write_csv("organizations.csv", self.organizations, ["organization_id", "name", "organization_type", "city", "state"])
        _write_csv("case_members.csv", self.case_members, ["case_member_id", "case_id", "person_id", "role"])
        _write_csv("person_organizations.csv", self.person_orgs, ["person_org_id", "person_id", "organization_id", "role", "start_date", "end_date"])
        _write_csv("cdr.csv", self.cdr, ["cdr_id", "timestamp", "from_phone_id", "to_phone_id", "duration_seconds", "call_type", "cell_location_id", "case_id"])
        _write_csv("transactions.csv", self.transactions, ["transaction_id", "timestamp", "from_account_id", "to_account_id", "amount_inr", "transaction_type", "location_id", "case_id"])
        _write_csv("vehicle_sightings.csv", self.sightings, ["sighting_id", "vehicle_id", "location_id", "timestamp", "case_id", "source"])
        _write_csv("intelligence_reports.csv", self.intel, ["report_id", "report_date", "subject_person_id", "location_id", "case_id", "source_type", "summary"])
        _write_csv("documents.csv", self.documents, ["document_id", "case_id", "document_type", "file_path", "language", "source_environment"])

        # Write all physical document files
        for rel_path, content in self._doc_files.items():
            full_path = root / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                full_path.write_bytes(content)
            else:
                full_path.write_text(content, encoding="utf-8")

        def _write_json(path: Path, payload: Any) -> None:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Master DOCUMENT_INDEX.txt
        index_lines = [
            "CRIMELINK SYNTHETIC DATASET v2.1 — MASTER DOCUMENT INDEX",
            f"Total Operational Documents: {len(self.documents)}",
            f"Total Cases: {len(self.cases)}",
            "",
            "DOCUMENTS REGISTER:",
        ]
        for doc in self.documents:
            index_lines.append(f"- [{doc['document_id']}] ({doc['case_id']}) {doc['document_type']}: {doc['file_path']}")
        (root / "DOCUMENT_INDEX.txt").write_text("\n".join(index_lines), encoding="utf-8")
        _write_json(meta_dir / "document_index.json", self._doc_index)

        # Ground Truth files (evaluation only)

        _write_json(gt_dir / "canonical_registry.json", {"persons": self.persons, "organizations": self.organizations, "locations": self.locations})
        _write_json(gt_dir / "relationship_ground_truth.json", self.gt_relationships)
        _write_json(gt_dir / "bridge_people.json", self.gt_bridge_people)
        _write_json(gt_dir / "negative_controls.json", self.gt_negative_controls)
        _write_json(gt_dir / "data_gaps.json", self.gt_data_gaps)
        _write_json(gt_dir / "hidden_networks.json", self.gt_hidden_networks)
        _write_json(gt_dir / "money_cycles.json", self.gt_money_cycles)

        # Mirror ground_truth directory for backward compatibility
        gt_alt_dir = root / "ground_truth"
        if gt_alt_dir.exists():
            shutil.rmtree(gt_alt_dir)
        shutil.copytree(gt_dir, gt_alt_dir)

        # Metadata
        config = {
            "dataset_name": "CrimeLink_Synthetic_Corpus_v1",
            "dataset_version": "2.1",
            "seed": self.seed,
            "cases": len(self.cases),
            "persons": len(self.persons),
            "documents": len(self.documents),
            "operational_csvs": 17,
            "ground_truth_isolated": True,
        }
        _write_json(meta_dir / "generation_config.json", config)
        _write_json(
            meta_dir / "schema.json",
            {
                "version": "2.1",
                "structured_sources": "CSV",
                "unstructured_sources": ["TXT", "CSV", "PDF"],
                "ground_truth": "JSON (evaluation-only)",
            },
        )

        # Layout descriptor
        _write_json(
            root / "CORPUS_LAYOUT.json",
            {
                "version": "v2.1 Master Index",
                "operational": "operational/",
                "documents": "documents/",
                "ground_truth": "_ground_truth/",
                "cases_count": len(self.cases),
                "documents_count": len(self.documents),
            },
        )

        # README
        (root / "README.md").write_text(
            f"# CrimeLink Synthetic Investigation Corpus v2.1\n\n"
            f"Generated with seed: {self.seed}\n"
            f"Contains 10 cases (C101–C110) per Master Index specification.\n"
            f"All operational documents are 100% file-backed on disk.\n"
            f"Ground truth is isolated under `_ground_truth/` and `ground_truth/`.\n",
            encoding="utf-8",
        )


class Builder:
    """Compatibility interface for external corpus generation used in tests and tools."""

    def __init__(self, seed: int = DEFAULT_SEED, root: Path | str | None = None) -> None:
        self.seed = seed
        self.root = Path(root).resolve() if root else CORPUS_ROOT
        self._builder = ExternalCorpusBuilder(seed=self.seed, root=self.root)
        self.cases: list[dict] = []
        self.persons: list[dict] = []
        self.phones: list[dict] = []
        self.vehicles: list[dict] = []
        self.accounts: list[dict] = []
        self.locations: list[dict] = []
        self.organizations: list[dict] = []
        self.case_members: list[dict] = []
        self.person_orgs: list[dict] = []
        self.cdr: list[dict] = []
        self.transactions: list[dict] = []
        self.sightings: list[dict] = []
        self.intel: list[dict] = []
        self.evidence: list[dict] = []
        self.documents: list[dict] = []

    def build_case(self, index: int) -> dict:
        return {"case_id": f"C{100 + index:03d}"}

    def build_background(self) -> None:
        pass

    def write(self) -> None:
        self._builder.build()
        self._builder.write()
        self.cases = self._builder.cases
        self.persons = self._builder.persons
        self.phones = self._builder.phones
        self.vehicles = self._builder.vehicles
        self.accounts = self._builder.accounts
        self.locations = self._builder.locations
        self.organizations = self._builder.organizations
        self.case_members = self._builder.case_members
        self.person_orgs = self._builder.person_orgs
        self.cdr = self._builder.cdr
        self.transactions = self._builder.transactions
        self.sightings = self._builder.sightings
        self.intel = self._builder.intel
        self.evidence = self._builder.evidence
        self.documents = self._builder.documents


def build_external_corpus(root: Path | str | None = None, seed: int = DEFAULT_SEED) -> ExternalCorpusBuilder:
    root_path = Path(root).resolve() if root else CORPUS_ROOT
    builder = ExternalCorpusBuilder(seed=seed, root=root_path)
    builder.build()
    builder.write()
    return builder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the CrimeLink v2.1 Master Index external synthetic corpus.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--root", type=str, default=None)
    args = parser.parse_args()

    root_path = Path(args.root).resolve() if args.root else CORPUS_ROOT
    builder = ExternalCorpusBuilder(seed=args.seed, root=root_path)
    builder.build()
    builder.write()

    print(f"Corpus generated successfully at {builder.root}:")
    print(f"  Cases: {len(builder.cases)}")
    print(f"  Persons: {len(builder.persons)}")
    print(f"  Operational Documents: {len(builder.documents)}")
    print(f"  CDR rows: {len(builder.cdr)}")
    print(f"  Transactions: {len(builder.transactions)}")
    print(f"  Sightings: {len(builder.sightings)}")
    print(f"  Ground truth records: {len(builder.gt_relationships)} relationships, {len(builder.gt_bridge_people)} bridges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
