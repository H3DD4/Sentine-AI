"""
Embedding utilities — dense (BGE) + sparse (BM25).

Three correctness fixes over the previous version, each of which was silently
degrading retrieval:

1.  CHUNK_SIZE was 400 characters, on the assumption that BGE's 512-token
    window meant "~4 chars per token".  That is backwards: 512 *tokens* is
    roughly 2000 characters, so every CVE description was being split into
    ~5 fragments that each held a sentence or two of context.  Chunk size is
    now derived from the model's real token limit and measured with the
    model's own tokenizer.

2.  `embed_text_mean` averaged those fragment vectors into a single centroid.
    Averaging embeddings of unrelated sentences produces a vector that points
    at none of them — the classic "mean of the corners is the middle of the
    room" failure.  Long documents are now stored as one point per chunk and
    scored by their best-matching chunk, which is what actually answers a query.

3.  BGE is an *asymmetric* model: it was trained with an instruction prefix on
    the query side only, which the old code never applied.  `embed_query` now
    applies it and `embed_document` deliberately does not.  Note that the
    prefix lowers absolute cosine scores without necessarily widening the gap
    between relevant and irrelevant documents — so it is NOT a free recall win,
    and any fixed score threshold must be recalibrated against it.  This is one
    reason retrieval fuses by rank (RRF) rather than by raw score.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Iterable, Optional

from app.config import settings

log = logging.getLogger(__name__)

# ── Dense model ──────────────────────────────────────────────────────────────

_model = None
_model_lock = threading.Lock()

#: BGE was trained with this exact instruction on the query side. It moves
#: queries into the region of embedding space the model learned to match
#: passages from. Documents must NOT receive it. Applying it shifts absolute
#: similarity scores downward, so score thresholds tuned without it will be
#: wrong — prefer rank-based fusion over raw-score cutoffs.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

#: Model's real token window.
MAX_TOKENS = 512
#: Leave room for the prefix and special tokens rather than truncating mid-word.
CHUNK_TOKENS = 480
#: Overlap so a fact spanning a boundary survives in at least one whole chunk.
CHUNK_OVERLAP_TOKENS = 64

EMBED_DIM = 768


def _uses_bge_prefix() -> bool:
    """Only BGE-family models want the query instruction."""
    return "bge" in (settings.EMBEDDING_MODEL or "").lower()


def load_model_sync() -> None:
    """
    Pre-load the dense model. Call at application startup.

    Offline-only unless `ALLOW_MODEL_DOWNLOADS` is set. Unlike the reranker,
    this model is not optional — dense retrieval cannot run without it. But an
    uncached model must still fail *fast*: raising here surfaces one clear
    error the operator can act on, where a blocking download would look like a
    hung application and take the sparse/BM25 arm down with it.
    """
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            offline = not getattr(settings, "ALLOW_MODEL_DOWNLOADS", False)
            log.info(
                "Loading embedding model: %s (offline=%s) …",
                settings.EMBEDDING_MODEL,
                offline,
            )
            try:
                _model = SentenceTransformer(
                    settings.EMBEDDING_MODEL,
                    model_kwargs={"local_files_only": True} if offline else None,
                )
            except Exception as exc:
                if offline:
                    raise RuntimeError(
                        f"Embedding model '{settings.EMBEDDING_MODEL}' is not in the "
                        "local cache and downloads are disabled. Run "
                        "`python -m scripts.warm_models` on a good connection, or set "
                        f"ALLOW_MODEL_DOWNLOADS=true. Underlying error: {exc}"
                    ) from exc
                raise
            log.info(
                "Embedding model ready (dim=%d, max_seq=%s).",
                _model.get_sentence_embedding_dimension(),
                _model.max_seq_length,
            )


def get_model():
    if _model is None:
        load_model_sync()
    return _model


def embedding_dim() -> int:
    try:
        return int(get_model().get_sentence_embedding_dimension())
    except Exception:
        return EMBED_DIM


# ── Token-aware chunking ─────────────────────────────────────────────────────


def chunk_text(text: str) -> list[str]:
    """
    Split on the model's own tokenizer so chunks are guaranteed to fit its
    window — character heuristics get this wrong for the token-dense strings
    common in this corpus (CPE identifiers, stack traces, base64 blobs).
    """
    text = (text or "").strip()
    if not text:
        return []

    model = get_model()
    tokenizer = model.tokenizer
    ids = tokenizer.encode(text, add_special_tokens=False)

    if len(ids) <= CHUNK_TOKENS:
        return [text]

    step = CHUNK_TOKENS - CHUNK_OVERLAP_TOKENS
    chunks: list[str] = []
    for start in range(0, len(ids), step):
        window = ids[start : start + CHUNK_TOKENS]
        if not window:
            break
        chunk = tokenizer.decode(window, skip_special_tokens=True).strip()
        if chunk:
            chunks.append(chunk)
        if start + CHUNK_TOKENS >= len(ids):
            break
    return chunks or [text]


# ── Dense embedding ──────────────────────────────────────────────────────────


def embed_document(text: str) -> list[float]:
    """Embed a passage. No query prefix — this is the document side."""
    model = get_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Batched document embedding — far faster than one call per text."""
    if not texts:
        return []
    model = get_model()
    vecs = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=batch_size,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """
    Embed a search query, applying the BGE instruction prefix.

    Every retrieval path must go through this rather than `embed_document`,
    otherwise queries and passages sit in different regions of the space.
    """
    model = get_model()
    payload = BGE_QUERY_PREFIX + text if _uses_bge_prefix() else text
    return model.encode(payload, normalize_embeddings=True).tolist()


