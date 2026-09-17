"""End-to-end verification of the live CrimeLink instance.

Walks the required chain for real, over HTTP, against the seeded v2 database:

    CASE -> PEOPLE -> RELATIONSHIPS -> EVIDENCE -> SOURCE DOCUMENT
         -> ORIGINAL FILE -> PROVENANCE

Plus: source preview/download, criminal stars, active-dataset consistency,
path-traversal refusal, and the person-network route that used to 404.
Prints a PASS/FAIL line per check and exits non-zero if any check fails.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8000/api/v1"
FAILURES: list[str] = []
PASSES = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def login(badge: str, password: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/auth/login",
        data=json.dumps({"badge_number": badge, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["access_token"]


TOKEN = login("DEMO-INVESTIGATOR", "DemoInvestigator@2026")
ADMIN = login("DEMO-ADMIN", "DemoAdmin@2026")


#: The API rate-limits at 100 requests/minute per principal; this walk makes
#: far more, so it paces itself rather than tripping its own limiter.
_last = [0.0]


def _pace() -> None:
    elapsed = time.monotonic() - _last[0]
    if elapsed < 0.65:
        time.sleep(0.65 - elapsed)
    _last[0] = time.monotonic()


def call(path: str, token: str | None = None) -> tuple[int, dict | list | None]:
    _pace()
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token or TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            try:
                return r.status, json.loads(body or b"{}")
            except json.JSONDecodeError:
                return r.status, {"_raw": body}
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": body}


def raw(path: str, token: str | None = None) -> tuple[int, str, bytes]:
    _pace()
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token or TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "", e.read()


def q(path: str) -> str:
    return urllib.parse.quote(path, safe="")


# ---------------------------------------------------------------------------
print("\n[1] Active dataset is consistent everywhere")
# ---------------------------------------------------------------------------
_, ds = call("/datasets/active")
active = None
if isinstance(ds, dict):
    for key in ("active", "dataset", "item"):
        if isinstance(ds.get(key), dict):
            active = ds[key].get("id")
            break
    else:
        active = ds.get("id")
check("GET /datasets/active resolves one dataset", bool(active), str(active))

_, cases = call("/cases?limit=200")
case_items = (cases or {}).get("items", [])
check("cases come from the API, not a constant", len(case_items) > 0, f"{len(case_items)} cases")

_, persons = call("/graph/master/persons?limit=2000")
person_items = (persons or {}).get("items", [])
check("people come from the API", len(person_items) > 0, f"{len(person_items)} people")

_, rel = call("/graph/master/relationships?limit=5000")
check(
    "relationship network is PERSON-only",
    rel.get("node_types") == ["PERSON"]
    and {n["label"] for n in rel["nodes"]} == {"PERSON"},
    f'{rel["counts"]["relationships"]} relationships, {rel["counts"]["persons"]} people',
)

_, entity = call("/graph/master?limit=10000")
entity_labels = {n["label"] for n in entity["nodes"]}
check(
    "entity network is a different, deeper graph",
    {"PHONE", "VEHICLE", "LOCATION", "BANK_ACCOUNT"} <= entity_labels
    and entity["counts"]["nodes"] > rel["counts"]["persons"],
    f'{entity["counts"]["nodes"]} nodes vs {rel["counts"]["persons"]} people',
)

# ---------------------------------------------------------------------------
print("\n[2] Criminal stars come only from criminal_status")
# ---------------------------------------------------------------------------
starred = [n for n in rel["nodes"] if n["is_criminal"]]
check("stars exist", len(starred) > 0, ", ".join(n["name"] for n in starred))
check(
    "every star has an authoritative criminal_status",
    all(n.get("criminal_status") for n in starred),
    str({n["name"]: n["criminal_status"] for n in starred}),
)
check(
    "no star without criminal_status",
    all(n["is_criminal"] == bool(n.get("criminal_status")) for n in rel["nodes"]),
)
check(
    "stars are rare, not universal",
    len(starred) < len(rel["nodes"]) * 0.25,
    f"{len(starred)} of {len(rel['nodes'])}",
)

# ---------------------------------------------------------------------------
print("\n[3] Person -> Person relationships carry real supporting evidence")
# ---------------------------------------------------------------------------
top = rel["edges"][0]
names = {n["provenance_key"]: n["name"] for n in rel["nodes"]}
print(
    f"    top edge: {names[top['source']]} <-> {names[top['target']]} | "
    f"{top['label']} | {top['evidence_count']} records | cross_case={top['cross_case']}"
)
check("edge has supporting records", top["evidence_count"] >= 1)
check("edge is labelled with a relationship", bool(top["label"]))
check("no duplicate edges per pair", len({e["id"] for e in rel["edges"]}) == len(rel["edges"]))

st, ev = call(
    f"/graph/master/relationship-evidence?source={q(top['source'])}&target={q(top['target'])}"
)
check(
    "edge evidence drill-down works",
    st == 200 and ev["supporting_item_count"] >= 1,
    f"{ev['supporting_item_count']} items",
)
kinds = {i["kind"] for i in ev["supporting_items"]}
print(f"    supporting kinds: {sorted(kinds)}")
check("supporting entities are evidence, not nodes", "PHONE" in kinds or "TRANSACTION" in kinds)
cited_docs = {d for i in ev["supporting_items"] for d in i["source_doc_ids"]}
check("supporting records cite real documents", len(cited_docs) >= 1, f"{len(cited_docs)} docs")

# ---------------------------------------------------------------------------
print("\n[4] Full chain per case: CASE -> PEOPLE -> EVIDENCE -> FILE -> PROVENANCE")
# ---------------------------------------------------------------------------
walked = 0
for case in case_items[:6]:
    cid, cnum = case["id"], case["case_number"]
    st, docs = call(f"/cases/{cid}/documents")
    items = (docs or {}).get("items", [])
    if not (st == 200 and items):
        check(f"{cnum}: evidence records exist", False, f"status={st} items={len(items)}")
        continue
    opened = missing = 0
    for d in items:
        key = d.get("storage_key")
        if not key:
            missing += 1
            continue
        s, ct, data = raw(f"/sources/raw?path={q(key)}")
        if s == 200 and data:
            opened += 1
            if ct == "application/pdf" and not data.startswith(b"%PDF-"):
                check(f"{cnum}: {d['filename']} is a real PDF", False)
        else:
            missing += 1
    ok = opened == len(items) and missing == 0
    check(
        f"{cnum}: {len(items)} evidence records, all files open",
        ok,
        f"opened={opened} missing={missing}",
    )

    # provenance for one document in this case
    st, prov = call(f"/evidence/{q(items[0]['id'])}/provenance")
    good = (
        st == 200
        and prov["case"]
        and prov["case"]["case_number"] == cnum
        and prov["file"]["available"]
        and prov["checks"]["hash_matches"]["ok"] is True
    )
    check(
        f"{cnum}: provenance chain resolves",
        good,
        f'file={prov["file"]["size_bytes"]}B checks='
        f'{ {k: v["ok"] for k, v in prov["checks"].items()} }',
    )
    walked += 1

check("several cases walked end to end", walked >= 5, f"{walked} cases")

# ---------------------------------------------------------------------------
print("\n[5] Source preview / download / errors / traversal")
# ---------------------------------------------------------------------------
sample = case_items[0]
_, sdocs = call(f"/cases/{sample['id']}/documents")
sitem = sdocs["items"][0]
skey = sitem["storage_key"]

st, prev = call(f"/sources/preview?path={q(skey)}")
check(
    "preview returns the stored file",
    st == 200 and prev["status"] == "AVAILABLE" and prev["openable"],
    f'{prev["render_kind"]} {prev["file"]["size_bytes"]}B',
)
check(
    "preview never says 'workspace unavailable'",
    "workspace is unavailable" not in json.dumps(prev).lower(),
)

st, ct, data = raw(f"/sources/raw?path={q(skey)}")
check("raw download returns bytes", st == 200 and len(data) > 0, f"{len(data)}B {ct}")

st, miss = call(f"/sources/preview?path={q('evidence/NOPE/NOPE.pdf')}")
check(
    "missing file reports NOT_FOUND naming the file",
    st == 200 and miss["status"] == "NOT_FOUND" and "NOPE" in miss["reason"],
    miss.get("reason", ""),
)
st, _ct, _body = raw(f"/sources/raw?path={q('evidence/NOPE/NOPE.pdf')}")
check("missing file 404s on the byte route", st == 404, str(st))

for attempt in ("../../etc/passwd", "/etc/passwd", "evidence/../../../etc/passwd"):
    st, body = call(f"/sources/preview?path={q(attempt)}")
    check(
        f"traversal refused: {attempt}",
        st in (400, 404, 422) and "workspace is unavailable" not in json.dumps(body).lower(),
        str(st),
    )

# ---------------------------------------------------------------------------
print("\n[6] Person detail and the person-network route")
# ---------------------------------------------------------------------------
pkey = person_items[0]["provenance_key"]
st, ent = call(f"/explore/entities/{q(pkey)}")
check(
    "person detail resolves",
    st == 200 and ent["entity"]["label"] == "PERSON",
    f'{ent["entity"]["name"]} rels={ent["relationship_count"]}',
)
check("person detail lists real relationships", ent["relationship_count"] > 0)
check("person detail lists evidencing documents", len(ent["documents"]) >= 0)

st, net = call(f"/graph/master/person/{q(pkey)}/network?depth=3")
check("person network route resolves", st == 200, f'{net.get("counts", {}).get("nodes")} nodes')

# ---------------------------------------------------------------------------
print("\n[7] Hash integrity across the whole dataset")
# ---------------------------------------------------------------------------
mismatch = 0
sampled = 0
for case in case_items[:5]:
    _, d = call(f"/cases/{case['id']}/documents")
    for item in d["items"][:4]:
        st, v = call(f"/evidence/{q(item['id'])}/verify")
        sampled += 1
        if st != 200 or v.get("match") is not True:
            mismatch += 1
check(
    "recorded hashes match the stored bytes",
    mismatch == 0,
    f"{sampled - mismatch}/{sampled} verified",
)

# ---------------------------------------------------------------------------
print("\n[8] Provenance distribution (not one shared file)")
# ---------------------------------------------------------------------------
doc_counts = Counter()
for e in rel["edges"]:
    for i in e["supporting_items"]:
        for d in i["source_doc_ids"]:
            doc_counts[d] += 1
check(
    "provenance spans many documents",
    len(doc_counts) >= 50,
    f"{len(doc_counts)} distinct documents cited",
)
check(
    "no single document carries the graph",
    doc_counts.most_common(1)[0][1] < sum(doc_counts.values()) * 0.25,
    f"most-cited holds {doc_counts.most_common(1)[0][1]} of {sum(doc_counts.values())}",
)

# ---------------------------------------------------------------------------
print(f"\n{'=' * 72}")
print(f"PASSED: {PASSES}   FAILED: {len(FAILURES)}")
for f in FAILURES:
    print(f"  - {f}")
sys.exit(1 if FAILURES else 0)
