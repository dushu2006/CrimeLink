# CrimeLink Synthetic Corpus v1

100% synthetic development/evaluation corpus. It is NOT government/police data.

Operational data is imported through the CrimeLink source adapter and ingestion pipeline. Ground truth is benchmark-only and must never be loaded into investigator-facing storage.

Flow:
operational CSV/TXT -> Source Adapter -> validation/normalization -> PostgreSQL -> graph injection -> Neo4j.

Seed: 20260902
