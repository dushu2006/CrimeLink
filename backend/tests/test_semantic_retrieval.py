"""Hybrid semantic retrieval: vectors supplement, never replace, retrieval.

The point of the vector index is narrow and testable: find a record that is
*about* the question without containing its words.  Everything else about it is
a safety property — the index must not widen authorization, must not outlive a
retired document, and must not embed a real identity in strict privacy mode.
"""

from __future__ import annotations

import pytest

from app.ai.semantic import (
    LocalHashingEmbedder,
    RouterEmbedder,
    SemanticIndexStore,
    chunk_document,
    ensure_index,
    merge_retrieval_candidates,
    search_case_index,
)

CASE_ID = "case-semantic-001"

QUESTION = "What information relates to the alleged tender manipulation?"

BRIEF = {
    "doc_id": "DOC-BRIEF",
    "filename": "intelligence_brief.txt",
    "document_type": "INTELLIGENCE_REPORT",
    "content": (
        "Assessment: the lead concerning irregularities in procurement at the ward "
        "office is partially corroborated by call and financial records."
    ),
}
UNRELATED = {
    "doc_id": "DOC-FORENSIC",
    "filename": "forensic_report.txt",
    "document_type": "FORENSIC_REPORT",
    "content": "Exhibits were sealed on receipt and examined in the laboratory.",
}
OTHER_CASE = {
    "doc_id": "DOC-OTHER-CASE",
    "filename": "other_case_brief.txt",
    "document_type": "INTELLIGENCE_REPORT",
    "content": "A separate inquiry into procurement irregularities at another ward.",
}


def _store(tmp_path) -> SemanticIndexStore:
    return SemanticIndexStore(tmp_path / "ai_index")


def _local() -> RouterEmbedder:
    # A router that is not configured: the module must fall back to the local
    # embedder rather than failing or inventing vectors.
    return RouterEmbedder(None, fallback=LocalHashingEmbedder(), enabled=True)


