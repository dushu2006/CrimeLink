# CrimeLink Synthetic Investigation Dataset

Version: 3.0 — Realism Upgrade
Generation date: 2026-09-05

This corpus is synthetic and intended for CrimeLink evaluation and demonstration.

Core design:
- 450 persons, predominantly two-word Indian names with regionally plausible distributions.
- Three intentionally difficult same-name pairs; strong KYC identifiers distinguish them.
- Aadhaar/PAN are strong identity signals but are deliberately not present in every source.
- 55 fictional organizations.
- 650 synthetic phones, 480 synthetic bank accounts, 230 vehicles, 480 addresses, 120 locations.
- 60 cases, 38 FIRs, 260 evidence records.
- 9,000 transactions, 14,000 calls, 2,500 SMS, 700 vehicle sightings, 400 travel records.
- 12 hidden networks, including EVNET_11 as a bridge and EVNET_12 as a legitimate decoy.
- Investigator-facing files contain uncertainty, ordinary civilian activity, source-specific naming, temporal changes and plausible innocent explanations.
- Ground truth is isolated under _ground_truth.

IMPORTANT:
All identifiers, persons, organizations, cases and records are fictional/synthetic. No real person's Aadhaar, PAN, phone number, bank account or criminal record is represented.
