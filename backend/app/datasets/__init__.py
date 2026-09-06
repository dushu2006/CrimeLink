"""Dataset-driven core of CrimeLink.

Everything an investigator sees derives from exactly one dataset: the one
marked ``ACTIVE``.  This package owns that concept end to end:

``registry``
    The dataset lifecycle -- create, stage transitions, activate, archive --
    and the single ``active dataset`` resolution every other layer calls.

``discovery``
    Universal file discovery.  A dataset is *whatever the user gave us*: one
    CSV, one XLSX, a ZIP, a folder, a folder of ZIPs.  Nothing here assumes
    ``operational/`` or ``documents/`` exists, and nothing is classified by
    folder name.

``readers``
    One reader per real format (CSV/TSV, XLSX, JSON/JSONL, TXT/MD, PDF, DOCX)
    that returns either tables or text, always with the coordinates needed to
    point back at the exact row / sheet / page.

``schema_map``
    The normalization layer: arbitrary source column names -> the canonical
    CrimeLink schema, with a confidence score and an explicit list of the
    columns it could not map.

``normalize``
    Canonical entities and relationships (with temporal validity) built from
    mapped tables, each one carrying its provenance.

``pipeline``
    The staged import workflow: validate -> discover -> parse -> classify ->
    normalize -> persist -> documents -> graph -> indexes -> READY.

``graph_build``
    Projection of the canonical relationship layer into the graph store,
    tagged with ``dataset_id`` so a previous dataset's nodes can never be
    returned by a query.
"""

from __future__ import annotations

__all__ = [
    "discovery",
    "graph_build",
    "normalize",
    "pipeline",
    "readers",
    "registry",
    "schema_map",
]
