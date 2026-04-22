"""Retrieval layer.

Layers (composable, each in a separate entry point so eval harness can A/B them):
    retrieve_dense(query, top_k)                 — faiss only
    retrieve_bm25(query, top_k)                  — rank_bm25 + jieba only
    retrieve_hybrid(query, top_k)                — vec + bm25 fused via RRF
    retrieve_hybrid_diverse(query, top_k)        — + article-diversity cap
    retrieve_with_rewrite(query, top_k)          — + LLM query rewriting (needs API key)
    retrieve_with_rerank(query, top_k)           — + LLM rerank pass (needs API key)

All loaders are module-level lazy singletons; reused across calls in the same process.
"""
from __future__ import annotations

import json
import os
import pickle
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

CORPUS = Path(__file__).resolve().parent.parent / "corpus"
INDEX_FAISS = CORPUS / "index.faiss"
CHUNK_ID_MAP = CORPUS / "chunk_id_map.json"
CHUNKS_JSONL = CORPUS / "chunks.jsonl"
BM25_INDEX = CORPUS / "bm25_index.pkl"

RRF_K = 60  # Reciprocal Rank Fusion constant
RECALL_PER_LIST = 30  # how many candidates each retriever contributes before fusion


@dataclass
class Chunk:
    chunk_id: str
    text: str
    article_id: str
    article_title: str
    volume: int
    year: int
    month: int | None
    stable_slug: str
    section_title: str | None
    section_title_is_descriptive: bool
    is_footnote: bool
    footnote_ref: str | None
    char_offset_start: int
    char_offset_end: int
    source_url: str
    upstream_provider: str
    score: float = 0.0

    @classmethod
    def from_record(cls, rec: dict, score: float = 0.0) -> "Chunk":
        # char_count field is in records but not in dataclass — drop it
        rec = {k: v for k, v in rec.items() if k != "char_count" and k != "score"}
        return cls(**rec, score=score)


# ───────────────────────── singleton loaders ─────────────────────────

