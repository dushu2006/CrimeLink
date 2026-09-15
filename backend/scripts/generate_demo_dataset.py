"""
Generate canonical demo dataset package with real valid files.
Creates demo_dataset/ with manifest, cases, evidence, sources.
Ensures unique SHA-256 per file to satisfy PG unique constraint (case_id, content_hash)
"""
import json
import hashlib
import csv
import io
import os
from pathlib import Path
from datetime import datetime, timedelta
import random

# Ensure reportlab available
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
except ImportError:
    raise ImportError("reportlab required: pip install reportlab")

# Paths
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DEMO_ROOT = REPO_ROOT / "demo_dataset"
DEMO_ROOT_BACKEND = BACKEND_ROOT / "demo_dataset"

for p in [DEMO_ROOT, DEMO_ROOT_BACKEND]:
    p.mkdir(parents=True, exist_ok=True)

# Faker-like names (deterministic)
random.seed(42)

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Sai", "Arjun", "Reyansh", "Mohammed", "Atharv", "Ayaan", "Krishna",
               "Saanvi", "Ananya", "Diya", "Pari", "Ishani", "Anvi", "Prisha", "Myra", "Sara", "Avni",
               "Rahul", "Amit", "Suresh", "Ramesh", "Vikram", "Sanjay", "Anil", "Sunil", "Rajesh", "Manoj",
               "Priya", "Sunita", "Meena", "Kavita", "Pooja", "Neha", "Anjali", "Shalini", "Rekha", "Suman",
               "Imran", "Salman", "Arif", "Farhan", "Zubair", "Aftab", "Rizwan", "Shahid", "Irfan", "Javed",
               "Lakshmi", "Karthik", "Srinivas", "Ravi", "Murugan", "Kumar", "Babu", "Selva", "Mohan", "Hari"]

LAST_NAMES = ["Sharma", "Verma", "Yadav", "Singh", "Rathore", "Mehta", "Kumar", "Patel", "Shah", "Gupta",
              "Jain", "Reddy", "Nair", "Menon", "Pillai", "Rao", "Desai", "Khan", "Ali", "Ahmed",
              "Choudhary", "Mishra", "Tiwari", "Pandey", "Srivastava", "Chauhan", "Malhotra", "Kapoor", "Arora", "Bhatt"]

def indian_name(idx):
    fn = FIRST_NAMES[idx % len(FIRST_NAMES)]
    ln = LAST_NAMES[(idx * 7) % len(LAST_NAMES)]
    return f"{fn} {ln}"