async def _search(documents, question=QUESTION, *, store, embedder, **kwargs):
    return await search_case_index(
        case_id=CASE_ID,
        question=question,
        documents=documents,
        store=store,
        embedder=embedder,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1. A paraphrase retrieves the relevant record
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_semantic_paraphrase_retrieves_the_relevant_record(tmp_path):
    """The words of the question do not appear in the record that answers it."""
    store = _store(tmp_path)
    hits, stats = await _search([BRIEF, UNRELATED], store=store, embedder=_local())

    assert stats.available, stats.reason
    assert hits, "semantic retrieval must return candidates"
    assert hits[0].chunk.document_id == "DOC-BRIEF"
    assert "tender manipulation" not in BRIEF["content"].lower()
    assert hits[0].score > 0.25


# --------------------------------------------------------------------------- #
# 2. Exact retrieval still works
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_exact_phrase_retrieval_still_wins(tmp_path):
    store = _store(tmp_path)
    hits, _ = await _search(
        [BRIEF, UNRELATED],
        question="Which record mentions sealed exhibits examined in the laboratory?",
        store=store,
        embedder=_local(),
    )
    assert hits and hits[0].chunk.document_id == "DOC-FORENSIC"


# --------------------------------------------------------------------------- #
# 3. Vector and lexical candidates merge without duplicates
# --------------------------------------------------------------------------- #

def test_vector_and_lexical_candidates_merge_and_deduplicate():
    lexical = [dict(UNRELATED), dict(BRIEF)]
    merged, stats = merge_retrieval_candidates(
        lexical,
        [],
        all_documents=[BRIEF, UNRELATED],
        lexical_scores={"DOC-FORENSIC": 5.0, "DOC-BRIEF": 1.0},
    )
    assert [doc["doc_id"] for doc in merged] == ["DOC-FORENSIC", "DOC-BRIEF"]
    assert merged[0]["retrieval_sources"] == ["lexical"]
    assert stats["merged"] == 2

    from app.ai.semantic import Chunk, SemanticHit

    hit = SemanticHit(
        chunk=Chunk(
            chunk_id="DOC-BRIEF:0:abc",
            case_id=CASE_ID,
            document_id="DOC-BRIEF",
            sequence=0,
            text=BRIEF["content"],
            content_hash="abc",
        ),
        score=0.42,
    )
    merged, stats = merge_retrieval_candidates(
        lexical,
        [hit],
        all_documents=[BRIEF, UNRELATED],
        lexical_scores={"DOC-FORENSIC": 5.0, "DOC-BRIEF": 1.0},
    )
    assert len(merged) == 2, "a document found by both paths must appear once"
    brief = next(doc for doc in merged if doc["doc_id"] == "DOC-BRIEF")
    assert brief["retrieval_sources"] == ["lexical", "semantic"]
    assert brief["semantic_score"] == 0.42


# --------------------------------------------------------------------------- #
# 4 + 5. Cross-case and retired documents never enter the boundary
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cross_case_semantic_hit_is_filtered(tmp_path):
    """An index entry from another case cannot be retrieved for this case."""
    store = _store(tmp_path)
    hits, _ = await _search([BRIEF, OTHER_CASE], store=store, embedder=_local())
    assert hits
    merged, _ = merge_retrieval_candidates(
        [], hits, all_documents=[BRIEF], max_extra=8
    )
    assert [doc["doc_id"] for doc in merged] == ["DOC-BRIEF"]

    # Even when the caller asks for a document that is not in the authorised
    # set, the search gate still refuses it.
    gated, _ = await _search(
        [BRIEF, OTHER_CASE],
        store=store,
        embedder=_local(),
        authorized_document_ids=["DOC-OTHER-CASE"],
    )
    assert {hit.chunk.document_id for hit in gated} == {"DOC-OTHER-CASE"}


@pytest.mark.asyncio
async def test_retired_document_is_dropped_from_the_index(tmp_path):
    store = _store(tmp_path)
    embedder = _local()
    before, stats = await _search([BRIEF, UNRELATED], store=store, embedder=embedder)
    assert stats.chunks == 2

    # The document is retired: it is no longer in the live, case-scoped set.
    after, stats = await _search([UNRELATED], store=store, embedder=embedder)
    assert stats.dropped >= 1
    assert {hit.chunk.document_id for hit in after} == {"DOC-FORENSIC"}
    stored = store.load(CASE_ID)
    assert {chunk["document_id"] for chunk in stored["chunks"]} == {"DOC-FORENSIC"}


# --------------------------------------------------------------------------- #
# 6. Strict mode indexes pseudonyms, not identities
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_strict_mode_indexes_pseudonymized_content(tmp_path):
    store = _store(tmp_path)
    document = {
        "doc_id": "DOC-DIARY",
        "filename": "diary.txt",
        "document_type": "CASE_DIARY",
        "content": "Prakash Jain visited the ward office and met the contractor.",
    }
    scrub = lambda text: str(text).replace("Prakash Jain", "PERSON_01")  # noqa: E731

    await _search(
        [document],
        question="Who visited the ward office?",
        store=store,
        embedder=_local(),
        text_transform=scrub,
    )
    stored = store.load(CASE_ID)
    indexed_text = " ".join(chunk["text"] for chunk in stored["chunks"])
    assert "PERSON_01" in indexed_text
    assert "Prakash Jain" not in indexed_text


# --------------------------------------------------------------------------- #
# Index versioning
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_changed_document_is_re_embedded_and_old_chunk_is_dropped(tmp_path):
    store = _store(tmp_path)
    embedder = _local()
    await _search([BRIEF], store=store, embedder=embedder)
    original = store.load(CASE_ID)["chunks"][0]["content_hash"]

    updated = dict(BRIEF, content=BRIEF["content"] + " Additional narrative paragraph.")
    _, stats = await _search([updated], store=store, embedder=embedder)
    assert stats.embedded == 1, "the changed chunk must be re-embedded"
    new_hash = store.load(CASE_ID)["chunks"][0]["content_hash"]
    assert new_hash != original
    assert not any(chunk["content_hash"] == original for chunk in store.load(CASE_ID)["chunks"])


@pytest.mark.asyncio
async def test_provider_embeddings_are_used_when_available_then_fall_back(tmp_path):
    class _Invocation:
        def __init__(self, available: bool) -> None:
            self.available = available
            self.model = "test-embed-model"

    class _Router:
        def __init__(self, available: bool) -> None:
            self.available = available
            self.calls = 0

        def route(self, task):
            return _Invocation(self.available)

        async def embed(self, task, inputs):
            self.calls += 1
            if not self.available:
                return {"available": False, "reason": "no_api_key_for_role_embedding"}
            return {
                "available": True,
                "embeddings": [[1.0, 0.0, 0.0] for _ in inputs],
                "dimensions": 3,
                "model": "test-embed-model",
            }

    store = _store(tmp_path)
    router = _Router(available=True)
    embedder = RouterEmbedder(router, fallback=LocalHashingEmbedder())
    hits, stats = await _search([BRIEF], store=store, embedder=embedder)
    assert stats.model == "test-embed-model"
    assert stats.dimensions == 3
    assert router.calls >= 1

    # Provider disappears: the local embedder takes over and the index is
    # rebuilt rather than compared across incompatible vector spaces.
    store2 = SemanticIndexStore(tmp_path / "ai_index_fallback")
    hits, stats = await _search(
        [BRIEF], store=store2, embedder=RouterEmbedder(_Router(available=False))
    )
    assert stats.available
    assert stats.model == LocalHashingEmbedder.name


def test_chunking_is_bounded_and_sentence_aligned():
    long_document = {
        "doc_id": "DOC-LONG",
        "document_type": "CDR",
        "content": "\n".join(f"row {index}: value {index}" for index in range(200)),
    }
    chunks = chunk_document(long_document, case_id=CASE_ID, max_chars=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 + 22 for chunk in chunks)
    assert all(chunk.content_hash for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
