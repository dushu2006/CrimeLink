# CrimeLink industry foundation

This branch adds an evidence-grounded investigator workflow without replacing
CrimeLink's existing adapter architecture, embedded profile, graph injector,
source references, audit chain, or AI Gateway.

## Implemented

### Server-side security and policy

- Expanded roles: `SUPER_ADMIN`, `DISTRICT_ADMIN`, `STATION_ADMIN`,
  `SUPERVISOR`, `INVESTIGATOR`, `FORENSIC_ANALYST`, `FINANCIAL_ANALYST`,
  `INTELLIGENCE_ANALYST`, `VIEWER`, and `AUDITOR` (with legacy `ADMIN`).
- Information classifications: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`,
  `RESTRICTED`, `SECRET`, and `HIGHLY_RESTRICTED`.
- Classification checks run in the backend; the UI is not a security boundary.
- Production settings fail closed for debug mode, wildcard CORS, placeholder
  JWT secrets, default Neo4j/MinIO credentials, and development PostgreSQL
  credentials.
- Request-size protection, trusted-host support, CSP/HSTS/security headers,
  upload filename path-segment rejection, refresh-token rotation and failed
  login lockout remain enabled.

### Evidence and custody

`CaseDocument` retains the original SHA-256, filename, MIME type, size,
source metadata, case, and classification. `EvidenceCustodyEvent` is an
append-only record of `IMPORTED`, `STORED`, `HASH_VERIFIED`, `ACCESSED`,
`DOWNLOADED`, `DERIVED`, `SHARED`, `EXPORTED`, and `SEALED` events.

- `POST /api/v1/documents/{doc_id}/integrity` re-computes the stored hash and
  records a verification event.
- `GET /api/v1/cases/{case_id}/custody` exposes the custody trail to an
  authorized caller.
- Unexpected bytes are reported as `TAMPERED`; the system does not silently
  repair or replace evidence.

### Investigation workflow

- Formal case states: `DRAFT`, `OPEN`, `ACTIVE_INVESTIGATION`,
  `UNDER_REVIEW`, `SUBMITTED`, `CLOSED`, `SEALED`, with domain-level invalid
  transition rejection and supervisor authorization for closure/sealing.
- Durable tasks: owner, creator, priority, status, due date, linked evidence,
  linked entities, linked findings, comments, and timestamps.
- Versioned investigator notes with classification and explicit links. Notes
  never mutate source evidence.
- Persistent hypotheses with supporting evidence, contradicting evidence,
  unknown information, neutral assessment, and review status.
- Claims use `subject / predicate / object / time / source / evidence / status`.
  A second incompatible claim creates a persisted contradiction record that
  keeps both claims and suggests verification; it never selects a winner.
- Unknowns and next steps are exposed through `/unknowns` and `/next-steps`.
  Next steps are evidence-gathering actions only.

### Approvals and reporting

`ApprovalRecord` binds a human decision to an object hash/version. Requesters
cannot approve their own controlled action. Versioned report drafts contain
case information, evidence inventory and hashes, analysis sections, unknowns,
open questions, recommended investigative actions, and audit metadata.
Reports remain `DRAFT`/`PENDING_REVIEW` until a supervisor approves them.

Endpoints:

- `POST/GET /cases/{case_id}/reports`
- `POST /reports/{report_id}/submit`
- `POST /reports/{report_id}/approve`
- `POST/GET /cases/{case_id}/approvals`
- `PATCH /approvals/{approval_id}`

### AI safety and model governance

- `app.ai.safety` validates evidence IDs and entity IDs against the retrieved
  package, rejects AI-authoritative actions, detects prompt-injection-like
  instructions inside untrusted evidence, and reports neutral-language
  warnings.
- The gateway sanitizes evidence text before it enters a model prompt and
  withholds output with invalid evidence references.
- `app.ai.registry` provides provider-neutral model metadata for extraction,
  reasoning, explanation, classification, and embedding roles.
- `app.ai.evaluation` provides reproducible precision/recall, citation,
  contradiction, and hallucination-rate metrics over a caller-supplied
  benchmark manifest.
- `/api/v1/admin/models` records operator-managed model versions and evaluation
  state. API keys are never stored in the registry table.

### Investigator console

Case Detail now includes a case-scoped workflow panel for tasks, hypotheses,
contradictions, notes, and “What CrimeLink does not know”. The existing graph,
timeline, evidence viewer, findings, AI Gateway and review queue are unchanged
and continue to show provenance links and human-review boundaries.

## Adapter-ready / external infrastructure

- PostgreSQL, Neo4j, MinIO and Celery remain the production adapters. They are
  not started by the test suite.
- A government CCTNS/CCNS integration is not claimed. The existing
  `SourceAdapter` boundary remains the correct integration point and requires
  an authorized API or SFTP contract.
- OCR and IndicNER provider integrations remain configuration-dependent. The
  deterministic/heuristic fallback is available offline; language performance
  must be measured on approved representative data before operational use.
- Vector/BM25 hybrid retrieval and production-scale GDS are adapter-ready; the
  current authoritative graph and deterministic analytics continue to be the
  source of truth.

## Not claimed

- No automatic guilt, innocence, arrest, prosecution, or punishment decision is
  implemented.
- No real government data is committed.
- Disaster recovery is documented, but restoration against a deployed
  PostgreSQL/Neo4j/MinIO installation has not been executed in this checkout.
- The npm audit still reports moderate React Router v6 advisories whose fix is
  a breaking React Router v7 upgrade; the application remains on v6 to avoid an
  untested framework migration. Use the repository verification commands for the current validation state.
