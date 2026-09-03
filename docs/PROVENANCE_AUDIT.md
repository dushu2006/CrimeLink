# CrimeLink — Provenance & Evidence-Navigation Audit (Phase 1)

Audit performed against the **running** system, not by reading code alone:
the real corpus was imported through the real pipeline into SQLite + the
embedded graph, and the resulting provenance was inspected.

## Verified working today (do NOT rebuild)

| Area | State |
|---|---|
| Ingestion architecture | Real. `synthetic_external` SourceAdapter → `upload_document` → 6-stage pipeline. Nothing bypasses it. |
| Import result | 36 cases, 336 documents, 12,372 graph nodes, 67,312 edges, 698 entity-resolution proposals. |
| Idempotency | Confirmed. Second import reported 336/336 duplicates, 0 new rows. |
| Ground-truth isolation | Confirmed. All 5 `ground_truth/*.json` + 2 `metadata/*.json` excluded at scan time. |
| G1 (evidence) | Structurally enforced: `GraphEdge` cannot be constructed without `source_doc_id`. |
| Audit chain | Hash-chained, append-only, verify endpoint exists. |
| Backend APIs | Cases, documents, entities, relationships, graph, patterns, audit, users, thresholds, quarantine, health all exist and are real. |
| Text-span evidence | `text_span` IS captured at extraction time and resolves correctly. |

Confirmed by resolving real spans against stored derived text:

```
CALLED       span [329,409]   → "Call +919586356082 -> +919383253764 at 2025-05-31T04:02:00+00:00 for 1356s (SMS)"
USES_PHONE   span [0,71]      → "Criminal history: Gopal Naidu, phone +919145228439, case FIR/2026/00001"
TRANSFER_TO  span [1126,1227] → "Transfer of Rs 156,258.20 from A/C XX90857229 to A/C XX30786467 ..."
```

So evidence → *a* source location already works.

---

## THE BLOCKING PROBLEM

The requested chain is:

```
Relationship → Evidence → cdr.csv → Row 18342 → caller/receiver fields
```

That chain **cannot be built today**, and no amount of frontend work will fix it.

### Why

The corpus adapter does not ingest the corpus files. It *derives new files* from
them and ingests those. In `_records_from_crimelink_corpus`, each case gets a
synthesised document:

```
operational/cdr.csv  (15,000 rows, columns: cdr_id,timestamp,from_phone_id,to_phone_id,...)
        ↓  sliced by case, columns renamed, IDs replaced by values
C0001-cdr.csv        (columns: calling_number,called_number,timestamp,duration_seconds,direction,imei)
```

Three identity-destroying transformations happen here:

1. **`cdr_id` is dropped.** `CDR000001` exists nowhere in the ingested document.
2. **IDs are replaced by resolved values.** `PH0161` becomes `9802660843`.
3. **Rows are re-ordered and re-numbered per case.** Row 5 of `C0001-cdr.csv`
   is *not* row 5 of `cdr.csv`.

Also observed: timestamps shift (`2025-05-31 09:32` in the derived file vs
`04:02:00+00:00` in the span) because of IST→UTC normalisation, so even
text-matching back to the original row is unreliable.

### Consequence

`text_span` points into a **derived artefact**, not into the corpus file.
Today the system can honestly say *"this came from C0001-cdr.csv line 5"*.
It **cannot** say *"this came from cdr.csv row 18342, fields caller/receiver"*,
because the link from derived row → original row was discarded at ingestion,
and provenance discarded at ingestion cannot be reconstructed afterwards.

This is exactly the failure mode the brief warned about. It must be fixed in
the ingestion architecture first.

### Secondary gaps found

- **No `SourceReference` model exists.** There is `text_span` on edges and
  `evidence_doc_ids` (a bare JSON array of doc IDs) on patterns and the
  resolution queue — no row/field/line addressing anywhere.
- **`audit_logs` is empty (0 rows)** after a full corpus import. The CLI import
  path never records audit events; only the HTTP API path does.
- **19 of 336 documents FAILED** ingestion, and 70 are stuck PENDING. These
  should surface honestly in Quarantine rather than being invisible.
- **3,244 corpus rows silently dropped** (1,534 CDR, 820 transactions, 673
  sightings, 217 intel) for having no resolvable `case_id`. Currently only a
  log warning — this is exactly the data-quality reporting the brief asks for.
- **Frontend is 6 pages.** No routes for `/entities/:id`, `/relationships/:id`,
  `/documents/:id`, `/source/:id`. Admin is one 872-line file with local tab
  state, so admin sections are not linkable, and browser back/forward cannot
  work for them.

---

## Proposed plan

**Phase 2 — provenance at ingestion (the foundation).**
Extend `SourceRecord`/`Block` to carry origin coordinates, and have the corpus
adapter emit, per derived row: origin file, origin row number, origin primary
key (`cdr_id`), and the origin field names. Add a `source_references` table +
Alembic migration. Populate it during the pipeline, where the information still
exists. Everything else depends only on this.

**Phase 3 — source APIs.** Range-based file reads (never ship a 15k-row CSV to
the browser), CSV row addressing, TXT line windows with context.

**Phase 4 — one reusable Source Viewer.** CSV row/cell + TXT line-window
highlighting, adapting to source type.

**Phase 5 — routing & Administration.** Real routes per admin section and per
resource, so deep links and browser navigation work.

**Phase 6-7 — wire investigation views + AI answers to evidence.**

**Phase 8-10 — UI refinement, tests, end-to-end verification.**
