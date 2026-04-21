"""FastAPI entry point for the Thought Toolbox backend.

Exposes:
    POST /chat       — stream an Agent response for a user message
    GET  /articles   — list all ~159 articles (metadata only)
    GET  /article/{id} — fetch a single article's rendered markdown

Run:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Thought Toolbox backend", version="0.3.0-skeleton")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "stage": "skeleton"}


# TODO(step-6): POST /chat       → stream Agent responses (agent.py)
# TODO(step-5): GET  /articles   → read manifest/maoxuan-index.json
# TODO(step-5): GET  /article/{id} → read corpus/raw/volN/<id>-*.md
