# CrimeLink synthetic investigation dataset validation

The active bootstrap seed is `backend/scripts/seed_demo_v2.py` (`demo-dataset-002`). It is deterministic and replaces the previous 25-case v2 corpus with the intended 20-case corpus on every reseed. The seed writes the same bytes to the configured object store and `demo_dataset/runtime_sources/`.

## Expected corpus

| Metric | Result |
|---|---:|
| Cases | 20 |
| Closed / active / open / review | 10 / 4 / 3 / 3 |
| People | 105 |
| Phones | 84 |
| Vehicles | 38 |
| Bank accounts | 42 |
| Evidence documents | 210 |
| Confidential intelligence sources | 20 |
| FIRs | 20 |
| Charge sheets | 10 |
| CDR / surveillance JSON / financial CSV | 20 / 20 / 20 |
| Other narrative, forensic, ANPR and diary sources | 130 |

Each case has 10 core records; closed cases have an additional final charge sheet. Every core bundle includes FIR, CDR, surveillance, financial, witness, intelligence, scene, ANPR, forensic and diary material. Closed-case findings are marked `CONFIRMED` only after the multi-source evidence chain is created; offender repetition is limited to the closed-case cast.

## Invariants enforced by the seed

- deterministic IDs and timestamps; no Faker runtime randomness
- every CDR number is selected from the generated phone registry
- every transaction account is selected from the generated account registry
- every vehicle appearing in ANPR/surveillance is selected from the vehicle registry
- every source/evidence row references one of the 20 generated cases
- every case has an FIR; every closed case has a charge sheet and confirmed finding
- every person is assigned to at least one case before graph construction
- source bytes are hashed once, mirrored once, and reused for object storage and viewer metadata
- the graph is purged for `demo-dataset-002` before rebuild; competing datasets are not activated

The source bundle is synthetic and intentionally carries an explicit synthetic notice. No real personal information is used.
