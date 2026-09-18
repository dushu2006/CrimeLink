# CrimeLink Demo Dataset — Runtime Source Bundle

This directory is populated by `backend/scripts/seed_demo_v2.py`.

The seeder writes the **exact bytes** used by the evidence/source records here, preserving the same relative paths stored in `DatasetFile.relative_path`. It is intentionally kept out of Git as generated runtime data.

The bundle contains the presentation corpus across PDF, CSV, JSON, TXT and other seeded evidence/source formats. The active dataset points SourceViewer at this directory as its filesystem copy, while the object store remains the authoritative storage adapter.

Do not hand-edit generated files; re-run the v2 demo seed to rebuild them deterministically.
