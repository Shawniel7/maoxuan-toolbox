"""Retrieval layer. Two-stage: vector recall → optional LLM rerank.

Public API:
    retrieve(query, top_k=8, filters=None) -> list[Chunk]
    retrieve_with_rerank(query, top_k=8) -> list[Chunk]

Dependencies (loaded lazily so imports don't break in skeleton state):
    - faiss index at corpus/index.faiss
    - chunk records at corpus/chunks.jsonl
    - embedding model via backend.ingest.embedder.get_embedder()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Chunk:
    chunk_id: str
    text: str
    article_id: str
    article_title: str
    volume: int
    section: str
    char_offset_start: int
    char_offset_end: int
    source_url: str
    score: float = 0.0


def retrieve(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
) -> list[Chunk]:
    """Vector-only retrieval.

    filters (all optional):
        volume: list[int]           — restrict to given volumes
        year_range: tuple[int, int] — inclusive year bounds
        category: str               — topic filter
        exclude_articles: list[str] — article_ids to skip
    """
    raise NotImplementedError("step-5: implement faiss search over chunks.jsonl")


def retrieve_with_rerank(query: str, top_k: int = 8) -> list[Chunk]:
    """Two-stage retrieval.

    1. retrieve(query, top_k=30) — vector recall
    2. rerank via Claude Haiku (RAG_RERANK_MODEL)
    3. return top_k by rerank score
    """
    raise NotImplementedError("step-5: implement rerank pass")
