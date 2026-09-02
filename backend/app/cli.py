"""CrimeLink operator CLI.

Single entry point for operator actions that are deliberately *not* part of
application startup::

    python -m app.cli ingest-synthetic [--mode generate|external] [options]

``ingest-synthetic`` honours ``CRIMELINK_SYNTHETIC_DATA_MODE``:

* ``generate`` (the default) runs the deterministic in-process generator —
  equivalent to ``python -m app.synthetic_corpus.generate``.
* ``external`` reads the filesystem corpus at ``CRIMELINK_SYNTHETIC_DATA_ROOT``
  (e.g. the sibling ``../CrimeLink_Synthetic_Corpus_v1`` checkout) — equivalent
  to ``python -m app.synthetic_corpus.external``.

In both modes records enter through the standard ingestion pipeline with
``source_environment=synthetic`` provenance; nothing is imported at startup.
"""

from __future__ import annotations

import argparse
import sys

from app.logging import get_logger

log = get_logger("crimelink.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="CrimeLink operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest-synthetic",
        help="Ingest synthetic development data through the standard pipeline",
    )
    ingest.add_argument(
        "--mode",
        choices=["generate", "external"],
        default=None,
        help="Synthetic data mode (default: CRIMELINK_SYNTHETIC_DATA_MODE).",
    )
    ingest.add_argument(
        "--root",
        default=None,
        help="External corpus root (external mode only; default: "
        "CRIMELINK_SYNTHETIC_DATA_ROOT, resolved relative to the repo).",
    )
    ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="External mode: discover/validate/classify only, write nothing.",
    )
    ingest.add_argument("--seed", type=int, default=None, help="Generate mode: RNG seed.")
    ingest.add_argument("--persons", type=int, default=None, help="Generate mode: person count.")
    ingest.add_argument("--cases", type=int, default=None, help="Generate mode: case count.")
    ingest.add_argument(
        "--wait",
        type=float,
        default=600.0,
        metavar="SECONDS",
        help="External mode: wait for embedded-broker jobs and report pipeline "
        "outcomes (0 disables).",
    )
    ingest.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="Safety override for non-dev environments.",
    )

    args = parser.parse_args(argv)

    if args.command == "ingest-synthetic":
        from app.config import get_settings

        mode = args.mode or get_settings().synthetic_data_mode
        if mode == "external":
            from app.synthetic_corpus.external import main as external_main

            forward: list[str] = []
            if args.root:
                forward += ["--root", args.root]
            if args.dry_run:
                forward.append("--dry-run")
            forward += ["--wait", str(args.wait)]
            if args.yes_i_am_sure:
                forward.append("--yes-i-am-sure")
            return external_main(forward)

        # mode == "generate": delegate to the existing deterministic generator.
        if args.root or args.dry_run:
            print(
                "ERROR: --root/--dry-run only apply to --mode external. Running the "
                "generator instead is not what those flags mean.",
                file=sys.stderr,
            )
            return 2
        from app.synthetic_corpus.generate import main as generate_main

        forward = []
        if args.seed is not None:
            forward += ["--seed", str(args.seed)]
        if args.persons is not None:
            forward += ["--persons", str(args.persons)]
        if args.cases is not None:
            forward += ["--cases", str(args.cases)]
        if args.yes_i_am_sure:
            forward.append("--yes-i-am-sure")
        return generate_main(forward)

    return 2  # pragma: no cover - argparse enforces valid commands


if __name__ == "__main__":
    raise SystemExit(main())
