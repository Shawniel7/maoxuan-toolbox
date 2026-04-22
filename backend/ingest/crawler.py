"""Batch crawler for marxists.org Mao selected-works pages.

Respect rules (enforced by config, not optional):
    - delay in [CRAWL_MIN_DELAY_SEC, CRAWL_MAX_DELAY_SEC] between requests
    - User-Agent identifies the project
    - exponential backoff on failure, max CRAWL_MAX_RETRIES
    - idempotent: articles with status=="downloaded" are skipped
    - save manifest after every article so a crash loses at most one in-flight fetch

CLI:
    python -m backend.ingest.crawler [--only v1-001]
    python -m backend.ingest.crawler --only-ids v1-016,v2-005,v2-008,v3-017,v4-059
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
SMOKE_CACHE = Path(__file__).resolve().parents[2] / "corpus" / "_smoke_cache"


class CrawlError(Exception):
    pass


class Crawler:
    def __init__(self, cache_raw: bool = False) -> None:
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
        self.cache_raw = cache_raw
        if cache_raw:
            SMOKE_CACHE.mkdir(parents=True, exist_ok=True)

    def run(self, only: str | None = None, only_ids: list[str] | None = None,
            verbose: bool = False) -> list[dict]:
        """Crawl pending articles. Returns list of per-article result dicts.

        Filters:
            only       — single article id (legacy)
            only_ids   — list of article ids (smoke test)

        verbose: if True, print per-article progress line.
        """
        manifest = mf.load()
        filter_set = set(only_ids) if only_ids else ({only} if only else None)
        results = []

        # Total to process (for progress)
        to_process = []
        for volume_obj, article in mf.iter_articles(manifest):
            if filter_set is not None and article["id"] not in filter_set:
                continue
            if article["status"] == "downloaded" and not self.cache_raw:
                continue
            to_process.append((volume_obj, article))

        total = len(to_process)
        if verbose:
            print(f"[crawl] {total} article(s) to fetch")

        for i, (volume_obj, article) in enumerate(to_process, start=1):
            res = {"id": article["id"], "title": article["title"], "url": article["url_primary"]}
            try:
                res |= self._crawl_one(article, volume_obj["volume"])
                mf.update_status(manifest, article["id"], "downloaded",
                                  raw_size=res["raw_size"], clean_size=res["clean_size"])
                res["status"] = "downloaded"
                # Ratio guard: flag if clean/raw ratio is outside [0.15, 0.50]
                if res["raw_size"] > 0:
                    ratio = res["clean_size"] / res["raw_size"]
                    res["ratio"] = ratio
                    if ratio < 0.15 or ratio > 0.50:
                        res.setdefault("warnings", []).append(
                            f"compression ratio {ratio*100:.1f}% outside [15%, 50%] — inspect")
            except Exception as e:
                mf.update_status(manifest, article["id"], "failed", error=str(e))
                res["status"] = "failed"
                res["error"] = str(e)
                print(f"[FAIL] {article['id']} {article['title']}: {e}")
            finally:
                mf.save(manifest)
                results.append(res)
                if verbose:
                    tag = "OK" if res["status"] == "downloaded" else "FAIL"
                    warn_tag = f" ⚠{len(res.get('warnings') or [])}" if res.get("warnings") else ""
                    print(f"[crawl] {i:>3}/{total} [{tag}] {article['id']} "
                          f"{article['title'][:18]:<20} "
                          f"raw={res.get('raw_size',0):>7,}B clean={res.get('clean_size',0):>6,}c{warn_tag}")
                # Sleep between non-final fetches (no point after the last)
                if res.get("status") == "downloaded" and i < total:
                    time.sleep(random.uniform(self.min_delay, self.max_delay))

        return results

    def _crawl_one(self, article: dict, volume: int) -> dict:
        url = article["url_primary"]
        if not url:
            raise CrawlError(f"no url_primary for {article['id']}")
        html = self.fetch_one(url)

        # Optional raw cache (for smoke tests / replay without refetching)
        if self.cache_raw:
            cache_path = SMOKE_CACHE / f"{article['id']}.html"
            cache_path.write_text(html, encoding="utf-8")

        trim_log: list[dict] = []
        markdown, warnings = ps.html_to_markdown(html, article, volume, trim_log=trim_log)

        out_path = self._output_path(article, volume)
        out_path.write_text(markdown, encoding="utf-8")

        return {
            "raw_size": len(html.encode("utf-8")),
            "clean_size": len(markdown),
            "output_path": str(out_path),
            "warnings": warnings,
            "trim_log": trim_log,
        }

    def fetch_one(self, url: str) -> str:
        """Fetch with retry + exponential backoff. Handles GB2312/GBK encoding."""
        last_err = None
        for attempt in range(self.max_retries):
            try:
                r = self.client.get(url)
                r.raise_for_status()
                # marxists.org Chinese pages are gb2312/gbk; httpx's apparent_encoding
                # heuristic sometimes misses. Prefer explicit decode with gb18030 (superset).
                raw_bytes = r.content
                try:
                    return raw_bytes.decode("gb18030")
                except UnicodeDecodeError:
                    # Fall back to httpx's guess
                    r.encoding = r.apparent_encoding or "gb18030"
                    return r.text
            except Exception as e:
                last_err = e
                if attempt == self.max_retries - 1:
                    break
                backoff = 2 ** attempt
                time.sleep(backoff)
        raise CrawlError(f"fetch failed after {self.max_retries} attempts: {last_err}")

    def _output_path(self, article: dict, volume: int) -> Path:
        vol_dir = CORPUS_RAW / f"vol{volume}"
        vol_dir.mkdir(parents=True, exist_ok=True)
        return vol_dir / f"{article['id']}-{article['stable_slug']}.md"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Run just one article by id (e.g. v1-001)")
    ap.add_argument("--only-ids", help="Comma-separated list of article ids")
    ap.add_argument("--cache-raw", action="store_true",
                    help="Save raw HTML to corpus/_smoke_cache/ (for smoke tests)")
    args = ap.parse_args()

    ids = args.only_ids.split(",") if args.only_ids else None
    Crawler(cache_raw=args.cache_raw).run(only=args.only, only_ids=ids)


if __name__ == "__main__":
    main()
