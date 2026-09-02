"""Synthetic development corpus.

The corpus is a *realistic stand-in for the shape and messiness of external
police data*.  It is **not** demo data, and it is **not** presented as real
police data: every record carries ``source_environment="synthetic"`` metadata
and the UI/export layers label it as synthetic.

Use it to exercise every layer of CrimeLink — ingestion, deterministic
extraction, NLP (with or without a model), entity resolution, graph injection,
centrality, pattern detection, cross-case analysis and the AI Gateway — on a
dataset whose complexity, incompleteness, ambiguity and relationship structure
mirrors what an eventual authorised police-data adapter would produce.

Generation is explicit::

    python -m app.synthetic_corpus.generate          # generate with defaults
    python -m app.synthetic_corpus.generate --stats  # show counts without generating
    python -m app.synthetic_corpus.generate --clear  # remove all synthetic records
    python -m app.synthetic_corpus.generate --seed 42

An *external* corpus on disk (e.g. the sibling ``../CrimeLink_Synthetic_Corpus_v1``
checkout) is ingested just as explicitly — see :mod:`app.synthetic_corpus.external`::

    python -m app.synthetic_corpus.external          # ingest CRIMELINK_SYNTHETIC_DATA_ROOT
    python -m app.synthetic_corpus.external --dry-run  # validate/classify only
    python -m app.cli ingest-synthetic               # mode-aware umbrella command

Both modes feed the same six-stage ingestion pipeline through the same
``SourceAdapter`` boundary; only ``operational/`` and ``documents/`` of the
external corpus are read — ``ground_truth/`` stays evaluation-only and never
enters the operational stores.
"""

from .generate import (  # noqa: F401
    CorpusOptions,
    SyntheticCorpus,
    generate_corpus,
    get_corpus_stats,
)
