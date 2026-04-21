"""Corpus ingestion pipeline.

Pipeline stages (each is idempotent and resumable):
    1. manifest.ensure()  — load or initialize manifest/maoxuan-index.json
    2. crawler.Crawler    — fetch raw HTML → corpus/raw/volN/*.md
    3. verify.sample()    — human spot-check of cleaned output
    4. chunker.chunk_all()— slice raw markdown → corpus/chunks.jsonl
    5. embedder.embed()   — vectorize chunks → corpus/index.faiss

Entry point: backend/ingest/run.py
"""
