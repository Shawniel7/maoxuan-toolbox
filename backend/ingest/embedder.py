"""Vectorize chunks → faiss index.

Default: BAAI/bge-small-zh-v1.5 via sentence-transformers (local, free, ~100MB DL).
Providers are selected via env var EMBEDDING_PROVIDER: local | voyage | openai.

Features:
    - batch processing (batch_size=64) to bound memory
    - tqdm progress bar
    - checkpointing every 500 chunks so Ctrl-C doesn't lose hours of work
    - output: corpus/index.faiss + corpus/chunk_id_map.json (row → chunk_id)

CLI:
    python -m backend.ingest.embedder
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
CHUNKS_JSONL = CORPUS / "chunks.jsonl"
INDEX_FAISS = CORPUS / "index.faiss"
CHUNK_ID_MAP = CORPUS / "chunk_id_map.json"
CHECKPOINT = CORPUS / "embed.checkpoint.json"

EMBED_BATCH = 64
CHECKPOINT_EVERY = 500


class Embedder(Protocol):
    """Shared interface across local/voyage/openai backends."""
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def get_embedder() -> Embedder:
    """Instantiate the embedder selected by EMBEDDING_PROVIDER (default: local)."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "local").lower()
    if provider == "local":
        return _LocalBGE()
    if provider == "voyage":
        raise NotImplementedError("step-4: implement Voyage provider")
    if provider == "openai":
        raise NotImplementedError("step-4: implement OpenAI provider")
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider}")


class _LocalBGE:
    """BAAI/bge-small-zh-v1.5 via sentence-transformers. dim=512."""
    dim = 512

    def __init__(self) -> None:
        # Lazy import — sentence-transformers is heavy and not needed for skeleton checks.
        raise NotImplementedError("step-4: load model, set self.model")

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("step-4: batched encode")


def embed_all() -> int:
    """Read chunks.jsonl, embed in batches, write faiss index. Returns count."""
    raise NotImplementedError("step-4: implement embed_all")
