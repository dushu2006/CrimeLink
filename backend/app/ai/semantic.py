"""Hybrid semantic retrieval — vectors supplement deterministic retrieval.

CrimeLink's retrieval is deterministic first: exact terms, structured records
and the graph.  That remains the backbone.  This module adds the one thing
those layers cannot do — find a record that is *about* the question without
containing its words, e.g. a brief describing "irregularities in procurement"
for a question about "tender manipulation".

    SOURCE DOCUMENT → NORMALISATION → PSEUDONYMISATION → CHUNKING → EMBEDDING
                    → LOCAL VECTOR INDEX
    QUESTION → LEXICAL ∪ SEMANTIC ∪ GRAPH → MERGE → DEDUPE → RERANK →
    CASE BOUNDARY → EVIDENCE BOUNDARY

Design constraints, all deliberate:

* **The embedded profile must keep working.**  There is no vector service and
  no new infrastructure: the index is a JSON file next to the graph snapshot,
  and when no embedding provider is configured the module falls back to a
  deterministic local embedder rather than failing or inventing vectors.
* **The index is not an authorization layer.**  Every search intersects its
  hits with the case-scoped, non-retired document set that retrieval already
  authorised; a vector hit for another case or a retired document is dropped
  before it can reach the evidence boundary.
* **Strict privacy mode indexes pseudonyms.**  The text that gets embedded is
  whatever the caller hands over, and the gateway hands over pseudonymized
  text, so a real identity is never embedded, stored, or sent to a provider.
* **The index is version-aware.**  Chunks are addressed by content hash, so a
  changed document is re-embedded and a deleted one stops being retrievable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

from app.logging import get_logger

log = get_logger("crimelink.ai.semantic")

INDEX_SCHEMA_VERSION = 1
DEFAULT_CHUNK_CHARS = 700
DEFAULT_CHUNK_OVERLAP = 120
LOCAL_EMBEDDER_NAME = "local-hash-v1"
LOCAL_EMBEDDER_DIMS = 256

#: Domain thesaurus used by the *local* embedder.  Expanding a token into the
#: vocabulary of the corpus is a long-standing information-retrieval technique
#: and is what lets an offline CrimeLink instance match "tender manipulation"
#: against "procurement irregularities" without any external service.
_CONCEPT_GROUPS: list[tuple[str, ...]] = [
    ("tender", "tenders", "procurement", "bid", "bidding", "contract", "contracts", "award"),
    ("manipulation", "manipulated", "irregularities", "irregularity", "fraud", "fraudulent",
     "corruption", "corrupt", "bribery", "bribe", "kickback", "collusion", "rigging"),
    ("payment", "payments", "paid", "transfer", "transfers", "transaction", "transactions",
     "remittance", "deposit", "deposits", "cash", "funds", "money", "amount", "ledger"),
    ("phone", "phones", "mobile", "call", "calls", "cdr", "telecom", "sms", "message",
     "messages", "communication", "communications", "contact", "contacted"),
    ("vehicle", "vehicles", "car", "cars", "registration", "plate", "anpr", "sighting",
     "sightings", "camera", "cameras", "cctv"),
    ("location", "locations", "address", "premises", "place", "site", "scene", "warehouse",
     "office", "residence", "movement", "travel"),
    ("accused", "accusation", "suspect", "suspects", "defendant", "charged", "chargesheet"),
    ("witness", "witnesses", "statement", "statements", "testimony", "deposition", "161"),
    ("victim", "complainant", "informant", "source"),
    ("incident", "offence", "offense", "crime", "occurrence", "event", "happening"),
    ("arrest", "arrested", "custody", "detention", "raid", "raided", "seizure", "seized"),
    ("forensic", "forensics", "laboratory", "exhibit", "exhibits", "analysis", "scientific"),
    ("account", "accounts", "bank", "banking", "balance", "withdrawal", "cheque"),
    ("timeline", "chronology", "chronological", "sequence", "order"),
    ("relationship", "relationships", "connection", "connections", "link", "links",
     "association", "network", "path"),
    ("corroborate", "corroborated", "corroboration", "supported", "support", "confirmed",
     "consistent", "multiple", "independent"),
    ("contradiction", "contradictions", "conflict", "conflicting", "inconsistency",
     "inconsistent", "discrepancy", "discrepancies", "disagree"),
    ("before", "prior", "preceding", "earlier", "leading", "preliminary"),
    ("after", "following", "subsequent", "later", "subsequently"),
    ("intelligence", "brief", "lead", "leads", "assessment", "reliability"),
    ("diary", "investigation", "enquiry", "inquiry", "progress", "day"),
]

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_CONCEPT_INDEX: dict[str, int] = {}
for _group_index, _group in enumerate(_CONCEPT_GROUPS):
    for _term in _group:
        _CONCEPT_INDEX.setdefault(_term, _group_index)


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Chunk:
    """An embeddable slice of one case record."""

    chunk_id: str
    case_id: str
    document_id: str
    sequence: int
    text: str
    content_hash: str
    evidence_type: str = "DOCUMENT"
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "case_id": self.case_id,
            "document_id": self.document_id,
            "sequence": self.sequence,
            "text": self.text,
            "content_hash": self.content_hash,
            "evidence_type": self.evidence_type,
            "source_metadata": self.source_metadata,
        }


def _clean_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", str(text or "")).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def chunk_document(
    document: dict[str, Any],
    *,
    case_id: str,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split one record into overlapping, sentence-aligned chunks."""
    document_id = str(document.get("doc_id") or document.get("document_id") or "")
    if not document_id:
        return []
    text = _clean_text(document.get("content") or "")
    if not text:
        return []
    evidence_type = str(document.get("document_type") or document.get("evidence_type") or "DOCUMENT")
    evidence_type = evidence_type.rsplit(".", 1)[-1].upper()
    metadata = {
        "filename": document.get("filename"),
        "recorded_at": document.get("recorded_at") or document.get("date"),
    }

    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        while len(line) > max_chars:
            pieces.append(line[:max_chars])
            line = line[max_chars - overlap:]
        pieces.append(line)

    chunks: list[Chunk] = []
    buffer = ""
    for piece in pieces:
        candidate = f"{buffer}\n{piece}".strip() if buffer else piece
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            tail = buffer[-overlap:] if overlap else ""
            buffer = f"{tail}\n{piece}".strip() if tail else piece
        else:
            buffer = piece
    if buffer:
        chunks.append(buffer)

    out: list[Chunk] = []
    for sequence, chunk_text in enumerate(chunks):
        digest = content_hash(chunk_text)
        out.append(
            Chunk(
                chunk_id=f"{document_id}:{sequence}:{digest[:10]}",
                case_id=case_id,
                document_id=document_id,
                sequence=sequence,
                text=chunk_text,
                content_hash=digest,
                evidence_type=evidence_type,
                source_metadata=metadata,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Embedders
# --------------------------------------------------------------------------- #

@dataclass
class EmbeddingResult:
    vectors: list[list[float]] | None
    model: str
    dimensions: int
    available: bool = True
    reason: str | None = None
    used_provider: bool = False


def _hash_token(token: str, dimensions: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


class LocalHashingEmbedder:
    """Deterministic, dependency-free embeddings with domain expansion.

    This is a real vector-space model — tokens and their domain synonyms hash
    into a fixed number of dimensions with sublinear term weighting — not a
    stand-in for a provider.  It is what the embedded profile uses so semantic
    retrieval works with no key, no network and no service.
    """

    name = LOCAL_EMBEDDER_NAME
    dimensions = LOCAL_EMBEDDER_DIMS
    is_provider = False

    def expand(self, text: str) -> list[str]:
        tokens = _TOKEN_RE.findall(str(text or "").lower())
        expanded: list[str] = list(tokens)
        for token in tokens:
            group = _CONCEPT_INDEX.get(token)
            if group is not None:
                expanded.extend(_CONCEPT_GROUPS[group])
        return expanded

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts or ():
            vector = [0.0] * self.dimensions
            counts: dict[str, int] = {}
            for token in self.expand(text):
                counts[token] = counts.get(token, 0) + 1
            for token, count in counts.items():
                weight = 1.0 + math.log(count)
                index = _hash_token(token, self.dimensions)
                vector[index] += weight
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class RouterEmbedder:
    """Embeddings through ``AIModelRouter.embed`` with a local fallback.

    When no embedding provider is configured the local embedder is used and the
    result says so.  The router never fabricates vectors, and neither does this
    wrapper — it just records which of the two genuinely produced them.
    """

    is_provider = True

    def __init__(
        self,
        router: Any,
        *,
        task: str = "retrieval",
        fallback: LocalHashingEmbedder | None = None,
        enabled: bool = True,
    ) -> None:
        self.router = router
        self.task = task
        self.fallback = fallback or LocalHashingEmbedder()
        self.enabled = enabled

    @property
    def name(self) -> str:
        return f"provider:{self.task}"

    @property
    def index_identity(self) -> str:
        """The model an index built by this embedder will actually contain.

        Answering this *before* embedding is what stops a case index being
        needlessly rebuilt on every question when no provider key is present.
        """
        if self.enabled and self.router is not None:
            try:
                invocation = self.router.route(self.task)
            except Exception:
                invocation = None
            if invocation is not None and getattr(invocation, "available", False):
                model = getattr(invocation, "model", None)
                if model:
                    return str(model)
        return self.fallback.name

    @property
    def dimensions(self) -> int:
        return self.fallback.dimensions

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if self.enabled and self.router is not None:
            try:
                response = await self.router.embed(self.task, list(texts))
            except Exception as exc:  # pragma: no cover - defensive
                response = {"available": False, "reason": f"embed_raised:{type(exc).__name__}"}
            if response and response.get("available"):
                vectors = [list(map(float, vector)) for vector in response.get("embeddings") or []]
                if vectors and len(vectors) == len(texts):
                    return EmbeddingResult(
                        vectors=vectors,
                        model=str(response.get("model") or self.name),
                        dimensions=int(response.get("dimensions") or len(vectors[0])),
                        available=True,
                        used_provider=True,
                    )
        vectors = self.fallback.embed(texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.fallback.name,
            dimensions=self.fallback.dimensions,
            available=True,
            reason=None,
            used_provider=False,
        )


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #

def _pack_vector(vector: Sequence[float]) -> str:
    return base64.b64encode(array("f", [float(v) for v in vector]).tobytes()).decode("ascii")


def _unpack_vector(payload: str, dimensions: int) -> list[float]:
    raw = base64.b64decode(payload.encode("ascii"))
    values = array("f")
    values.frombytes(raw)
    if len(values) < dimensions:
        values.extend([0.0] * (dimensions - len(values)))
    return [float(v) for v in values[:dimensions]]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(length):
        dot += left[index] * right[index]
        left_norm += left[index] * left[index]
        right_norm += right[index] * right[index]
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


@dataclass
class SemanticHit:
    chunk: Chunk
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.chunk.document_id,
            "chunk_id": self.chunk.chunk_id,
            "evidence_type": self.chunk.evidence_type,
            "score": round(self.score, 4),
        }


@dataclass
class IndexStats:
    documents: int = 0
    chunks: int = 0
    embedded: int = 0
    reused: int = 0
    dropped: int = 0
    rebuilt: bool = False
    model: str = ""
    dimensions: int = 0
    available: bool = True
    reason: str | None = None
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "reused": self.reused,
            "dropped": self.dropped,
            "rebuilt": self.rebuilt,
            "model": self.model,
            "dimensions": self.dimensions,
            "available": self.available,
            "reason": self.reason,
            "elapsed_ms": self.elapsed_ms,
        }


class SemanticIndex:
    """A case's chunks and their vectors, persisted as one JSON file."""

    def __init__(self, *, case_id: str, model: str, dimensions: int) -> None:
        self.case_id = case_id
        self.model = model
        self.dimensions = dimensions
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    # -------------------------------------------------------------- mutation
    def set_chunk(self, chunk: Chunk, vector: Sequence[float]) -> None:
        self._chunks[chunk.chunk_id] = chunk
        self._vectors[chunk.chunk_id] = list(vector)

    def drop_chunk(self, chunk_id: str) -> None:
        self._chunks.pop(chunk_id, None)
        self._vectors.pop(chunk_id, None)

    def document_ids(self) -> set[str]:
        return {chunk.document_id for chunk in self._chunks.values()}

    def chunks_for(self, document_id: str) -> list[Chunk]:
        return [chunk for chunk in self._chunks.values() if chunk.document_id == document_id]

    # ----------------------------------------------------------------- lookup
    def search(
        self,
        query_vector: Sequence[float],
        *,
        allowed_document_ids: Iterable[str],
        top_k: int = 8,
        min_score: float = 0.05,
    ) -> list[SemanticHit]:
        allowed = {str(doc_id) for doc_id in allowed_document_ids or ()}
        hits: list[SemanticHit] = []
        for chunk_id, chunk in self._chunks.items():
            if allowed and chunk.document_id not in allowed:
                continue
            vector = self._vectors.get(chunk_id)
            if not vector:
                continue
            score = _cosine(query_vector, vector)
            if score < min_score:
                continue
            hits.append(SemanticHit(chunk=chunk, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return hits[:top_k]

    # ------------------------------------------------------------ persistence
    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "case_id": self.case_id,
            "model": self.model,
            "dimensions": self.dimensions,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "chunks": [
                {**chunk.as_record(), "vector": _pack_vector(self._vectors.get(chunk_id, []))}
                for chunk_id, chunk in sorted(self._chunks.items())
            ],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SemanticIndex":
        index = cls(
            case_id=str(payload.get("case_id") or ""),
            model=str(payload.get("model") or ""),
            dimensions=int(payload.get("dimensions") or 0),
        )
        for record in payload.get("chunks") or []:
            try:
                chunk = Chunk(
                    chunk_id=str(record["chunk_id"]),
                    case_id=str(record.get("case_id") or index.case_id),
                    document_id=str(record["document_id"]),
                    sequence=int(record.get("sequence") or 0),
                    text=str(record.get("text") or ""),
                    content_hash=str(record.get("content_hash") or ""),
                    evidence_type=str(record.get("evidence_type") or "DOCUMENT"),
                    source_metadata=record.get("source_metadata") or {},
                )
            except (KeyError, TypeError, ValueError):
                continue
            index.set_chunk(chunk, _unpack_vector(str(record.get("vector") or ""), index.dimensions))
        return index


class SemanticIndexStore:
    """Filesystem-backed store for per-case semantic indexes."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, case_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id or "case"))
        return self.directory / f"{safe}.json"

    def load(self, case_id: str) -> dict[str, Any] | None:
        path = self.path_for(case_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("ai.semantic_index_unreadable", case_id=case_id, error=type(exc).__name__)
            return None

    def save(self, case_id: str, payload: dict[str, Any]) -> None:
        path = self.path_for(case_id)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            log.warning("ai.semantic_index_write_failed", case_id=case_id, error=type(exc).__name__)


# --------------------------------------------------------------------------- #
# Indexing pipeline
# --------------------------------------------------------------------------- #

async def ensure_index(
    *,
    case_id: str,
    documents: Sequence[dict[str, Any]],
    store: SemanticIndexStore,
    embedder: RouterEmbedder | LocalHashingEmbedder,
    text_transform: Callable[[str], str] | None = None,
    max_chunks: int = 600,
) -> tuple[SemanticIndex, IndexStats]:
    """Bring the case's vector index up to date with its live documents.

    Documents that changed are re-embedded (content hash), documents that are
    no longer live are dropped, and an index built by a different embedder is
    rebuilt rather than compared across incompatible vector spaces.
    """
    started = time.perf_counter()
    stats = IndexStats()
    transform = text_transform or (lambda text: text)

    desired_model = (
        getattr(embedder, "index_identity", None)
        or getattr(embedder, "name", LOCAL_EMBEDDER_NAME)
    )
    desired_dimensions = int(getattr(embedder, "dimensions", LOCAL_EMBEDDER_DIMS))

    payload = store.load(case_id)
    index: SemanticIndex | None = None
    if payload:
        stored_model = str(payload.get("model") or "")
        if stored_model == desired_model and int(payload.get("dimensions") or 0) == desired_dimensions:
            index = SemanticIndex.from_payload(payload)
    if index is None:
        index = SemanticIndex(case_id=case_id, model=desired_model, dimensions=desired_dimensions)
        stats.rebuilt = True

    live_ids = {str(d.get("doc_id") or d.get("document_id")) for d in documents or ()}
    for chunk_id, chunk in list(index._chunks.items()):  # noqa: SLF001 - same module
        if chunk.document_id not in live_ids:
            index.drop_chunk(chunk_id)
            stats.dropped += 1

    wanted: list[Chunk] = []
    for document in documents or ():
        transformed = dict(document)
        if transformed.get("content"):
            transformed["content"] = transform(str(transformed["content"]))
        for chunk in chunk_document(transformed, case_id=case_id):
            if chunk.document_id not in live_ids:
                continue
            wanted.append(chunk)
            if len(wanted) >= max_chunks:
                break
        if len(wanted) >= max_chunks:
            break

    existing_by_id = {chunk_id: chunk for chunk_id, chunk in index._chunks.items()}  # noqa: SLF001
    for chunk_id in list(existing_by_id):
        if chunk_id not in {chunk.chunk_id for chunk in wanted}:
            index.drop_chunk(chunk_id)
            stats.dropped += 1

    to_embed: list[Chunk] = []
    for chunk in wanted:
        if index._vectors.get(chunk.chunk_id):  # noqa: SLF001
            stats.reused += 1
            continue
        to_embed.append(chunk)

    pruned = stats.dropped > 0
    if to_embed:
        result = await embedder.embed([chunk.text for chunk in to_embed])
        if not result.available or not result.vectors or len(result.vectors) != len(to_embed):
            stats.available = False
            stats.reason = result.reason or "embedding_unavailable"
            stats.documents = len(live_ids)
            stats.chunks = len(index._chunks)
            stats.model = index.model
            stats.dimensions = index.dimensions
            stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return index, stats
        if index.model != result.model or index.dimensions != result.dimensions:
            # The embedder changed under us: an old index in another vector
            # space must not be compared with new vectors.
            index = SemanticIndex(case_id=case_id, model=result.model, dimensions=result.dimensions)
            stats.rebuilt = True
        for chunk, vector in zip(to_embed, result.vectors):
            index.set_chunk(chunk, vector)
        stats.embedded = len(to_embed)
        stats.model = result.model
        stats.dimensions = result.dimensions
        store.save(case_id, index.to_payload())
    else:
        stats.model = index.model
        stats.dimensions = index.dimensions
        if pruned:
            # A retired document must stop being retrievable on disk too, not
            # only in this process's copy of the index.
            store.save(case_id, index.to_payload())

    stats.documents = len(live_ids)
    stats.chunks = len(index._chunks)
    stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "ai.semantic_index_ready",
        case_id=case_id,
        documents=stats.documents,
        chunks=stats.chunks,
        embedded=stats.embedded,
        reused=stats.reused,
        dropped=stats.dropped,
        model=stats.model,
        elapsed_ms=stats.elapsed_ms,
    )
    return index, stats


async def search_case_index(
    *,
    case_id: str,
    question: str,
    documents: Sequence[dict[str, Any]],
    store: SemanticIndexStore,
    embedder: RouterEmbedder | LocalHashingEmbedder,
    text_transform: Callable[[str], str] | None = None,
    authorized_document_ids: Iterable[str] | None = None,
    top_k: int = 8,
    min_score: float = 0.05,
    max_chunks: int = 600,
) -> tuple[list[SemanticHit], IndexStats]:
    """Embed the question and return semantic hits inside the authorised set."""
    index, stats = await ensure_index(
        case_id=case_id,
        documents=documents,
        store=store,
        embedder=embedder,
        text_transform=text_transform,
        max_chunks=max_chunks,
    )
    if not stats.available:
        return [], stats
    transformed_question = (text_transform or (lambda text: text))(question)
    question_result = await embedder.embed([transformed_question])
    if not question_result.available or not question_result.vectors:
        stats.available = False
        stats.reason = question_result.reason or "question_embedding_unavailable"
        return [], stats
    allowed = (
        {str(doc_id) for doc_id in authorized_document_ids}
        if authorized_document_ids is not None
        else {str(d.get("doc_id") or d.get("document_id")) for d in documents or ()}
    )
    hits = index.search(
        question_result.vectors[0],
        allowed_document_ids=allowed,
        top_k=top_k,
        min_score=min_score,
    )
    log.info(
        "ai.semantic_search",
        case_id=case_id,
        hits=len(hits),
        top_score=round(hits[0].score, 4) if hits else 0.0,
        documents_in_index=stats.documents,
        model=stats.model,
    )
    return hits, stats


# --------------------------------------------------------------------------- #
# Fusion with deterministic retrieval
# --------------------------------------------------------------------------- #

def merge_retrieval_candidates(
    lexical_documents: Sequence[dict[str, Any]],
    semantic_hits: Sequence[SemanticHit],
    *,
    all_documents: Sequence[dict[str, Any]],
    lexical_scores: dict[str, float] | None = None,
    budget_chars: int | None = None,
    max_extra: int = 4,
    semantic_weight: float = 0.6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fuse lexical and semantic candidates, then re-rank the union.

    Lexical results keep their place; a document that only semantic retrieval
    found is added *behind* the deterministic evidence, bounded by the same
    character budget, and every document records which retrieval paths found
    it so an investigator (and the Evidence Drawer) can see why it is present.
    """
    lexical_scores = lexical_scores or {}
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for document in lexical_documents or ():
        doc_id = str(document.get("doc_id") or document.get("document_id") or "")
        if not doc_id or doc_id in by_id:
            continue
        entry = dict(document)
        entry["retrieval_sources"] = ["lexical"]
        by_id[doc_id] = entry
        order.append(doc_id)

    all_by_id = {
        str(document.get("doc_id") or document.get("document_id")): document
        for document in all_documents or ()
    }
    semantic_scores: dict[str, float] = {}
    semantic_only: list[str] = []
    for hit in semantic_hits or ():
        doc_id = hit.chunk.document_id
        if doc_id not in all_by_id:
            # Not part of the authorised case document set: never merged.
            continue
        semantic_scores[doc_id] = max(semantic_scores.get(doc_id, 0.0), hit.score)
        if doc_id not in by_id:
            entry = dict(all_by_id[doc_id])
            entry["retrieval_sources"] = ["semantic"]
            entry["semantic_score"] = round(hit.score, 4)
            by_id[doc_id] = entry
            semantic_only.append(doc_id)
            order.append(doc_id)

    for doc_id, entry in by_id.items():
        sources = set(entry.get("retrieval_sources") or [])
        if doc_id in semantic_scores:
            sources.add("semantic")
            entry["semantic_score"] = round(semantic_scores[doc_id], 4)
        entry["retrieval_sources"] = sorted(sources)

    lexical_ids = [doc_id for doc_id in order if doc_id not in set(semantic_only)]
    total_chars = sum(len(str(by_id[doc_id].get("content") or "")) for doc_id in lexical_ids)
    if budget_chars:
        added = 0
        kept_semantic: list[str] = []
        for doc_id in sorted(semantic_only, key=lambda d: -semantic_scores.get(d, 0.0)):
            if added >= max_extra:
                break
            content_len = len(str(by_id[doc_id].get("content") or ""))
            if total_chars + content_len > budget_chars:
                continue
            total_chars += content_len
            added += 1
            kept_semantic.append(doc_id)
        for doc_id in semantic_only:
            if doc_id not in kept_semantic:
                by_id.pop(doc_id, None)
        order = lexical_ids + kept_semantic
    else:
        order = [doc_id for doc_id in order if doc_id in by_id]

    if lexical_scores or semantic_scores:
        lexical_top = max(lexical_scores.values()) if lexical_scores else 1.0

        def _combined(doc_id: str) -> float:
            lexical = lexical_scores.get(doc_id, 0.0)
            lexical_norm = (lexical / lexical_top) if lexical_top else 0.0
            semantic = semantic_scores.get(doc_id, 0.0)
            if doc_id not in semantic_scores:
                # Deterministic retrieval found it even though the vector space
                # did not; that evidence stays first-class rather than being
                # penalised for a semantic miss.
                return lexical_norm
            return (1 - semantic_weight) * lexical_norm + semantic_weight * semantic

        rank = {doc_id: index for index, doc_id in enumerate(order)}
        order.sort(key=lambda doc_id: (-_combined(doc_id), rank[doc_id]))

    merged = [by_id[doc_id] for doc_id in order if doc_id in by_id]
    diagnostics = {
        "lexical_candidates": len(lexical_documents or ()),
        "semantic_candidates": len(semantic_scores),
        "semantic_only_merged": len([d for d in merged if d.get("retrieval_sources") == ["semantic"]]),
        "merged": len(merged),
    }
    return merged, diagnostics
