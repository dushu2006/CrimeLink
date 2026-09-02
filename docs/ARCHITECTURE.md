# Architecture

CrimeLink is built so that the interesting rules live in one place each, and so
that the place is the one an auditor would expect to find it in. This document
explains the shape of the code and, where a decision is non-obvious, why it was
made.

## 1. Layering

```
app/api/v1/         HTTP only: parse, authorise, delegate, audit
app/services/       application logic; the only layer the API calls
app/domain/         the model: enums, provenance keys, normalisation, graph types
app/pipeline/       ingestion: extraction → entity resolution → write
app/analytics/      centrality, patterns, explanations, temporal paths
app/adapters/       the outside world: graph, NLP, object store, broker
app/security/       tokens, RBAC, jurisdiction, rate limiting, @audited
app/ports/          the interfaces the adapters implement
```

Dependencies point downwards only. `app/domain` imports nothing from the rest of
the application, which is what makes the domain tests fast and meaningful.

## 2. Ports and adapters

Four ports have two implementations each. The profile selects them; nothing else
in the codebase changes.

| Port | embedded | production |
|---|---|---|
| graph | NetworkX `MultiDiGraph` with JSON write-through | Neo4j 5 (Cypher through `injector.py`) |
| objects | filesystem, HMAC-signed links | MinIO, three buckets |
| broker | in-process thread pool | Celery + Redis |
| relational | SQLite | PostgreSQL 15 |

The embedded profile is not a toy: it is what the tests and local development run on, and
it is the only thing a district can use while waiting for infrastructure. It
means a broken port contract shows up in CI rather than at a deployment.

### Why the embedded graph exists at all

Neo4j Community is a single-database server; running one per test is slow and
running one per district before procurement is impossible. The embedded graph
keeps the *same* injector interface, so the Cypher in `injector.py` is exercised
in production and the domain rules are exercised everywhere.

## 3. The graph write path (G1)

```
pipeline  ──►  GraphInjector.upsert_nodes / upsert_edges  ──►  store
                        │
                        └── _validate_nodes / _validate_edges
                              raise UnevidencedGraphWriteError when
                              properties["source_doc_id"] is absent
```

`app/adapters/graph/injector.py` is the **only** module permitted to write to the
graph. This is enforced by convention and checked by tests, and it means a
reviewer auditing "can anything enter the graph without evidence?" has one file
to read.

Three idempotence rules keep re-ingestion safe:

* `provenance_key = SHA256(case_id | doc_id | entity_type | normalized_value)`
* `edge_key(rel_type, source, target, *discriminators)` — see `app/domain/provenance.py`
* `CALL` edges aggregate (`call_count`, `first_ts`, `last_ts`) instead of
  creating one edge per call, so a CDR with 50,000 calls does not create 50,000
  edges.

Meta-relationships (`POTENTIAL_ALIAS`, `SIMILARITY_REJECTED`) are review
artifacts rather than evidence, but they still carry the union of both endpoints'
document ids — an investigator must be able to open the sources from the edge.

## 4. Entity resolution (G2)

```
hard identifier match (Aadhaar / phone / IFSC+account / vehicle)   →  merge now
name similarity ≥ 0.85                                             →  propose
alias co-mention                                                   →  propose
otherwise                                                          →  distinct nodes
```

`combined_similarity` is the max of Jaccard, token-sort, a weighted containment
score and a consonant-skeleton ratio, computed on a **script-agnostic key**:
Devanagari is transliterated to ISO-15919 and folded to ASCII first, so
`सुरेश मेहता` and `Suresh Mehta` score 1.00.

Two deliberate constraints:

* **One proposal per new mention.** Seven documents naming the same person
  produce seven nodes (the provenance key is per document by definition);
  comparing each against every existing person would put dozens of identical
  decisions in the queue. A queue nobody can work through gets rubber-stamped,
  which is the failure G2 exists to prevent. Merges are transitive, so the best
  single proposal per mention still lets the cluster form.
* **A rejected pair is tombstoned** (`SIMILARITY_REJECTED`) and never
  re-proposed. Re-deciding the same non-match every time a document lands is how
  a review queue becomes background noise.

## 5. Analytics

Centrality is computed **in Python**, with a hand-written confidence-weighted
PageRank (see `app/analytics/centrality.py::weighted_pagerank`) rather than
NetworkX's SciPy-backed implementation. Three reasons:

1. an air-gapped district can install CrimeLink from a wheelhouse without
   pulling a scientific Python stack;
2. edge weights are normalised by out-weight, so a low-confidence social link
   cannot inflate a node's reach — the "source-confidence discipline" the PRD
   asks for, expressed in the maths;
3. the same code runs in both profiles, so the ranking an investigator sees is
   the ranking the tests assert.

Every influence response carries its justification: the summary sentence, the
weighted edges that produced the score, the community, the method description and
the document ids behind it. **A score never travels alone** — a number without
its derivation is not something an officer can defend in court.

## 6. Security

* **Tokens** — 15-minute access tokens, 8-hour refresh tokens with rotation.
  Replaying a rotated refresh token is treated as theft: the entire family is
  revoked and the event is audited.
* **RBAC** — three roles (VIEWER, INVESTIGATOR, ADMIN), enforced on the server
  (`Principal.require`), never by hiding UI.
* **Jurisdiction** — `JurisdictionScope` narrows every query. A case outside your
  jurisdiction returns the same 404 as one that does not exist, so the API does
  not confirm that another district's case is real.
* **Audit** — `@audited(action, …)` wraps a handler and writes a hash-chained row
  in the *same transaction* as the mutation, so "the action happened but the
  record did not" is impossible.
* **Rate limiting and lockout** — 100 requests/minute, 10 logins/minute, five
  failed logins lock an account for 30 minutes. Both counters are committed
  before the error response is returned, because a request-scoped session is
  rolled back on an error path and would otherwise discard the lock.

## 7. Failure handling

The pipeline records a stage event for each of its six stages. A document that
cannot be parsed is **quarantined** with the reason, not dropped; an
administrator releases or discards it, and both are audited. A model that cannot
be reached falls through NIM → IndicNER → deterministic heuristic, and the
extraction continues at lower confidence rather than stopping the case.

No component fails silently: every exception path either records a stage event,
writes to the audit log, or both.
