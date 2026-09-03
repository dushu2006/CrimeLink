"""Domain rules: provenance keys, normalisation, transliteration, caps."""

from __future__ import annotations

import pytest

from app.domain.enums import EntityType
from app.domain.models import GraphEdge, GraphNode
from app.domain.normalize import (
    combined_similarity,
    normalize_account,
    normalize_ifsc,
    normalize_name,
    normalize_phone,
    normalize_plate,
    parse_amount,
    transliterate_devanagari,
)
from app.domain.provenance import (
    GENESIS_HASH,
    candidate_key,
    chain_hash,
    content_hash,
    edge_key,
    sha256_hex,
)


# --------------------------------------------------------------------------- #
# Provenance (PRD 9.1)
# --------------------------------------------------------------------------- #

def test_provenance_key_is_sha256_of_case_doc_type_value():
    key = candidate_key("case-1", "doc-1", "Phone", "+919829012345")
    assert key == sha256_hex("case-1", "doc-1", "Phone", "+919829012345")
    assert len(key) == 64


def test_provenance_key_is_stable_and_case_scoped():
    a = candidate_key("case-1", "doc-1", "Phone", "+919829012345")
    b = candidate_key("case-1", "doc-1", "Phone", "+919829012345")
    c = candidate_key("case-2", "doc-1", "Phone", "+919829012345")
    assert a == b
    assert a != c, "the same number in two cases must not share a node"


def test_edge_key_is_deterministic_and_discriminated():
    a = edge_key("CALLED", "pk-a", "pk-b")
    b = edge_key("CALLED", "pk-a", "pk-b")
    c = edge_key("CALLED", "pk-b", "pk-a")
    assert a == b
    assert a != c


def test_content_hash_is_the_sha256_of_the_bytes():
    import hashlib

    assert content_hash(b"crimelink") == hashlib.sha256(b"crimelink").hexdigest()


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+91 98290 12345", "+919829012345"),
        ("09829012345", "+919829012345"),
        ("9829012345", "+919829012345"),
        ("+91-9829-012-345", "+919829012345"),
    ],
)
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


def test_invalid_phone_is_rejected_not_guessed():
    assert normalize_phone("12345") is None
    assert normalize_phone("") is None


def test_plate_normalisation_is_uppercase_and_compact():
    assert normalize_plate("rj 14 ab 1234") == "RJ14AB1234"


def test_ifsc_and_account_normalisation():
    assert normalize_ifsc("sbin0001234") == "SBIN0001234"
    assert normalize_account("5010-0234-5678-90") == "50100234567890"
    assert normalize_account("XX90857229") == "XX90857229"
    assert normalize_account("short") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("₹48,000", 48000.0),
        ("Rs. 48000", 48000.0),
        ("48000", 48000.0),  # bare numeric (CSV amount column)
        ("2.5 lakh", 250000.0),
        ("1 crore", 10000000.0),
        ("no money here", None),
    ],
)
def test_amount_parsing(raw, expected):
    assert parse_amount(raw) == expected


def test_name_normalisation_collapses_honorifics_and_spacing():
    assert normalize_name("  Shri   RAMESH  Kumar  YADAV ") == "ramesh kumar yadav"
    assert normalize_name("Smt. Sunita Devi") == "sunita devi"
    # "Kumar" is a middle name in Indian names, never an honorific to strip.
    assert normalize_name("Kumar Ramesh") == "kumar ramesh"


def test_transliteration_maps_devanagari_to_iso15919():
    assert transliterate_devanagari("रमेश") == "ramēś"
    assert transliterate_devanagari("कुमार") == "kumār"


def test_cross_script_matching_covers_the_common_spelling_drift():
    for hindi, latin in [
        ("सुनीता देवी", "Sunita Devi"),
        ("राम प्रसाद यादव", "Ram Prasad Yadav"),
        ("कैलाश चंद", "Kailash Chand"),
        ("अनील शर्मा", "Anil Sharma"),
        ("राजेश खन्ना", "Rajesh Khanna"),
        ("मीना राठौड़", "Meena Rathore"),
        ("सुरेश मेहता", "Suresh Mehta"),
    ]:
        assert combined_similarity(hindi, latin) >= 0.85, (hindi, latin)


def test_devanagari_matches_latin_equivalent():
    score = combined_similarity("रमेश कुमार यादव", "Ramesh Kumar Yadav")
    assert score >= 0.85, (
        "a Devanagari name and its Latin transliteration must clear the fuzzy "
        "threshold, otherwise Hindi and English FIRs about the same person "
        "would never be offered for review"
    )


def test_unrelated_names_score_below_threshold():
    assert combined_similarity("Sunita Devi", "Vikram Singh Rathore") < 0.85


# --------------------------------------------------------------------------- #
# Model-level guarantees (G1)
# --------------------------------------------------------------------------- #

def test_graph_edge_cannot_exist_without_a_source_document():
    from app.errors import UnevidencedGraphWriteError

    with pytest.raises(UnevidencedGraphWriteError):
        GraphEdge(
            source_key="a",
            target_key="b",
            rel_type="CALLED",
            properties={"confidence": 0.9},  # no source_doc_id
        )


def test_graph_node_can_be_built_with_evidence():
    node = GraphNode(
        provenance_key=candidate_key("c", "d", "Person", "ramesh yadav"),
        label=EntityType.PERSON.value,
        properties={"name": "Ramesh Yadav", "source_doc_id": "doc-1", "confidence": 0.8},
    )
    assert node.name == "Ramesh Yadav"


# --------------------------------------------------------------------------- #
# Configuration caps
# --------------------------------------------------------------------------- #

def test_nlp_confidence_cap_is_below_one(settings):
    assert settings.nlp_max_confidence <= 0.8


def test_graph_expand_depth_cap_is_two(settings):
    assert settings.graph_max_expand_depth == 2


def test_hash_chain_genesis_and_link():
    import hashlib

    row = chain_hash(GENESIS_HASH, '{"a":1}')
    expected = hashlib.sha256((GENESIS_HASH + '{"a":1}').encode()).hexdigest()
    assert row == expected
    assert row != GENESIS_HASH
    # The same row under a different predecessor must hash differently.
    assert chain_hash(row, '{"a":1}') != row