@lru_cache(maxsize=1)
def _load_chunks_by_id() -> dict[str, dict]:
    out = {}
    with open(CHUNKS_JSONL, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            out[c["chunk_id"]] = c
    return out


@lru_cache(maxsize=1)
def _load_faiss():
    import faiss
    index = faiss.read_index(str(INDEX_FAISS))
    ids_in_order = json.load(open(CHUNK_ID_MAP, encoding="utf-8"))
    return index, ids_in_order


@lru_cache(maxsize=1)
def _load_bm25():
    with open(BM25_INDEX, "rb") as f:
        data = pickle.load(f)
    # Re-seed jieba user dict in this process
    import jieba
    for term in data.get("user_dict", []):
        jieba.add_word(term, freq=10000)
    return data["bm25"], data["chunk_ids"]


@lru_cache(maxsize=1)
def _load_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-small-zh-v1.5")


# ───────────────────────── tokenization helper ─────────────────────────

_MD_NOISE_RE = re.compile(r"\\(\[|\]|\*)")


def _tokenize_query(query: str) -> list[str]:
    import jieba
    # Same normalization as build-side so query-time tokens match
    q = _MD_NOISE_RE.sub(r"\1", query)
    return [t for t in jieba.lcut(q) if t.strip()]


# ───────────────────────── individual retrievers ─────────────────────────

def retrieve_dense(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """Vector search via faiss. filters applied post-search (cheap, cap is small)."""
    import numpy as np
    model = _load_embedder()
    index, ids_in_order = _load_faiss()
    chunks_by_id = _load_chunks_by_id()

    vec = model.encode([query], normalize_embeddings=True, convert_to_numpy=True).astype("float32")
    # Over-fetch so filters don't starve top_k
    fetch = max(top_k * 3, RECALL_PER_LIST)
    D, I = index.search(vec, fetch)

    results: list[Chunk] = []
    for score, row in zip(D[0], I[0]):
        if row < 0:
            continue
        rec = chunks_by_id[ids_in_order[row]]
        if _passes_filters(rec, filters):
            results.append(Chunk.from_record(rec, score=float(score)))
        if len(results) >= top_k:
            break
    return results


def retrieve_bm25(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """BM25 search."""
    import numpy as np
    bm25, chunk_ids = _load_bm25()
    chunks_by_id = _load_chunks_by_id()

    tokens = _tokenize_query(query)
    if not tokens:
        return []
    scores = bm25.get_scores(tokens)
    order = np.argsort(scores)[::-1]

    results: list[Chunk] = []
    for row in order:
        if scores[row] <= 0:
            break
        rec = chunks_by_id[chunk_ids[row]]
        if _passes_filters(rec, filters):
            results.append(Chunk.from_record(rec, score=float(scores[row])))
        if len(results) >= top_k:
            break
    return results


def retrieve_hybrid(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    queries: Optional[Sequence[str]] = None,
) -> list[Chunk]:
    """Fuse dense + BM25 via Reciprocal Rank Fusion.

    If `queries` is provided (e.g. from query rewriting), each query contributes one
    dense result list + one BM25 result list to the fusion.
    """
    query_list = list(queries) if queries else [query]
    all_result_lists: list[list[Chunk]] = []
    for q in query_list:
        all_result_lists.append(retrieve_dense(q, top_k=RECALL_PER_LIST, filters=filters))
        all_result_lists.append(retrieve_bm25(q, top_k=RECALL_PER_LIST, filters=filters))

    # RRF: score per chunk_id = sum(1 / (k + rank)) across all lists (1-indexed rank)
    rrf_scores: dict[str, float] = {}
    best_chunk: dict[str, Chunk] = {}
    for lst in all_result_lists:
        for rank, ch in enumerate(lst, start=1):
            rrf_scores[ch.chunk_id] = rrf_scores.get(ch.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            # Keep the first-seen Chunk dataclass for metadata (score will be overwritten)
            if ch.chunk_id not in best_chunk:
                best_chunk[ch.chunk_id] = ch

    # Sort by fused score desc
    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])
    out: list[Chunk] = []
    for cid in sorted_ids[: top_k * 4]:  # keep a wider pool for downstream diversity/rerank
        ch = best_chunk[cid]
        ch.score = rrf_scores[cid]
        out.append(ch)
    return out[:top_k]


def retrieve_hybrid_diverse(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    queries: Optional[Sequence[str]] = None,
    per_article_cap: int = 2,
) -> list[Chunk]:
    """Hybrid fusion then walk the sorted list, capping per-article_id membership.

    Keeps the top-ranked chunk per article plus up to (cap-1) more. Surfaces additional
    articles once the first has filled its cap.
    """
    # Get a wider pool from hybrid to survive the diversity cap
    pool_size = max(top_k * 3, 20)
    wide = retrieve_hybrid(query, top_k=pool_size, filters=filters, queries=queries)

    article_counts: dict[str, int] = {}
    selected: list[Chunk] = []
    for ch in wide:
        if article_counts.get(ch.article_id, 0) >= per_article_cap:
            continue
        selected.append(ch)
        article_counts[ch.article_id] = article_counts.get(ch.article_id, 0) + 1
        if len(selected) >= top_k:
            break
    return selected


# ───────────────────────── legacy signatures ─────────────────────────

def retrieve(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """Legacy name. Defaults to dense."""
    return retrieve_dense(query, top_k, filters)


def retrieve_with_rerank(query: str, top_k: int = 8) -> list[Chunk]:
    """LLM rerank on top of hybrid+diverse. Implemented in Layer-4 step."""
    raise NotImplementedError("Layer 4 (LLM rerank) — pending eval checkpoint")


# ───────────────────────── filter helper ─────────────────────────

def _passes_filters(rec: dict, filters: Optional[dict]) -> bool:
    if not filters:
        return True
    if "volume" in filters and rec.get("volume") not in filters["volume"]:
        return False
    if "year_range" in filters:
        lo, hi = filters["year_range"]
        y = rec.get("year", 0)
        if not (lo <= y <= hi):
            return False
    if "category" in filters and rec.get("category") != filters["category"]:
        return False
    if "exclude_articles" in filters and rec.get("article_id") in filters["exclude_articles"]:
        return False
    return True
