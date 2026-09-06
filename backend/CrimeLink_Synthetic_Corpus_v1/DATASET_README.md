# CrimeLink Synthetic Investigation Corpus — V1-Compatible Realism Rebuild

Generated: 2026-09-06

This is a fully synthetic corpus intended for CrimeLink demonstration/evaluation. The operational CSV schemas are kept compatible with the supplied CrimeLink corpus.

## Design
- World-first generation: identities, life events, organizations, communications, finance, vehicles, addresses and cases are generated before documents.
- Mostly two-word Indian names, a small number of three-word names, and only two deliberate same-name ambiguity pairs.
- Aadhaar and PAN are strong master identity signals, but investigator-facing documents expose only the signals appropriate to each source.
- Ordinary civilian behavior is the majority background; suspicious activity is embedded within normal life.
- Criminal networks develop inside the same population, with bridge people and a legitimate decoy.
- Cases have temporal investigation stages and source uncertainty.
- Ground truth is isolated under `_ground_truth/`.

## Counts
450 persons; 55 organizations; 650 phones; 480 bank accounts; 230 vehicles; 480 addresses; 120 locations; 60 cases; 260 evidence; 14,000 calls; 2,500 SMS; 9,000 transactions; 700 vehicle sightings; 400 travel records; 1,001 documents.

All identifiers and records are fictional/synthetic.
