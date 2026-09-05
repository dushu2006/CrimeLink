"""Persistence and single-writer safety tests for the embedded graph store.

The embedded store is write-through JSON on a NetworkX graph.  Two properties
matter for correctness under real deployment pressure:

* a flush must never tear or corrupt the snapshot, and must never leave a
  temporary file behind (the previous fixed ``.tmp`` name raced across
  processes);
* the store is single-writer: a second process on the same snapshot cannot
  merge its graph, so it is rejected up front instead of silently losing data.
"""

from __future__ import annotations

import json
import threading

import pytest

from app.adapters.graph.embedded import EmbeddedGraphStore
from app.domain.models import GraphNode


def _person(key: str, name: str) -> GraphNode:
    return GraphNode(
        provenance_key=key,
        label="Person",
        properties={"name": name, "case_id": "case-1"},
    )


def test_snapshot_persists_and_reloads(workspace):
    """The snapshot written by one store instance is reloaded by the next."""
    store = EmbeddedGraphStore(workspace)
    store.upsert_nodes([_person("person:1", "Ankit")])
    store.close()

    reloaded = EmbeddedGraphStore(workspace)
    try:
        node = reloaded.get_node("person:1")
        assert node is not None
        assert node.properties["name"] == "Ankit"
    finally:
        reloaded.close()


def test_second_writer_on_same_snapshot_is_rejected(workspace):
    """Two writers on one snapshot cannot be made safe, so the second fails fast."""
    store = EmbeddedGraphStore(workspace)
    try:
        with pytest.raises(RuntimeError, match="single-writer"):
            EmbeddedGraphStore(workspace)
    finally:
        store.close()


def test_flush_is_atomic_and_leaves_no_temp_files(workspace):
    """Every flush is a unique-temp + atomic-rename, leaving no temp residue."""
    store = EmbeddedGraphStore(workspace)
    try:
        for i in range(10):
            store.upsert_nodes([_person(f"person:{i}", f"person-{i}")])

        leftovers = list(workspace.graph_snapshot_path.parent.glob("graph.json.tmp-*"))
        assert leftovers == []

        raw = json.loads(workspace.graph_snapshot_path.read_text(encoding="utf-8"))
        assert len(raw["nodes"]) == 10
    finally:
        store.close()


def test_concurrent_flushes_from_threads_leave_a_valid_snapshot(workspace):
    """The embedded profile runs its pipeline in worker threads; a burst of
    concurrent flushes must never tear or corrupt the snapshot."""
    store = EmbeddedGraphStore(workspace)
    errors: list[BaseException] = []

    def write(worker: int) -> None:
        try:
            for i in range(50):
                store.upsert_nodes([_person(f"person:{worker}-{i}", f"person-{worker}-{i}")])
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        store.close()

    assert errors == []
    raw = json.loads(workspace.graph_snapshot_path.read_text(encoding="utf-8"))
    assert len(raw["nodes"]) == 4 * 50
