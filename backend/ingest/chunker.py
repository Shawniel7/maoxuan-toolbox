"""Slice cleaned markdown into overlapping chunks.

Strategy:
    - Primary split on `## ` headings (section boundaries)
    - Secondary split on blank lines (paragraph)
    - Target chunk: 300–600 chars (Chinese)
    - Max chunk: 900 chars (split on sentence end 。！？ when exceeded)
    - 80-char overlap between adjacent chunks within a section
    - Preserve: article_id, article_title, volume, section, char_offset_start/end, source_url

Output: corpus/chunks.jsonl (one JSON object per line).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

CORPUS_RAW = Path(__file__).resolve().parents[2] / "corpus" / "raw"
CHUNKS_JSONL = Path(__file__).resolve().parents[2] / "corpus" / "chunks.jsonl"

MIN_CHARS = 300
TARGET_CHARS = 450
MAX_CHARS = 900
OVERLAP_CHARS = 80


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str
    article_id: str
    article_title: str
    volume: int
    section: str
    char_count: int
    char_offset_start: int
    char_offset_end: int
    source_url: str


def chunk_all() -> int:
    """Walk corpus/raw/, chunk every md, write chunks.jsonl. Returns chunk count."""
    raise NotImplementedError("step-4: implement chunk_all")


def chunk_article(markdown: str, metadata: dict) -> list[ChunkRecord]:
    """Chunk a single article. Pure function — no IO."""
    raise NotImplementedError("step-4: implement chunk_article")
