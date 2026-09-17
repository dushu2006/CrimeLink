"""Orphan and referential-integrity audit of the seeded dataset (§17).

Checks the specific orphan categories the audit brief names, plus the ones that
matter for the CASE → EVIDENCE → SOURCE → FILE chain:

  * evidence record with no source (no storage key / no dataset file row)
  * source record (SourceReference) pointing at a document that does not exist
  * file in object storage with no source record behind it
  * finding with no evidence
  * evidence with no case
  * relationship edge with no supporting record
  * person / entity with an invalid case or document reference
  * document whose recorded hash does not match the stored object

Read-only.  Exits non-zero if any category is non-empty.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.container import get_container  # noqa: E402
from app.datasets import registry  # noqa: E402
from app.db.models import (  # noqa: E402
    Case,
    CaseDocument,
    DatasetFile,
    InvestigationFinding,
    SourceReference,
)
from app.db.session import async_session, dispose_engines  # noqa: E402
from app.domain.provenance import content_hash  # noqa: E402


class _Snap:
    """Minimal read-only view of a graph.json node or edge."""

    __slots__ = ("provenance_key", "label", "properties", "source", "target", "key")

    def __init__(self, key, label, properties, *, target=None, key_name=None) -> None:
        self.provenance_key = key
        self.label = label
        self.properties = properties
        self.source = key if target is not None else None
        self.target = target
        self.key = key_name


def classify(
    *,
    cases: set[str],
    documents: list[CaseDocument],
    dataset_files: list[DatasetFile],
    references: list[SourceReference],
    findings: list[InvestigationFinding],
    nodes: list[_Snap],
    edges: list[_Snap],
    stored_keys: set[str] | None = None,
    hash_mismatch: list[str] | None = None,
    unreadable: list[str] | None = None,
) -> list[tuple[str, int, list[str]]]:
    """Pure referential-integrity classification over already-fetched inputs.

    Split out of :func:`audit` so the checks themselves can be tested against
    deliberately broken inputs.  A tool that only ever runs against live data
    cannot show that it would notice a problem.
    """
    problems: list[tuple[str, int, list[str]]] = []

    def report(name: str, offenders: list[str]) -> None:
        problems.append((name, len(offenders), offenders[:5]))

    doc_ids = {d.id for d in documents}

    # 1. evidence record with no source
    report("evidence record with no storage key", [d.id for d in documents if not d.storage_key])
    report(
        "evidence record with no dataset-file row",
        [d.id for d in documents if not any(f.doc_id == d.id for f in dataset_files)],
    )

    # 2. source record pointing at a missing document or case
    report("source reference -> missing document",
           [r.id for r in references if r.doc_id and r.doc_id not in doc_ids])
    report("source reference -> missing case",
           [r.id for r in references if r.case_id and r.case_id not in cases])

    # 3. file in storage with no source record behind it
    referenced_paths = {r.origin_file for r in references if r.origin_file}
    df_paths = {f.relative_path for f in dataset_files}
    known_keys = {d.storage_key for d in documents if d.storage_key}
    report("source reference to an unregistered path",
           sorted(referenced_paths - df_paths - known_keys))

    # 4. finding with no evidence
    findings_no_ev = []
    for f in findings:
        ev = f.evidence or []
        if not ev:
            findings_no_ev.append(f.id)
            continue
        cited = {
            str(i.get("doc_id")) for i in ev if isinstance(i, dict) and i.get("doc_id")
        } | {str(x) for x in ev if isinstance(x, str)}
        if not (cited & doc_ids):
            findings_no_ev.append(f.id)
    report("finding with no resolvable evidence", findings_no_ev)
    report("finding -> missing case", [f.id for f in findings if f.case_id not in cases])

    # 5. evidence with no case
    report("evidence record with no case",
           [d.id for d in documents if not d.case_id or d.case_id not in cases])
    report("dataset-file row -> missing document",
           [f.id for f in dataset_files if not f.doc_id or f.doc_id not in doc_ids])

    # 6. relationship edge with no supporting record
    node_keys = {n.provenance_key for n in nodes}
    edges_no_doc, edges_bad_endpoints = [], []
    for e in edges:
        props = e.properties or {}
        docs = props.get("source_doc_ids") or (
            [props["source_doc_id"]] if props.get("source_doc_id") else []
        )
        edge_id = getattr(e, "key", None) or f"{e.source}->{e.target}"
        if not docs or not (set(map(str, docs)) & doc_ids):
            edges_no_doc.append(edge_id)
        if e.source not in node_keys or e.target not in node_keys:
            edges_bad_endpoints.append(edge_id)
    report("relationship edge with no supporting document", edges_no_doc)
    report("relationship edge with an unknown endpoint", edges_bad_endpoints)

    # 7. entity with an invalid case or document reference
    nodes_bad_case, nodes_bad_doc = [], []
    for n in nodes:
        props = n.properties or {}
        for cid in props.get("case_ids") or []:
            if cid not in cases:
                nodes_bad_case.append(n.provenance_key)
                break
        docs = props.get("source_doc_ids") or []
        if docs and not (set(map(str, docs)) & doc_ids):
            nodes_bad_doc.append(n.provenance_key)
    report("entity -> unknown case", nodes_bad_case)
    report("entity -> unknown source document", nodes_bad_doc)

    # duplicate document rows for one file
    report("duplicate document rows for one stored file", [
        key for key, c in Counter(d.storage_key for d in documents if d.storage_key).items()
        if c > 1
    ])

    # 8. storage integrity
    report("stored object unreadable", unreadable or [])
    report("recorded hash does not match stored bytes", hash_mismatch or [])

    # 9. every stored object is registered
    if stored_keys is not None:
        registered = {d.storage_key for d in documents if d.storage_key} | df_paths
        report("stored object with no document/dataset-file record",
               sorted(stored_keys - registered))

    return problems


async def audit() -> int:
    container = get_container()
    bucket = container.settings.minio_bucket_documents
    problems: list[tuple[str, int, list[str]]] = []

    def report(name: str, offenders: list[str]) -> None:
        problems.append((name, len(offenders), offenders[:5]))

    async with async_session() as session:
        dataset = await registry.active_dataset(session)
        if dataset is None:
            print("FATAL: no active dataset")
            return 1
        ds_id = dataset.id
        print(f"active dataset: {ds_id}\n")

        cases = {c.id for c in (await session.execute(select(Case))).scalars()}
        documents = list(
            (
                await session.execute(
                    select(CaseDocument).where(CaseDocument.dataset_id == ds_id)
                )
            ).scalars()
        )
        doc_ids = {d.id for d in documents}
        dataset_files = list(
            (
                await session.execute(
                    select(DatasetFile).where(DatasetFile.dataset_id == ds_id)
                )
            ).scalars()
        )
        references = list(
            (
                await session.execute(
                    select(SourceReference).where(SourceReference.dataset_id == ds_id)
                )
            ).scalars()
        )
        findings = list((await session.execute(select(InvestigationFinding))).scalars())
        # The embedded profile keeps the graph in graph.json, not in relational
        # tables.  The store takes a single-writer process lock, which the
        # running API holds, so a read-only audit reads the snapshot file
        # directly instead of contending for the lock.
        import json

        snapshot_path = Path(container.settings.graph_snapshot_path)
        nodes, edges = [], []
        if snapshot_path.is_file():
            raw_graph = json.loads(snapshot_path.read_text(encoding="utf-8"))
            # graph.json stores the label and rel type inside `data` under
            # private keys, not as top-level fields.
            nodes = [
                _Snap(n["pk"], (n.get("data") or {}).get("_label"), n.get("data") or {})
                for n in raw_graph.get("nodes", [])
            ]
            edges = [
                _Snap(e.get("source"), (e.get("data") or {}).get("_rel"), e.get("data") or {},
                      target=e.get("target"), key_name=e.get("key"))
                for e in raw_graph.get("edges", [])
            ]

        print(
            f"cases={len(cases)} documents={len(documents)} dataset_files={len(dataset_files)} "
            f"references={len(references)} findings={len(findings)} "
            f"nodes={len(nodes)} edges={len(edges)}\n"
        )

        dataset_files = list(
            (
                await session.execute(
                    select(DatasetFile).where(DatasetFile.dataset_id == ds_id)
                )
            ).scalars()
        )

    # Storage checks are done outside the session: they touch the object store,
    # not the database.
    mismatch, unreadable = [], []
    for d in documents:
        try:
            raw = container.object_store.get(bucket, d.storage_key)
        except Exception:
            unreadable.append(d.id)
            continue
        if content_hash(raw) != d.content_hash:
            mismatch.append(d.id)
    stored_keys = set(container.object_store.list_keys(bucket))

    problems = classify(
        cases=cases,
        documents=documents,
        dataset_files=dataset_files,
        references=references,
        findings=findings,
        nodes=nodes,
        edges=edges,
        stored_keys=stored_keys,
        hash_mismatch=mismatch,
        unreadable=unreadable,
    )

    print("RESULTS")
    print("-" * 72)
    total = 0
    for name, count, sample in problems:
        total += count
        mark = "OK  " if count == 0 else "BAD "
        print(f"  {mark} {count:>5}  {name}" + (f"   e.g. {sample}" if sample else ""))
    print("-" * 72)
    print(f"total orphan / integrity problems: {total}")
    return 1 if total else 0


def main() -> int:
    try:
        return asyncio.run(audit())
    finally:
        try:
            asyncio.run(dispose_engines())
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
