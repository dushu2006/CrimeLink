"""Tests for the synthetic development corpus, pseudonymization and AI gateway."""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
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
from app.db.base import utcnow  # noqa: E402
from app.config import Settings  # noqa: E402
from app.synthetic_corpus.names import (  # noqa: E402
    GIVEN_NAMES_F,
    GIVEN_NAMES_M,
    SURNAMES,
)
from app.synthetic_corpus.generate import (  # noqa: E402
    SIGHTING_SOURCES,
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


# ---------------------------------------------------------------------------
# Case population vs background population, event identity and validation
# (generate path — must stay semantically consistent with build_external.py)
# ---------------------------------------------------------------------------


def _opts_with_background(**overrides) -> CorpusOptions:
    opts = _opts()
    opts.background_person_count = 12
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


def test_background_population_is_structural_and_unrelated():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40))
    c.build()
    bg = [p for p in c.persons if p.subject_in_no_case]
    assert len(bg) == 12
    case_ids = {case.id for case in c.cases}
    assert case_ids  # sanity: cases exist
    for p in bg:
        # Structurally valid: a name, an address and always a phone.
        assert p.canonical_name.strip()
        assert p.addresses
        assert p.phone_ids
        # Never attached to a case.
        for case in c.cases:
            assert p.id not in case.person_ids
            for pid in p.phone_ids:
                assert pid not in case.phone_ids
            for vid in p.vehicle_ids:
                assert vid not in case.vehicle_ids
            for aid in p.account_ids:
                assert aid not in case.account_ids
    # Canonical representation is recorded in ground truth.
    assert set(c.ground_truth["background_person_ids"]) == {p.id for p in bg}
    gt = {p["id"]: p for p in c.ground_truth["background_population"]}
    assert all(gt[p.id]["subject_in_no_case"] for p in bg)


def test_background_population_never_enters_case_documents_or_graph():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=4))
    c.build()
    bg_names = {p.canonical_name for p in c.persons if p.subject_in_no_case}
    bg_phone_numbers = {
        ph.number
        for p in c.persons
        if p.subject_in_no_case
        for pid in p.phone_ids
        for ph in c.phones
        if ph.id == pid
    }
    bg_account_numbers = {
        a.number
        for p in c.persons
        if p.subject_in_no_case
        for aid in p.account_ids
        for a in c.accounts
        if a.id == aid
    }
    # A negative-class person appears in *no* case document.
    for doc in c.documents:
        content = doc["content"]
        for name in bg_names:
            assert name not in content
        for number in bg_phone_numbers:
            assert number not in content
        for number in bg_account_numbers:
            assert number not in content
    # ... and their activity is coherent but recorded only in ground truth.
    assert c.background_calls or c.background_transactions
    assert "background_calls" in c.ground_truth
    assert "background_transactions" in c.ground_truth


def test_background_assets_are_exclusively_owned():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40))
    c.build()
    bg_ids = {p.id for p in c.persons if p.subject_in_no_case}
    case_ids = {p.id for p in c.persons if not p.subject_in_no_case}
    for ph in c.phones:
        if any(oid in bg_ids for oid in ph.owner_ids):
            # A background phone must never be shared with a case person.
            assert not any(oid in case_ids for oid in ph.owner_ids)


def test_generated_corpus_passes_shared_validation():
    for bg in (0, 12):
        opts = _opts_with_background(person_count=40, background_person_count=bg)
        corpus = SyntheticCorpus(opts=opts)
        corpus.build()
        assert corpus.validate() == []


def test_event_identity_is_unique_per_occurrence():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=5))
    c.build()
    assert c.sightings  # the corpus must actually contain sightings
    ids = [s.id for s in c.sightings]
    assert len(ids) == len(set(ids))
    keys = [(s.person_id, s.ts.isoformat()) for s in c.sightings]
    assert len(keys) == len(set(keys))  # two sightings are never the same event
    # Sources are a property of the event, not its identity.
    assert {s.source for s in c.sightings} <= set(SIGHTING_SOURCES)


