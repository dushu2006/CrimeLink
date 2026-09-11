"""Evidence-driven investigation reasoning layer.

This package is an *orchestration* layer, not a new intelligence. It reuses
the platform's existing capabilities — the AI gateway, deterministic graph
analytics, scoped retrieval, review queues, and the hash-chained audit log —
and composes them into question-driven investigations over the active
dataset:

* ``labels`` — the six inference labels plus evidence-strength scoring.
* ``schemas`` — the strict JSON contract of an investigation answer.
* ``prompts`` — the contract the language model must obey (it explains
  deterministic results; it never authors findings), plus the language guard.
* ``evidence`` — every claim carries openable, hash-pinned provenance.
* ``entity_resolution`` — question mentions become canonical graph ids.
* ``patterns`` — deterministic suspicious-pattern detectors.
* ``relationships`` — relationship discovery between resolved entities.
* ``hypotheses`` — hypotheses with mandatory contradiction search.
* ``gaps`` — missing evidence, reported as DATA_GAP, never filled in.
* ``next_steps`` — lawful, identity-first follow-up recommendations.
* ``memory`` — dataset-pinned investigation threads.
* ``orchestrator`` — the pipeline that runs all of the above.

Nothing here bundles data, trains a model, or calls a live API in tests.
Strength, labels, gaps, and next steps never depend on a model being
available: without one the answer degrades to the deterministic analysis,
honestly labelled as such.
"""
