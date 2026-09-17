# Person → Person relationship network

**What changed:** the Investigate → Relationships graph stopped being an entity
graph wearing a relationship graph's title.

---

## 1. The problem

`/investigate` presented the **entity graph** as the criminal relationship
network. The active v2 demo dataset projects **575 nodes / ~2 790 edges**:

```
Case: 25 · Person: 120 · Phone: 90 · Vehicle: 40 · Location: 50
Organization: 15 · BankAccount: 35 · Event: 200
PARTICIPATED_IN: 836 · ASSOCIATE_OF: 322 · USES_PHONE: 373 · LOCATED_AT: 317
OWNS_ACCOUNT: 172 · CALLED: 400 · TRANSFER_TO: 175 · OWNS_VEHICLE: 85
MEMBER_OF: 54 · RELATIVE_OF: 55
```

Rendered as-is, the canvas reads as `Person → Phone → Account → Location →
Vehicle → …`. The investigator cannot see *who is connected to whom*, and the
"Confirmed Criminal ONLY (Star)" legend promised a marker the graph never drew,
because the demo seed never wrote the authoritative `criminal_status` field.

Two of the three surfaces made it worse by fetching the whole entity graph and
filtering it **in the browser** — `InvestigatorWorkspace` and `RelationshipsPage`
both called `masterGraph()` (all 575 nodes) and kept only the person endpoints.
The browser had already paid for every phone and location before one was
discarded.

## 2. The model

A phone number, bank account, vehicle, address, organisation, call record or
transfer is **support** for a relationship, never the relationship itself. The
backend now walks those entities internally and collapses them into a single
aggregated person-to-person edge:

```
Priya Kumar ★ ─── Communication · 12 records ─── Dinesh Malhotra
                 ├─ Recorded ASSOCIATE_OF relationship
                 ├─ Communication: +91 90000 00274 → +91 90000 00137 (3 calls)
                 ├─ Financial link: SBI a/c 0000 → HDFC a/c 4417 (46 755)
                 └─ … 9 more records
```

Derivation lives in `backend/app/services/person_relationships.py` and has three
sources, all of which require an existing record. Nothing is invented:

| Source | Graph rows consumed | Relationship type |
| --- | --- | --- |
| First-class person-to-person records | `ASSOCIATE_OF`, `RELATIVE_OF`, `NAMED_ACCOMPLICE_OF`, `ARRESTED_WITH`, `LINKED_ON_SOCIAL`, `SHARED_IDENTIFIER`, `ACCUSED_IN` | `KNOWN_ASSOCIATION`, `FAMILY_RELATIVE`, `NAMED_ACCOMPLICE`, `ARRESTED_WITH`, `SOCIAL_LINK`, `SHARED_IDENTIFIER`, `EVIDENCE_SUPPORTED` |
| Shared supporting attribute | two people on the same `USES_PHONE` / `OWNS_ACCOUNT` / `OWNS_VEHICLE` / `LOCATED_AT` / `MEMBER_OF` target | `SHARED_PHONE`, `SHARED_ACCOUNT`, `SHARED_VEHICLE`, `SHARED_ADDRESS`, `SHARED_ORGANIZATION` |
| Bridged record | `person → phone ─CALLED→ phone ← person`, `person → account ─TRANSFER_TO→ account ← person` | `COMMUNICATION`, `FINANCIAL_LINK` |

Guards:

* **No duplicate edges.** One unordered person pair → exactly one edge, carrying
  `supporting_item_count`, `relationship_types`, `strength` and `case_ids`.
* **No invented edges.** Two people with no shared record and no bridge get no
  edge at all, and the response says so (`empty_reason`).
* **No explosion from generic attributes.** An address or account held by more
  than `max_fanout` (default 12) people is a *category*, not a relationship; it
  is reported under `suppressed_shared_entities` instead of producing thousands
  of meaningless pairs.
* **Progressive disclosure.** `limit`, `relationship_types` and `min_evidence`
  narrow the slice server-side; `relationships_total` always reports the whole.

## 3. The ★ rule

`criminal_status` is the authoritative field. It is the column the ingest
pipeline maps (`app/datasets/schema_map.py` → `PERSON.criminal_status`), the
property `GraphService._node_row` reads to set `is_criminal`, and the property
the console's `isConfirmedCriminal` checks. Only
`CONFIRMED / CONVICTED / ACCUSED / CHARGESHEETED / CRIMINAL` earn the star.

Never derived from: degree, betweenness, PageRank, evidence volume, having a
phone or a transaction, appearing in a case, being a witness, being a suspect,
or being an associate. `is_criminal` is a pure function of `criminal_status` and
a test asserts exactly that.

The v2 seed previously wrote no `criminal_status` at all, which is why no star
ever appeared. It now maps the dataset's own explicit assertions onto the
authoritative field (`scripts/seed_demo_v2.py`):

* `role == "ACCOMPLICE"` → `CONFIRMED`
* being an `entity_key` of an `InvestigationFinding` with `status == "CONFIRMED"`
  → `CONFIRMED` (the CR-2001 conspiracy finding)