def test_person_phone_and_account_semantics_are_evidence_based():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=5))
    c.build()
    phones = {ph.id: ph for ph in c.phones}
    accounts = {a.id: a for a in c.accounts}
    # Every person<->asset link the corpus claims resolves to a real record.
    for p in c.persons:
        for pid in p.phone_ids:
            assert pid in phones and p.id in phones[pid].owner_ids
        for aid in p.account_ids:
            assert aid in accounts and p.id in accounts[aid].controller_ids


def test_transaction_traceability_person_account_transaction_account_person():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=5))
    c.build()
    assert c.transactions
    accounts = {a.id: a for a in c.accounts}
    persons = {p.id: p for p in c.persons}
    for txn in c.transactions:
        src = accounts[txn.src_account]
        dst = accounts[txn.dst_account]
        # Every transfer is anchored at both ends by a real holder...
        assert src.controller_ids and dst.controller_ids
        # ...and that holder exists, so Person -> Account -> Transaction ->
        # Account -> Person is traversable.
        for holder in (*src.controller_ids, *dst.controller_ids):
            assert holder in persons
        assert txn.amount > 0
        assert txn.id


def test_timestamps_are_coherent_and_never_in_the_future():
    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=5))
    c.build()
    now = utcnow()
    floor = now - timedelta(days=c.opts.time_window_days + 40)
    events = (
        [(call.ts, "call") for call in c.calls]
        + [(txn.ts, "transaction") for txn in c.transactions]
        + [(s.ts, "sighting") for s in c.sightings]
        + [(call.ts, "background call") for call in c.background_calls]
        + [(txn.ts, "background transaction") for txn in c.background_transactions]
    )
    assert events
    for ts, kind in events:
        assert ts <= now + timedelta(days=2), f"{kind} in the future: {ts}"
        assert ts >= floor, f"{kind} predates the corpus window: {ts}"


def test_documents_emit_ownership_and_sighting_edges():
    from app.domain.enums import EntityType, SourceConfidence
    from app.pipeline.adapters.protocol import DocumentMeta
    from app.pipeline.adapters.registry import get_adapter
    from app.pipeline.extraction.deterministic import extract_deterministic

    c = SyntheticCorpus(opts=_opts_with_background(person_count=40, case_count=4))
    c.build()
    ch_docs = [d for d in c.documents if d["document_type"].value == "CRIMINAL_HISTORY"]
    surv_docs = [d for d in c.documents if d["document_type"].value == "SURVEILLANCE"]
    assert ch_docs and surv_docs

    rel_types: set[str] = set()
    for doc in ch_docs:
        meta = DocumentMeta(
            doc_id=doc["doc_id"], case_id=doc["case"].id, filename=doc["filename"],
            document_type=doc["document_type"],
            source_confidence=SourceConfidence.UNVERIFIED, language_hint="en", extra={},
        )
        normalized = get_adapter(doc["document_type"]).parse(
            doc["content"].encode("utf-8"), meta
        )
        _, relations = extract_deterministic(normalized)
        rel_types.update(r.rel_type for r in relations)
    # Ownership evidence yields the canonical ownership edges.
    assert "USES_PHONE" in rel_types
    assert "OWNS_VEHICLE" in rel_types
    assert "OWNS_ACCOUNT" in rel_types

    event_keys: set[str] = set()
    for doc in surv_docs:
        meta = DocumentMeta(
            doc_id=doc["doc_id"], case_id=doc["case"].id, filename=doc["filename"],
            document_type=doc["document_type"],
            source_confidence=SourceConfidence.UNVERIFIED, language_hint="en", extra={},
        )
        normalized = get_adapter(doc["document_type"]).parse(
            doc["content"].encode("utf-8"), meta
        )
        entities, relations = extract_deterministic(normalized)
        for ent in entities:
            if ent.entity_type == EntityType.EVENT:
                event_keys.add(ent.normalized_value)
                assert ent.attributes.get("timestamp")
        assert any(r.rel_type == "LOCATED_AT" for r in relations)
    # Every sighting became its own event node (no global PATROL/CCTV collapse).
    assert len(event_keys) == len(c.sightings)
