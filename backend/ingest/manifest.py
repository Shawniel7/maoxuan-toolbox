"""Manifest load/save.

manifest/maoxuan-index.json is the single source of truth for which articles
belong to the corpus. Each article carries a `status` field used for resumable
crawling: pending | downloaded | failed | skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "manifest" / "maoxuan-index.json"


def load() -> dict[str, Any]:
    """Read manifest/maoxuan-index.json."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"Manifest not found at {MANIFEST_PATH}. "
            "The repo ships a seeded manifest; run `git pull` or restore from backup."
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save(manifest: dict[str, Any]) -> None:
    """Atomically write manifest back to disk.

    Uses write-to-tmp-then-rename so a crash mid-write cannot corrupt the file.
    """
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(MANIFEST_PATH)


def iter_articles(manifest: dict[str, Any]):
    """Yield (volume_obj, article_obj) tuples across all volumes."""
    for vol in manifest["volumes"]:
        for art in vol["articles"]:
            yield vol, art


def update_status(manifest: dict[str, Any], article_id: str, status: str, **extra) -> None:
    """Set status (and optional extra fields like `error`) on a given article_id."""
    for _, art in iter_articles(manifest):
        if art["id"] == article_id:
            art["status"] = status
            for k, v in extra.items():
                art[k] = v
            return
    raise KeyError(f"article_id not found in manifest: {article_id}")