`SUSPECT`, `PERSON_OF_INTEREST`, `WITNESS`, `VICTIM`, `ASSOCIATE` and
`INFORMANT` deliberately map to `None`. Seeded result: **3 of 120 people** carry
the star (Priya Kumar, Vikram Verma, Amit Sharma).

> A deployment seeded before this change has no `criminal_status` on its person
> nodes and therefore shows no stars until it is re-seeded
> (`python -m scripts.seed_demo_v2`). Nothing else about the graph changes.

## 4. The endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/graph/master/relationships` | Cross-case PERSON → PERSON graph. `node_types: ["PERSON"]`. |
| `GET /api/v1/graph/cases/{case_id}/relationships` | Same graph scoped to one case. |
| `GET /api/v1/graph/master/relationship-evidence?source=&target=` | Every record supporting one edge. |
| `GET /api/v1/graph/master` | **Unchanged.** The full entity graph, for ENTITY NETWORK. |
| `GET /api/v1/graph/master/case-network` | **Unchanged.** Case-level network. |

Query parameters on the relationship endpoints: `limit` (default 400),
`include_isolated`, `max_fanout`, `relationship_types`, `min_evidence`.

## 5. The console

`MASTER CASE NETWORK` on `/investigate` now has three tabs, each rendering a
different graph from a different endpoint:

| Tab | Graph | Endpoint |
| --- | --- | --- |
| **PEOPLE NETWORK** (default) | people + person-to-person relationships | `/graph/master/relationships` |
| **CASE NETWORK** | cases + evidence-backed case connections | `/graph/master/case-network` |
| **ENTITY NETWORK** | the deeper evidence/entity graph | `/graph/master` |

`PersonRelationshipNetwork.tsx` renders the default view: people as circles
labelled with their **names**, confirmed criminals as a red node with an amber
ring and a `★` on the line above the name, edges labelled with the relationship
(`Communication · 4`) and thickened by record count, dashed when cross-case.
Selecting an edge fetches its supporting records; each opens in the existing
`SourceViewer`. An empty network says *"No verified person-to-person
relationships found in the active dataset"* and offers **View supporting
entities**.

`InvestigatorWorkspace` no longer loads the entity graph on mount — it fetches
the case-scoped relationship network and loads the entity layer only when the
investigator presses **Show supporting entities**. `RelationshipsPage` reads the
same endpoint instead of filtering `masterGraph()` in the browser, and its
hardcoded `PERSON-001` / `PERSON-024` / `E-042` placeholders are gone.

## 6. Measured result on the seeded v2 dataset

Read back from a live instance seeded with `scripts/seed_demo_v2`
(`GET /api/v1/graph/master/relationships?limit=5000` and `GET /api/v1/graph/master`):

```
graph store         : 575 nodes / 2789 edges
ENTITY NETWORK      : 527 nodes / 2553 edges  (case nodes + document artifacts excluded)
                      PERSON 109 · PHONE 90 · VEHICLE 37 · LOCATION 50
                      ORGANIZATION 6 · BANK_ACCOUNT 35 · EVENT 200

PEOPLE NETWORK      : node_types ["PERSON"], node labels {PERSON} only
people              : 109 in scope, 109 linked
relationships       : 1664 (exactly one edge per unordered person pair)
supporting records  : 2608 aggregated behind those edges
by type             : COMMUNICATION 1151 · KNOWN_ASSOCIATION 168 · SHARED_ADDRESS 100
                      FINANCIAL_LINK 75 · SHARED_PHONE 67 · SHARED_ORGANIZATION 37
                      FAMILY_RELATIVE 36 · SHARED_ACCOUNT 30
confirmed criminals : 3 (★) — Priya Kumar, Vikram Verma, Amit Sharma
```

Example edge, straight from the API:

```
★ Priya Kumar ↔ Dinesh Malhotra
  label: Communication · strength STRONG · cross_case true · 12 supporting records
  relationship_types: COMMUNICATION, FINANCIAL_LINK, KNOWN_ASSOCIATION
  ├─ [Recorded relationship]   Known association (ASSOCIATE_OF)
  ├─ [Communication record]    +91 90000 00274 → +91 90000 00137 · 3 calls
  └─ [Financial transaction] × 10  SBI a/c 0000 / a/c 7290 → HDFC a/c 4417
```

## 7. Tests

* `backend/tests/test_person_relationship_network.py` — acceptance points A–L:
  PERSON-only nodes; phones, accounts, documents, vehicles and locations are not
  nodes; evidence retrievable per edge; aggregation into one edge; ★ only from
  the authoritative status; witnesses/associates never starred; the entity and
  case networks remain distinct graphs; cross-case edges; honest empty state;
  HTTP routes.
* `backend/tests/test_demo_v2_relationship_network.py` — runs the real
  `seed_demo_v2` generators end to end and asserts the person network is real,
  aggregated, cross-case, and that every ★ traces to an explicit dataset
  assertion.
* `frontend/tests/person-relationship-network.test.mjs` — the console defaults
  to the people tab, each tab renders different data, the person view never
  loads the entity graph, and the star keys off the authoritative flag.
