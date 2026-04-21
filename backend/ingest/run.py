"""End-to-end ingestion runner.

Runs crawler → verify → chunker → embedder in order. Each stage is idempotent,
so re-running picks up where a previous run left off.

CLI:
    python -m backend.ingest.run [--skip-crawl] [--skip-chunk] [--skip-embed]
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Mao corpus end-to-end.")
    ap.add_argument("--skip-crawl", action="store_true")
    ap.add_argument("--skip-chunk", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    args = ap.parse_args()

    # Late imports so missing deps in one stage don't block the others.
    if not args.skip_crawl:
        from backend.ingest.crawler import Crawler
        from backend.ingest import verify
        Crawler().run()
        verify.sample(n=10)

    if not args.skip_chunk:
        from backend.ingest import chunker
        n = chunker.chunk_all()
        print(f"[chunker] wrote {n} chunks")

    if not args.skip_embed:
        from backend.ingest import embedder
        n = embedder.embed_all()
        print(f"[embedder] indexed {n} chunks")


if __name__ == "__main__":
    main()
