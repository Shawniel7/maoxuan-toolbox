"""Batch crawler for marxists.org Mao selected-works pages.

Respect rules (enforced by config, not optional):
    - delay in [CRAWL_MIN_DELAY_SEC, CRAWL_MAX_DELAY_SEC] between requests
    - User-Agent identifies the project
    - exponential backoff on failure, max CRAWL_MAX_RETRIES
    - idempotent: articles with status=="downloaded" are skipped
    - save manifest after every article so a crash loses at most one in-flight fetch

CLI:
    python -m backend.ingest.crawler [--only v1-001]
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

import httpx

from backend.ingest import manifest as mf
from backend.ingest import parser as ps

CORPUS_RAW = Path(__file__).resolve().parents[2] / "corpus" / "raw"


class Crawler:
    def __init__(self) -> None:
        self.min_delay = float(os.environ.get("CRAWL_MIN_DELAY_SEC", "1.5"))
        self.max_delay = float(os.environ.get("CRAWL_MAX_DELAY_SEC", "3.0"))
        self.max_retries = int(os.environ.get("CRAWL_MAX_RETRIES", "3"))
        self.user_agent = os.environ.get(
            "CRAWL_USER_AGENT",
            "MaoxuanToolbox/1.0 (educational; respects robots.txt)",
        )
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )

    def run(self, only: str | None = None) -> None:
        """Crawl every article with status==pending. `only=<article_id>` limits to one."""
        raise NotImplementedError(
            "step-3: implement crawl loop. "
            "For each pending article: fetch → parser.html_to_markdown → write md → update status. "
            "Save manifest after each article. Sleep random(min_delay, max_delay)."
        )

    def fetch_one(self, article: dict) -> str:
        """Fetch a single URL with retry/backoff. Returns raw HTML string."""
        raise NotImplementedError("step-3: implement fetch_one")

    def _output_path(self, article: dict, volume: int) -> Path:
        vol_dir = CORPUS_RAW / f"vol{volume}"
        vol_dir.mkdir(parents=True, exist_ok=True)
        return vol_dir / f"{article['id']}.md"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Run just one article by id (e.g. v1-001)")
    args = ap.parse_args()
    Crawler().run(only=args.only)


if __name__ == "__main__":
    main()
