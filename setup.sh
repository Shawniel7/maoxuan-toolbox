#!/usr/bin/env bash
# Thought Toolbox v3 — one-shot corpus build.
# Idempotent: re-run to resume from the last successful stage.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "== 思想工具箱 v3 语料库构建 =="
echo "   repo: $REPO_ROOT"
echo

# ---- 1. Python environment ----
if [ ! -d ".venv" ]; then
  echo "[1/6] creating .venv"
  python3 -m venv .venv
else
  echo "[1/6] .venv already exists, reusing"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

# ---- 2. Manifest ----
# The repo ships a seeded manifest at manifest/maoxuan-index.json.
# Step 2 of the build (URL discovery) populates url_primary fields.
if [ ! -f "manifest/maoxuan-index.json" ]; then
  echo "[2/6] ERROR: manifest/maoxuan-index.json is missing. This file is committed to the repo."
  exit 1
else
  echo "[2/6] manifest present"
fi

# ---- 3. Crawl (TODO: implemented in Step 3) ----
# python -m backend.ingest.crawler
echo "[3/6] SKIPPED — crawler not yet implemented (Step 3)"

# ---- 4. Verify (TODO) ----
# python -m backend.ingest.verify --sample 10
echo "[4/6] SKIPPED — verify not yet implemented (Step 3)"

# ---- 5. Chunk (TODO: implemented in Step 4) ----
# python -m backend.ingest.chunker
echo "[5/6] SKIPPED — chunker not yet implemented (Step 4)"

# ---- 6. Embed (TODO: implemented in Step 4) ----
# python -m backend.ingest.embedder
echo "[6/6] SKIPPED — embedder not yet implemented (Step 4)"

echo
echo "== skeleton OK. Steps 3+ light up pipeline stages one at a time. =="