def embed_chunks(text: str) -> list[tuple[int, str, list[float]]]:
    """
    Chunk a document and embed each chunk separately.

    Returns (chunk_index, chunk_text, vector) tuples. The caller stores one
    Qdrant point per chunk, so a query matches the specific passage that
    answers it instead of a blurred average of the whole document.
    """
    chunks = chunk_text(text)
    if not chunks:
        return []
    vectors = embed_documents(chunks)
    return [(i, c, v) for i, (c, v) in enumerate(zip(chunks, vectors))]


# ── Sparse (BM25) embedding ──────────────────────────────────────────────────
# BM25 covers exactly what dense embeddings are worst at: rare literal tokens.
# Version strings, CPE fragments, CWE numbers and product names are precisely
# the terms a red-team query hinges on, and a 768-dim dense vector smooths them
# away. Running both arms and fusing is why hybrid beats either alone.

_sparse_model = None
_sparse_lock = threading.Lock()
SPARSE_MODEL_NAME = "Qdrant/bm25"
#: Named vector key used in Qdrant for the sparse arm.
SPARSE_VECTOR_NAME = "sparse"
DENSE_VECTOR_NAME = "dense"


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]

    def is_empty(self) -> bool:
        return not self.indices


def load_sparse_model_sync() -> None:
    """
    Load the BM25 sparse encoder.

    Offline-only unless `ALLOW_MODEL_DOWNLOADS` is set, for the same reason as
    the dense model: callers already handle this raising (retrieval drops to
    dense-only and says so), but nothing handles it *blocking*.
    """
    global _sparse_model
    with _sparse_lock:
        if _sparse_model is None:
            from fastembed import SparseTextEmbedding

            offline = not getattr(settings, "ALLOW_MODEL_DOWNLOADS", False)
            log.info(
                "Loading sparse model: %s (offline=%s) …", SPARSE_MODEL_NAME, offline
            )
            if offline:
                # fastembed has no local_files_only flag; HF_HUB_OFFLINE is the
                # supported way to force its downloader to use cache only.
                prev = os.environ.get("HF_HUB_OFFLINE")
                os.environ["HF_HUB_OFFLINE"] = "1"
                try:
                    _sparse_model = SparseTextEmbedding(SPARSE_MODEL_NAME)
                finally:
                    if prev is None:
                        os.environ.pop("HF_HUB_OFFLINE", None)
                    else:
                        os.environ["HF_HUB_OFFLINE"] = prev
            else:
                _sparse_model = SparseTextEmbedding(SPARSE_MODEL_NAME)
            log.info("Sparse model ready.")


def get_sparse_model():
    if _sparse_model is None:
        load_sparse_model_sync()
    return _sparse_model


def _to_sparse(emb) -> SparseVector:
    return SparseVector(
        indices=[int(i) for i in emb.indices],
        values=[float(v) for v in emb.values],
    )


def sparse_embed_documents(texts: list[str]) -> list[SparseVector]:
    if not texts:
        return []
    model = get_sparse_model()
    return [_to_sparse(e) for e in model.embed(texts)]


def sparse_embed_query(text: str) -> SparseVector:
    """
    BM25 query encoding differs from document encoding: `query_embed` omits
    term-frequency saturation, since a query term appearing twice does not make
    it twice as important. Using `embed` here would distort the scores.
    """
    model = get_sparse_model()
    return _to_sparse(next(iter(model.query_embed(text))))


# ── Identity & hashing ───────────────────────────────────────────────────────


def deterministic_qdrant_id(uid: str, chunk_index: int = 0) -> int:
    """
    Stable 63-bit point ID derived from (document id, chunk index).

    Determinism is what makes re-ingest idempotent: the same document always
    lands on the same point IDs, so an upsert overwrites rather than duplicating.
    MD5 is used purely as a fast, well-distributed hash — no security role.
    """
    digest = hashlib.md5(f"{uid}::{chunk_index}".encode()).hexdigest()
    return int(digest[:16], 16) & 0x7FFF_FFFF_FFFF_FFFF


def content_hash(text: str) -> str:
    """
    Hash of the exact text that was embedded.

    Stored per row so a re-sync can skip documents whose content has not
    changed — the difference between a 10-minute incremental sync and a
    3-hour full re-embed.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def current_embed_signature() -> str:
    """
    Identifies the embedding configuration a vector was produced with.

    When the model or chunking changes, this string changes, and rows whose
    stored signature no longer matches are known to need re-embedding. Without
    it, a model swap leaves a silently incoherent index.
    """
    return f"{settings.EMBEDDING_MODEL}|{CHUNK_TOKENS}|{CHUNK_OVERLAP_TOKENS}"


# ── Backwards compatibility ──────────────────────────────────────────────────
# Older call sites used these names. They remain importable so nothing breaks
# mid-migration, but `embed_text_mean` no longer mean-pools across chunks —
# it embeds the first window, which is strictly better than a centroid.


def embed_text(text: str) -> list[float]:
    return embed_document(text)


def embed_text_mean(text: str) -> list[float]:
    chunks = chunk_text(text)
    return embed_document(chunks[0]) if chunks else [0.0] * embedding_dim()


def build_kb_text(entry: dict) -> str:
    """Legacy field concatenation. New code uses `KBSource.build_text`."""
    parts = [
        entry.get("id", "") or entry.get("cve_id", ""),
        entry.get("title", ""),
        entry.get("description", ""),
        " ".join(entry.get("affected_products", []) or []),
        " ".join(entry.get("indicators", []) or []),
        entry.get("cwe", "") or "",
        " ".join(entry.get("mitre_techniques", []) or []),
        " ".join((entry.get("validation_steps", []) or [])[:3]),
    ]
    return " | ".join(p for p in parts if p)
