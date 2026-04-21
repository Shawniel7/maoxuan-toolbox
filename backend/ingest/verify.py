"""Human-in-the-loop spot check after a crawl.

Reads N random articles from corpus/raw/, prints title + first 500 chars,
flags any that look suspicious (too short, HTML residue, encoding mojibake).

CLI:
    python -m backend.ingest.verify [--sample 10]
"""
from __future__ import annotations

from pathlib import Path

CORPUS_RAW = Path(__file__).resolve().parents[2] / "corpus" / "raw"


def sample(n: int = 10) -> None:
    """Print a random sample of N articles for manual inspection."""
    raise NotImplementedError("step-3: implement sample")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10)
    args = ap.parse_args()
    sample(args.sample)


if __name__ == "__main__":
    main()