def generate_pdf_bytes(title, lines):
    """Generate a valid PDF using reportlab."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 14)
    c.drawString(inch, height - inch, title)
    c.setFont("Helvetica", 10)
    y = height - inch - 30
    for line in lines:
        if y < inch:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - inch
        # Wrap long lines
        if len(line) > 100:
            for i in range(0, len(line), 100):
                c.drawString(inch, y, line[i:i+100])
                y -= 14
                if y < inch:
                    c.showPage()
                    c.setFont("Helvetica", 10)
                    y = height - inch
        else:
            c.drawString(inch, y, line)
            y -= 14
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

def generate_csv_bytes(headers, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')

# Define cases
CASES = []
for i in range(20):
    case_num = f"CR-{1024+i}"
    case_id = f"case-{i+1:03d}-demo"
    case_key = f"CASE-{i+1:03d}"
    purposes = [
        "Operation Cross-Link — Communication Network Analysis (Hero Case)",
        "Financial Trail — Shared Resources Investigation",
        "Co-Location Pattern — Surveillance Evidence",
        "Common Contact Network — Association Analysis",
        "Repeated Communication — Pattern Detection",
        "Cross-Case Link — Person Overlap Investigation",
        "Timeline-Heavy Investigation — Event Reconstruction",
        "Sparse Network — Limited Evidence Case",
        "Dense Network — Multiple Associations",
        "Conflicting Evidence — Contradiction Analysis",
        "Weak Evidence / Inference — Insufficient Support",
        "Missing Provenance — Incomplete Records",
        "No Supported Connection — Negative Control",
        "Financial Association — Transaction Trail",
        "Multi-Hop Investigation — 2-Hop Relationship",
        "Multi-Hop Investigation — 3-Hop Relationship",
        "Multi-Hop Investigation — 4-Hop Relationship",
        "Mixed Relationship Types — Comprehensive Analysis",
        "Completed Investigation Review — Viewer Validation",
        "Cross-Jurisdiction Link — Jurisdiction Overlap"
    ]
    CASES.append({
        "id": case_id,
        "case_number": case_num,
        "case_key": case_key,
        "title": purposes[i],
        "jurisdiction_id": "DEMO-JURISDICTION",
        "status": "OPEN",
        "classification": "CONFIDENTIAL" if i < 10 else "INTERNAL",
        "description": f"Investigation case {case_num}: {purposes[i]}"
    })

# Define people
PEOPLE = []
for i in range(1, 101):
    pid = f"PERSON-{i:03d}"
    name = indian_name(i)
    role = ["Subject", "Associate", "Witness", "Contact", "Person of Interest"][i % 5]
    PEOPLE.append({
        "id": pid,
        "name": name,
        "label": "Person",
        "role": role,
        "age": 20 + (i % 45),
        "properties": {
            "role": role,
            "age": 20 + (i % 45),
            "display_name": name,
            "full_name": name
        }
    })

# Assign people to cases with overlaps
CASE_PEOPLE = {}
for idx, case in enumerate(CASES):
    case_id = case["id"]
    count = 6 + (idx % 7)
    start = (idx * 5) % 100
    people_ids = []
    for j in range(count):
        p_idx = (start + j) % 100
        people_ids.append(f"PERSON-{p_idx+1:03d}")
    if idx == 0:
        people_ids = ["PERSON-001", "PERSON-002", "PERSON-003", "PERSON-004", "PERSON-005", "PERSON-009", "PERSON-015", "PERSON-020"]
    if idx == 5:
        people_ids = ["PERSON-001", "PERSON-010", "PERSON-015", "PERSON-025", "PERSON-030", "PERSON-040"]
    if idx == 11:
        people_ids = ["PERSON-001", "PERSON-050", "PERSON-051", "PERSON-052", "PERSON-053"]
    if idx == 12:
        people_ids = ["PERSON-080", "PERSON-081"]
    CASE_PEOPLE[case_id] = list(set(people_ids))

for extra_case in ["case-002-demo", "case-006-demo", "case-012-demo"]:
    if "PERSON-001" not in CASE_PEOPLE[extra_case]:
        CASE_PEOPLE[extra_case].append("PERSON-001")

REL_TYPES = [
    "CALLED", "PARTICIPATED_IN", "USES_PHONE", "OWNS_VEHICLE", "OWNS_ACCOUNT",
    "MEMBER_OF", "ASSOCIATE_OF", "RELATIVE_OF", "ARRESTED_WITH", "NAMED_ACCOMPLICE_OF",
    "TRANSFER_TO", "SHARED_PHONE", "SHARED_ACCOUNT", "SHARED_VEHICLE", "SHARED_LOCATION",
    "LOCATED_AT"
]

RELATIONSHIPS = []
def add_rel(source, target, rel_type, case_id, evidence_ids, confidence=0.85, props=None):
    props = props or {}
    rel_id = f"{source}-{rel_type}-{target}-{case_id}"
    RELATIONSHIPS.append({
        "id": rel_id,
        "source": source,
        "target": target,
        "rel_type": rel_type,
        "case_id": case_id,
        "confidence": confidence,
        "evidence": evidence_ids[0] if evidence_ids else "E-001",
        "evidence_list": evidence_ids,
        "properties": props
    })

add_rel("PERSON-001", "PERSON-002", "CALLED", "case-001-demo", ["E-042", "E-103"], 0.92, {"display_type": "COMMUNICATION", "call_count": 5, "duration_s": 635})
add_rel("PERSON-001", "PERSON-003", "PARTICIPATED_IN", "case-001-demo", ["E-071"], 0.85, {"display_type": "CO_LOCATION"})
add_rel("PERSON-002", "PERSON-004", "PARTICIPATED_IN", "case-001-demo", ["E-118"], 0.78, {"display_type": "SHARED_EVENT"})
add_rel("PERSON-002", "PERSON-003", "ASSOCIATE_OF", "case-001-demo", ["E-103"], 0.71, {"display_type": "COMMON_CONTACT"})
add_rel("PERSON-001", "PERSON-005", "SHARED_LOCATION", "case-001-demo", ["E-042", "E-071"], 0.88)
add_rel("PERSON-002", "PERSON-005", "PARTICIPATED_IN", "case-001-demo", ["E-103"], 0.82)
add_rel("PERSON-005", "PERSON-009", "CALLED", "case-001-demo", ["E-103", "E-118"], 0.79)
add_rel("PERSON-009", "PERSON-015", "ASSOCIATE_OF", "case-001-demo", ["E-118"], 0.75)
add_rel("PERSON-015", "PERSON-020", "SHARED_LOCATION", "case-001-demo", ["E-042"], 0.70)

evidence_counter = 1
for case_idx, case in enumerate(CASES):
    case_id = case["id"]
    if case_id == "case-001-demo":
        continue
    people = CASE_PEOPLE[case_id]
    if case["case_number"] == "CR-1036":
        rel_count = 2
    elif case["case_number"] == "CR-1037":
        rel_count = 30
    elif case["case_number"] == "CR-1032":
        rel_count = 0
    else:
        rel_count = 8 + (case_idx % 10)

    if rel_count == 0:
        continue

    if case["case_number"] in ["CR-1038", "CR-1039", "CR-1040"]:
        hop_map = {"CR-1038": 2, "CR-1039": 3, "CR-1040": 4}
        hops = hop_map[case["case_number"]]
        chain_people = people[:hops+1]
        for h in range(hops):
            src = chain_people[h]
            tgt = chain_people[h+1]
            ev_id = f"E-{evidence_counter:03d}"
            evidence_counter += 1
            add_rel(src, tgt, "CALLED" if h % 2 == 0 else "ASSOCIATE_OF", case_id, [ev_id], 0.80 + (h*0.02))
        continue

    for r in range(rel_count):
        if len(people) < 2:
            break
        src = people[r % len(people)]
        tgt = people[(r+1) % len(people)]
        if src == tgt:
            tgt = people[(r+2) % len(people)]
        rel_type = REL_TYPES[(case_idx + r) % len(REL_TYPES)]
        ev_id = f"E-{evidence_counter:03d}"
        evidence_counter += 1
        if case["case_number"] == "CR-1028":
            rel_type = "CALLED"
        if case["case_number"] == "CR-1025" and r % 3 == 0:
            rel_type = "SHARED_ACCOUNT"
        if case["case_number"] in ["CR-1025", "CR-1037"] and r % 4 == 0:
            rel_type = "TRANSFER_TO"
        if case["case_number"] == "CR-1026" and r % 2 == 0:
            rel_type = "LOCATED_AT"
        add_rel(src, tgt, rel_type, case_id, [ev_id], 0.60 + random.random()*0.35)

EVIDENCE = []
hero_evidence_defs = [
    {"evidence_id": "E-042", "case_id": "case-001-demo", "filename": "E-042_communication_record.pdf", "storage_key": "evidence/E-042/original.pdf", "mime_type": "application/pdf", "document_type": "CDR", "title": "Communication Record E-042"},
    {"evidence_id": "E-103", "case_id": "case-001-demo", "filename": "E-103_call_log.pdf", "storage_key": "evidence/E-103/original.pdf", "mime_type": "application/pdf", "document_type": "CDR", "title": "Call Log E-103"},
    {"evidence_id": "E-071", "case_id": "case-002-demo", "filename": "E-071_location_record.pdf", "storage_key": "evidence/E-071/original.pdf", "mime_type": "application/pdf", "document_type": "SCENE_REPORT", "title": "Location Record E-071"},
    {"evidence_id": "E-118", "case_id": "case-003-demo", "filename": "E-118_surveillance_log.pdf", "storage_key": "evidence/E-118/original.pdf", "mime_type": "application/pdf", "document_type": "SURVEILLANCE_REPORT", "title": "Surveillance Log E-118"},
    {"evidence_id": "S-001", "case_id": "case-001-demo", "filename": "S-001_source_document.pdf", "storage_key": "sources/S-001/source-document.pdf", "mime_type": "application/pdf", "document_type": "FIR", "title": "Source Document S-001"},
]

all_ev_ids = set()
for rel in RELATIONSHIPS:
    for ev in rel["evidence_list"]:
        all_ev_ids.add(ev)
for he in hero_evidence_defs:
    all_ev_ids.add(he["evidence_id"])

while len(all_ev_ids) < 300:
    ev_id = f"E-{len(all_ev_ids)+1:03d}"
    if ev_id not in all_ev_ids:
        all_ev_ids.add(ev_id)

sorted_ev_ids = sorted(all_ev_ids, key=lambda x: int(x.split('-')[1]) if '-' in x and x.split('-')[1].isdigit() else 9999)

ev_to_case = {}
for rel in RELATIONSHIPS:
    for ev in rel["evidence_list"]:
        ev_to_case[ev] = rel["case_id"]
for he in hero_evidence_defs:
    ev_to_case[he["evidence_id"]] = he["case_id"]

for ev_id in sorted_ev_ids:
    if ev_id not in ev_to_case:
        case = CASES[hash(ev_id) % len(CASES)]
        ev_to_case[ev_id] = case["id"]

for ev_id in sorted_ev_ids:
    hero_match = next((h for h in hero_evidence_defs if h["evidence_id"] == ev_id), None)
    if hero_match:
        EVIDENCE.append(hero_match)
    else:
        case_id = ev_to_case[ev_id]
        num = int(ev_id.split('-')[1]) if ev_id.startswith('E-') and ev_id.split('-')[1].isdigit() else 0
        if num % 5 == 0:
            ext = "csv"
            mime = "text/csv"
            doc_type = "CDR" if num % 2 == 0 else "FINANCIAL"
            filename = f"{ev_id}_call_record.csv" if doc_type == "CDR" else f"{ev_id}_transaction_report.csv"
            storage_key = f"evidence/{ev_id}/original.{ext}"
            title = f"Call Record {ev_id}" if doc_type == "CDR" else f"Transaction Report {ev_id}"
        else:
            ext = "pdf"
            mime = "application/pdf"
            doc_types = ["FIR", "CHARGE_SHEET", "SURVEILLANCE_REPORT", "WITNESS_STATEMENT", "SCENE_REPORT", "INTELLIGENCE_REPORT", "FINANCIAL", "EVIDENCE"]
            doc_type = doc_types[num % len(doc_types)]
            filename = f"{ev_id}_{doc_type.lower()}.pdf"
            storage_key = f"evidence/{ev_id}/original.{ext}"
            title = f"{doc_type} {ev_id}"
        EVIDENCE.append({
            "evidence_id": ev_id,
            "case_id": case_id,
            "filename": filename,
            "storage_key": storage_key,
            "mime_type": mime,
            "document_type": doc_type,
            "title": title
        })

SOURCES = []
for i in range(1, 121):
    s_id = f"S-{i:03d}"
    case = CASES[i % len(CASES)]
    filename = f"{s_id}_source_document.pdf"
    storage_key = f"sources/{s_id}/source-document.pdf"
    SOURCES.append({
        "source_id": s_id,
        "case_id": case["id"],
        "filename": filename,
        "storage_key": storage_key,
        "mime_type": "application/pdf",
        "document_type": "FIR" if i % 3 == 0 else "WITNESS_STATEMENT" if i % 3 == 1 else "INTELLIGENCE_REPORT",
        "title": f"Source Document {s_id}"
    })

TIMELINE = []
event_counter = 1
for case in CASES:
    case_id = case["id"]
    people = CASE_PEOPLE[case_id]
    if case["case_number"] == "CR-1030":
        ev_count = 40
    elif case["case_number"] == "CR-1032":
        ev_count = 1
    else:
        ev_count = 8 + (hash(case_id) % 10)
    for e in range(ev_count):
        event_id = f"EVENT-{event_counter:04d}"
        event_counter += 1
        ts = datetime(2026, 1, 1) + timedelta(days=(hash(event_id) % 200), hours=(hash(event_id) % 24))
        participants = [people[e % len(people)]] if people else []
        if len(people) > 1 and e % 2 == 0:
            participants.append(people[(e+1) % len(people)])
        TIMELINE.append({
            "id": event_id,
            "case_id": case_id,
            "event_type": ["MEETING", "CALL", "TRANSACTION", "SURVEILLANCE", "INCIDENT"][e % 5],
            "title": f"Event {event_id} — {case['case_number']}",
            "timestamp": ts.isoformat(),
            "location": f"Location {e+1} — {case['case_number']}",
            "participants": participants,
            "description": f"Timeline event {event_id} for case {case['case_number']} involving {', '.join(participants)}"
        })

INVESTIGATIONS = []
INVESTIGATIONS.append({
    "id": "INV-0042",
    "case_id": "case-001-demo",
    "case_number": "CR-1024",
    "investigator_id": "demo-investigator-0001",
    "investigator_badge": "DEMO-INVESTIGATOR",
    "investigator_name": "Demo Investigator",
    "subject": "PERSON-001 ↔ PERSON-002",
    "finding": "Supported communication relationship",
    "finding_type": "COMMUNICATION",
    "evidence": ["E-042", "E-103", "E-118"],
    "evidence_strength": "HIGH",
    "classification": "FACT",
    "confidence": 0.92,
    "confidence_band": "HIGH",
    "completed_at": "2026-09-15T10:30:00",
    "connection_path": ["PERSON-001", "PERSON-002"],
    "limitations": ["Purpose of association beyond documented records is unknown", "Criminal intent not established by this evidence alone"],
    "provenance": "Verified from CDR and field reports",
    "entity_keys": ["PERSON-001", "PERSON-002"],
    "status": "CONFIRMED",
    "narrative": "Investigation of communication between PERSON-001 and PERSON-002 reveals 5 calls over 3 days, supported by CDR records E-042 and E-103, and corroborated by surveillance log E-118 showing co-location.",
    "reason": "Multiple independent sources converge on direct communication"
})

for i in range(2, 11):
    case = CASES[i % len(CASES)]
    inv_id = f"INV-{42+i:04d}"
    people = CASE_PEOPLE[case["id"]]
    subject = f"{people[0]} ↔ {people[1]}" if len(people) >=2 else people[0] if people else "UNKNOWN"
    INVESTIGATIONS.append({
        "id": inv_id,
        "case_id": case["id"],
        "case_number": case["case_number"],
        "investigator_id": "demo-investigator-0001",
        "investigator_badge": "DEMO-INVESTIGATOR",
        "investigator_name": "Demo Investigator",
        "subject": subject,
        "finding": f"Investigation finding for {case['case_number']}",
        "finding_type": ["COMMUNICATION", "CO_LOCATION", "FINANCIAL", "SHARED_EVENT"][i % 4],
        "evidence": [f"E-{(i*10+j):03d}" for j in range(1,4)],
        "evidence_strength": ["HIGH", "MODERATE", "WEAK"][i % 3],
        "classification": ["FACT", "INFERENCE", "HYPOTHESIS"][i % 3],
        "confidence": 0.6 + (i % 4)*0.1,
        "confidence_band": ["HIGH", "MEDIUM", "LOW"][i % 3],
        "completed_at": (datetime(2026, 9, 15) - timedelta(days=i)).isoformat(),
        "connection_path": people[:2],
        "limitations": ["Limited evidence available"] if i % 3 == 0 else ["Co-location does not establish intent"],
        "provenance": "Verified from multiple sources",
        "entity_keys": people[:2],
        "status": "CONFIRMED" if i % 2 == 0 else "NEW",
        "narrative": f"Investigation {inv_id} for case {case['case_number']} found association between {subject}",
        "reason": "Evidence convergence from multiple records"
    })

MANIFEST = {
    "name": "CrimeLink Production Demo Dataset v1",
    "version": "1.0",
    "created_at": datetime.utcnow().isoformat(),
    "description": "Production-style persistent demo with 20 interconnected cases",
    "counts": {
        "cases": len(CASES),
        "people": len(PEOPLE),
        "relationships": len(RELATIONSHIPS),
        "evidence": len(EVIDENCE),
        "sources": len(SOURCES),
        "timeline_events": len(TIMELINE),
        "investigations": len(INVESTIGATIONS)
    },
    "hero_case": "CR-1024",
    "cases": [c["case_number"] for c in CASES]
}

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

write_json(DEMO_ROOT / "manifest.json", MANIFEST)
write_json(DEMO_ROOT_BACKEND / "manifest.json", MANIFEST)

for case in CASES:
    case_dir = DEMO_ROOT / "cases" / case["case_key"]
    case_dir_backend = DEMO_ROOT_BACKEND / "cases" / case["case_key"]
    for d in [case_dir, case_dir_backend]:
        d.mkdir(parents=True, exist_ok=True)
    case_people = CASE_PEOPLE[case["id"]]
    case_rels = [r for r in RELATIONSHIPS if r["case_id"] == case["id"]]
    case_ev = [e for e in EVIDENCE if e["case_id"] == case["id"]]
    case_tl = [t for t in TIMELINE if t["case_id"] == case["id"]]
    case_inv = [inv for inv in INVESTIGATIONS if inv["case_id"] == case["id"]]

    write_json(case_dir / "case.json", case)
    write_json(case_dir_backend / "case.json", case)
    write_json(case_dir / "people.json", [p for p in PEOPLE if p["id"] in case_people])
    write_json(case_dir_backend / "people.json", [p for p in PEOPLE if p["id"] in case_people])
    write_json(case_dir / "relationships.json", case_rels)
    write_json(case_dir_backend / "relationships.json", case_rels)
    write_json(case_dir / "evidence.json", case_ev)
    write_json(case_dir_backend / "evidence.json", case_ev)
    write_json(case_dir / "timeline.json", case_tl)
    write_json(case_dir_backend / "timeline.json", case_tl)
    write_json(case_dir / "investigations.json", case_inv)
    write_json(case_dir_backend / "investigations.json", case_inv)

write_json(DEMO_ROOT / "people" / "people.json", PEOPLE)
write_json(DEMO_ROOT_BACKEND / "people" / "people.json", PEOPLE)
write_json(DEMO_ROOT / "relationships.json", RELATIONSHIPS)
write_json(DEMO_ROOT_BACKEND / "relationships.json", RELATIONSHIPS)
write_json(DEMO_ROOT / "evidence" / "evidence.json", EVIDENCE)
write_json(DEMO_ROOT_BACKEND / "evidence" / "evidence.json", EVIDENCE)
write_json(DEMO_ROOT / "sources" / "sources.json", SOURCES)
write_json(DEMO_ROOT_BACKEND / "sources" / "sources.json", SOURCES)
write_json(DEMO_ROOT / "timeline.json", TIMELINE)
write_json(DEMO_ROOT_BACKEND / "timeline.json", TIMELINE)
write_json(DEMO_ROOT / "investigations.json", INVESTIGATIONS)
write_json(DEMO_ROOT_BACKEND / "investigations.json", INVESTIGATIONS)

def ensure_evidence_files():
    for ev in EVIDENCE:
        storage_key = ev["storage_key"]
        for base in [DEMO_ROOT, DEMO_ROOT_BACKEND]:
            file_path = base / storage_key
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists():
                # Remove if it's old duplicate hash file — regenerate to ensure uniqueness
                # Check if file is part of duplicate set: if csv, regenerate always to ensure uniqueness
                if ev["mime_type"] == "text/csv":
                    file_path.unlink()
                else:
                    continue
            if ev["mime_type"] == "text/csv":
                ev_id = ev["evidence_id"]
                # Unique per evidence_id
                if "call" in ev["filename"].lower():
                    headers = ["evidence_id", "calling_number", "called_number", "timestamp", "duration_seconds", "direction", "case_id", "unique_nonce"]
                    rows = []
                    base_hash = abs(hash(ev_id)) % 100000000
                    for i in range(10):
                        rows.append([
                            ev_id,
                            f"+9198{(base_hash+i)%100000000:08d}",
                            f"+9198{(base_hash+i+5)%100000000:08d}",
                            f"2026-02-{10+i%20:02d}T14:32:00Z",
                            str(60+i*10+base_hash%100),
                            "OUTGOING",
                            ev["case_id"],
                            f"{ev_id}-{i}-{base_hash}"
                        ])
                    content = generate_csv_bytes(headers, rows)
                else:
                    headers = ["evidence_id", "txn_id", "date", "from_account", "to_account", "amount", "ifsc", "case_id", "unique_nonce"]
                    rows = []
                    base_hash = abs(hash(ev_id)) % 100000000
                    for i in range(15):
                        rows.append([
                            ev_id,
                            f"TXN{ev_id.replace('E-','')}-{i:04d}-{base_hash}",
                            f"2026-02-{10+i%18:02d}",
                            f"50100{(base_hash+i)%1000000:06d}",
                            f"50200{(base_hash+i)%1000000:06d}",
                            str(10000+i*500+base_hash%5000),
                            "SBIN0001234",
                            ev["case_id"],
                            f"{ev_id}-{i}"
                        ])
                    content = generate_csv_bytes(headers, rows)
                file_path.write_bytes(content)
            else:
                title = ev["title"]
                lines = [
                    f"CrimeLink Evidence Report — {ev['evidence_id']}",
                    f"Case: {ev['case_id']}",
                    f"Document Type: {ev['document_type']}",
                    f"Filename: {ev['filename']}",
                    f"Generated: {datetime.utcnow().isoformat()}",
                    f"Evidence Unique: {ev['evidence_id']}-{ev['case_id']}-{abs(hash(ev['evidence_id']))}",
                    "",
                    f"This is a synthetic but valid evidence document for {ev['evidence_id']}.",
                    f"It supports investigation of case {ev['case_id']}.",
                    "",
                    "Evidence Details:",
                    f"- Evidence ID: {ev['evidence_id']}",
                    f"- Case ID: {ev['case_id']}",
                    f"- Classification: CONFIDENTIAL",
                    f"- Source Confidence: VERIFIED",
                    "",
                    "Content Summary:",
                    "This document contains communication records, surveillance logs, or financial",
                    "transactions relevant to the investigation. All content is synthetic and",
                    "non-sensitive, generated for demonstration purposes.",
                    "",
                    "Provenance:",
                    "Source: CDR Database / Field Reports / Surveillance System",
                    "Verified: Yes",
                    "Chain of Custody: Maintained",
                    "",
                    "Additional Notes:",
                    "This file is a valid PDF parseable by CrimeLink Source Viewer.",
                    "It contains realistic but synthetic investigation data.",
                ]
                if ev["evidence_id"] == "E-042":
                    lines.extend([
                        "",
                        "HERO EVIDENCE — E-042",
                        "PERSON-001 ↔ PERSON-002 Communication",
                        "Timestamp: 2026-02-14 14:32:00",
                        "Duration: 127s",
                        "Location: Jaipur, Rajasthan",
                        "Evidence Strength: HIGH",
                        "Classification: FACT",
                        "Supports: Direct communication relationship",
                        "",
                        "Call Details:",
                        "Caller: PERSON-001 (Aarav Sharma)",
                        "Callee: PERSON-002 (Vivaan Verma)",
                        "Duration: 127 seconds",
                        "Tower: Jaipur Central",
                        "Verified by: CDR Database",
                    ])
                content = generate_pdf_bytes(title, lines)
                file_path.write_bytes(content)

    for src in SOURCES:
        storage_key = src["storage_key"]
        for base in [DEMO_ROOT, DEMO_ROOT_BACKEND]:
            file_path = base / storage_key
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path.exists():
                continue
            title = src["title"]
            lines = [
                f"CrimeLink Source Document — {src['source_id']}",
                f"Case: {src['case_id']}",
                f"Document Type: {src['document_type']}",
                f"Filename: {src['filename']}",
                f"Generated: {datetime.utcnow().isoformat()}",
                f"Source Unique: {src['source_id']}-{src['case_id']}-{abs(hash(src['source_id']))}",
                "",
                f"This is a synthetic but valid source document for {src['source_id']}.",
                f"It is the original source material for evidence in case {src['case_id']}.",
                "",
                "Source Details:",
                f"- Source ID: {src['source_id']}",
                f"- Case ID: {src['case_id']}",
                f"- Classification: CONFIDENTIAL",
                "",
                "Original Record:",
                "FIR No. 231/2024 — Synthetic",
                "Complainant statement — Synthetic",
                "Witness account — Synthetic",
                "Field observation — Synthetic",
                "",
                "Provenance:",
                "Origin: Police Station Records",
                "Verified: Yes",
                "Chain of Custody: Maintained",
            ]
            content = generate_pdf_bytes(title, lines)
            file_path.write_bytes(content)

ensure_evidence_files()

print(f"Generated demo dataset:")
print(f"  Cases: {len(CASES)}")
print(f"  People: {len(PEOPLE)}")
print(f"  Relationships: {len(RELATIONSHIPS)}")
print(f"  Evidence: {len(EVIDENCE)}")
print(f"  Sources: {len(SOURCES)}")
print(f"  Timeline: {len(TIMELINE)}")
print(f"  Investigations: {len(INVESTIGATIONS)}")
print(f"  Location: {DEMO_ROOT}")
print(f"  Backend copy: {DEMO_ROOT_BACKEND}")
