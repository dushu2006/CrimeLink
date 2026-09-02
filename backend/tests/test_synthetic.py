"""Tests for the synthetic development corpus, pseudonymization and AI gateway."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="crimelink-tests-syn-"))
os.environ["CRIMELINK_PROFILE"] = "embedded"
os.environ["CRIMELINK_DATA_DIR"] = str(_TMP / "data")
os.environ["CRIMELINK_OBJECT_STORE_DIR"] = str(_TMP / "objects")
os.environ["CRIMELINK_LOG_LEVEL"] = "WARNING"

from app.ai.pseudonymize import (  # noqa: E402
    PseudonymMap,
    apply_pseudonymization_to_context,
)
from app.config import Settings  # noqa: E402
from app.synthetic_corpus.names import (  # noqa: E402
    GIVEN_NAMES_F,
    GIVEN_NAMES_M,
    SURNAMES,
)
from app.synthetic_corpus.generate import (  # noqa: E402
    CorpusOptions,
    SyntheticCorpus,
)


def _opts(**overrides) -> CorpusOptions:
    base = dict(
        seed=20260902,
        person_count=30,
        case_count=4,
        phone_count=40,
        vehicle_count=12,
        location_count=10,
        account_count=15,
        organization_count=6,
        document_count=25,
        call_count=100,
        transaction_count=60,
        bridge_count=3,
        network_count=3,
        missing_field_rate=0.12,
        duplicate_rate=0.08,
        name_variation_rate=0.25,
    )
    base.update(overrides)
    return CorpusOptions(**base)


def test_deterministic_seed_produces_same_corpus():
    c1 = SyntheticCorpus(opts=_opts())
    c1.build()
    c2 = SyntheticCorpus(opts=_opts())
    c2.build()
    assert len(c1.persons) == len(c2.persons) == 30
    assert [p.canonical_name for p in c1.persons] == [p.canonical_name for p in c2.persons]
    assert len(c1.calls) == len(c2.calls)
    # same seed => same ground truth bridges
    assert set(c1.ground_truth["bridge_person_ids"]) == set(c2.ground_truth["bridge_person_ids"])


def test_different_seeds_produce_different_corpora():
    c1 = SyntheticCorpus(opts=_opts(seed=1))
    c1.build()
    c2 = SyntheticCorpus(opts=_opts(seed=2))
    c2.build()
    names1 = {p.canonical_name for p in c1.persons}
    names2 = {p.canonical_name for p in c2.persons}
    # There should be SOME difference in naming (astronomically likely)
    assert names1 != names2


def test_configurable_entity_counts_scale_without_code_change():
    for n in (20, 40, 60, 100):
        c = SyntheticCorpus(opts=_opts(person_count=n, case_count=max(3, n // 15)))
        c.build()
        assert len(c.persons) == n
        for p in c.persons:
            assert p.canonical_name  # never empty


def test_indian_names_are_used():
    c = SyntheticCorpus(opts=_opts(person_count=50))
    c.build()
    all_given = set(GIVEN_NAMES_M) | set(GIVEN_NAMES_F)
    all_sur = set(SURNAMES)
    # Almost every generated surname should come from the gazetteer
    surnames_hit = 0
    for p in c.persons:
        parts = p.canonical_name.split()
        if len(parts) >= 2 and parts[-1] in all_sur:
            surnames_hit += 1
    assert surnames_hit >= 45


def test_name_variations_exist():
    # High variation rate guarantees variations show up in documents
    c = SyntheticCorpus(opts=_opts(name_variation_rate=0.9, person_count=20))
    c.build()
    docs = "\n".join(d["content"] for d in c.documents if isinstance(d.get("content"), str))
    # Some variations like initials or uppercase should appear
    has_initial = "." in docs and any(f"{p.canonical_name.split()[0][0]}." in docs for p in c.persons if p.canonical_name)
    has_upper = any(p.canonical_name.upper() in docs for p in c.persons)
    assert has_initial or has_upper


def test_missing_fields_are_introduced():
    c = SyntheticCorpus(opts=_opts(missing_field_rate=0.5, person_count=60))
    c.build()
    missing_dob = sum(1 for p in c.persons if p.dob is None)
    missing_father = sum(1 for p in c.persons if p.father_or_spouse_name is None)
    # with 50% missing rate at least some should be missing
    assert missing_dob > 5
    assert missing_father > 5


def test_multiple_networks_and_bridges_exist():
    c = SyntheticCorpus(opts=_opts(network_count=3, bridge_count=3, person_count=40))
    c.build()
    bridges = [p for p in c.persons if p.is_bridge]
    assert len(bridges) >= 2
    # bridges must have phones (so they actually link networks through evidence)
    for b in bridges:
        assert b.phone_ids or b.vehicle_ids or b.account_ids
    # scenarios include the expected types
    scenario_types = {s["type"] for s in c.ground_truth["scenarios"]}
    assert "bridge_individual" in scenario_types
    assert "structuring" in scenario_types
    assert "identity_resolution" in scenario_types


def test_cross_case_entities_exist():
    c = SyntheticCorpus(opts=_opts(case_count=5, person_count=40, bridge_count=3))
    c.build()
    # Build an entity->cases map
    by_person: dict[str, set[str]] = {}
    for case in c.cases:
        for pid in case.person_ids:
            by_person.setdefault(pid, set()).add(case.id)
    cross = [pid for pid, cases in by_person.items() if len(cases) > 1]
    # With bridges + overlapping networks, there MUST be cross-case people
    assert len(cross) >= 1


def test_temporal_relationships_exist():
    c = SyntheticCorpus(opts=_opts(call_count=200, transaction_count=100))
    c.build()
    assert len(c.calls) > 0
    assert len(c.transactions) > 0
    # Calls must have timestamps spread across a window
    timestamps = sorted(call.ts for call in c.calls)
    assert (timestamps[-1] - timestamps[0]).total_seconds() > 0


def test_ground_truth_written_to_separate_structure_not_entities():
    c = SyntheticCorpus(opts=_opts())
    c.build()
    assert "networks" in c.ground_truth
    assert "bridge_person_ids" in c.ground_truth
    assert "scenarios" in c.ground_truth
    # Synthetic person canonical names should contain no marker of being a
    # "criminal" / "guilty" — neutrality test
    for p in c.persons:
        assert "criminal" not in p.canonical_name.lower()
        assert "guilty" not in p.canonical_name.lower()


# ---------------------------------------------------------------------------
# Pseudonymization
# ---------------------------------------------------------------------------


def test_pseudonymization_is_deterministic_within_map():
    pmap = PseudonymMap()
    a = pmap.pseudonymize("person:123", "Person")
    b = pmap.pseudonymize("person:123", "Person")
    c = pmap.pseudonymize("phone:456", "Phone")
    assert a == b
    assert a.startswith("PERSON_")
    assert c.startswith("PHONE_")
    assert a != c
    assert pmap.resolve(a) == "person:123"
    assert pmap.resolve(c) == "phone:456"


def test_pseudonymization_strips_sensitive_fields():
    nodes = [
        {"provenance_key": "pk1", "label": "Person", "properties": {"name": "Ramesh Kumar", "confidence": 0.9}},
        {"provenance_key": "pk2", "label": "Phone", "properties": {"number": "+919876543210", "confidence": 1.0}},
    ]
    edges = [
        {"source_key": "pk1", "target_key": "pk2", "rel_type": "USES_PHONE", "confidence": 0.9,
         "source_doc_id": "doc1"},
    ]
    pmap = PseudonymMap()
    safe_nodes, safe_edges = apply_pseudonymization_to_context(nodes, edges, pmap)
    assert safe_nodes[0]["id"] == "PERSON_001"
    assert safe_nodes[1]["id"] == "PHONE_001"
    assert "name" not in safe_nodes[0]
    assert "number" not in safe_nodes[1]
    assert safe_edges[0]["source"] == "PERSON_001"
    assert safe_edges[0]["target"] == "PHONE_001"


def test_pseudonymization_does_not_send_raw_pii():
    nodes = [
        {"provenance_key": "pk1", "label": "Person", "properties": {"name": "Secret Name"}},
    ]
    edges: list[dict] = []
    pmap = PseudonymMap()
    out_nodes, _ = apply_pseudonymization_to_context(nodes, edges, pmap)
    assert "Secret Name" not in str(out_nodes)


# ---------------------------------------------------------------------------
# AI model router / config
# ---------------------------------------------------------------------------


def test_ai_role_available_reports_false_when_no_key():
    s = Settings(ai_api_key=None, environment="dev")
    assert s.ai_role_available("reasoning") is False
    assert s.ai_role_available("extraction") is False


def test_ai_role_config_picks_up_per_role_overrides():
    s = Settings(
        ai_api_key="global-key",
        ai_reasoning_api_key="reasoning-key",
        ai_reasoning_model="reason-model",
        environment="dev",
    )
    cfg = s.role_config("reasoning")
    assert cfg["api_key"] == "reasoning-key"
    assert cfg["model"] == "reason-model"
    cfg_extr = s.role_config("extraction")
    assert cfg_extr["api_key"] == "global-key"  # falls back to global
