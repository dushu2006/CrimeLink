"""Investigative-quality evaluation for the CrimeLink Case Evidence Assistant.

This package measures the *current* architecture; it does not add retrieval,
reasoning or UI capability.  It asks a fixed, data-driven bank of investigative
questions across twelve categories, runs them through the real gateway, and
grades each answer on thirteen mechanical checks.

Run it with::

    cd backend
    .venv/bin/python -m evals.investigative_quality.runner --cases CR-2001 CR-2019 CR-2020

Results are written to the ignored ``evals/investigative_quality/results/``
directory as JSON (machine-readable, one object per question) plus a Markdown
scorecard; no generated evaluation output is part of the source tree.
"""
