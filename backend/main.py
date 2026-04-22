"""FastAPI entry point for the Thought Toolbox backend.

Endpoints:
    POST /chat              — stream agent response (Server-Sent Events)
    GET  /articles          — list all 158 articles (metadata only)
    GET  /article/{id}      — rendered markdown of one article
    GET  /chunk/{chunk_id}  — one chunk record (for citation popovers)
    GET  /health            — status + counts

Run:
    uvicorn backend.main:app --reload --port 8000

.env is loaded automatically on startup so ANTHROPIC_API_KEY + related vars
are available without requiring the caller to export them.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Load .env at module-import time so env vars are available to rag.py / agent.py
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

from backend import agent  # noqa: E402 — must come after load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "manifest" / "maoxuan-index.json"
CORPUS_RAW = REPO_ROOT / "corpus" / "raw"
CHUNKS_JSONL = REPO_ROOT / "corpus" / "chunks.jsonl"

app = FastAPI(title="Thought Toolbox backend", version="0.5.0")

# CORS: allow localhost:8080 (v1 static site via `python3 -m http.server`)
# plus 3000/5173 (common frontend dev ports). Tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080", "http://127.0.0.1:8080",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ───────────────────────── loaders (cached) ─────────────────────────

@lru_cache(maxsize=1)
def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _article_index() -> dict[str, dict]:
    """{article_id: manifest_article_entry (with _volume added)}"""
    m = _load_manifest()
    out: dict[str, dict] = {}
    for v in m["volumes"]:
        for a in v["articles"]:
            rec = dict(a)
            rec["_volume"] = v["volume"]
            out[a["id"]] = rec
    return out


@lru_cache(maxsize=1)
def _chunk_index() -> dict[str, dict]:
    """{chunk_id: chunk_record}"""
    out: dict[str, dict] = {}
    if not CHUNKS_JSONL.exists():
        return out
    with open(CHUNKS_JSONL, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            out[c["chunk_id"]] = c
    return out


def _article_md_path(article_id: str) -> Path | None:
    a = _article_index().get(article_id)
    if not a:
        return None
    return CORPUS_RAW / f"vol{a['_volume']}" / f"{article_id}-{a['stable_slug']}.md"


# ───────────────────────── request/response models ─────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    cited_chunk_ids: Optional[list[str]] = None
    top_k: int = 8


# ───────────────────────── endpoints ─────────────────────────

@app.get("/health")
def health() -> dict:
    m = _load_manifest()
    total_articles = sum(len(v["articles"]) for v in m["volumes"])
    downloaded = sum(
        1 for v in m["volumes"] for a in v["articles"]
        if a.get("status") == "downloaded"
    )
    chunks_loaded = len(_chunk_index())
    return {
        "status": "ok",
        "stage": "v0.5 — agent + endpoints",
        "manifest_articles_total": total_articles,
        "manifest_articles_downloaded": downloaded,
        "chunks_loaded": chunks_loaded,
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.get("/articles")
def list_articles() -> list[dict]:
    """List all 158 manifest articles (metadata only, no bodies)."""
    m = _load_manifest()
    out = []
    for v in m["volumes"]:
        for a in v["articles"]:
            out.append({
                "id": a["id"],
                "stable_slug": a["stable_slug"],
                "title": a["title"],
                "year": a["year"],
                "month": a.get("month"),
                "volume": v["volume"],
                "category": a.get("category"),
                "source_url": a.get("url_primary"),
                "status": a.get("status", "pending"),
            })
    return out


@app.get("/article/{article_id}")
def get_article(article_id: str) -> dict:
    a = _article_index().get(article_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"no such article: {article_id}")
    path = _article_md_path(article_id)
    if not path or not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"article {article_id} not yet downloaded (status={a.get('status')})",
        )
    raw = path.read_text(encoding="utf-8")
    # Strip frontmatter
    body = raw
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :].lstrip("\n")
    return {
        "id": article_id,
        "title": a["title"],
        "year": a["year"],
        "month": a.get("month"),
        "volume": a["_volume"],
        "category": a.get("category"),
        "source_url": a.get("url_primary"),
        "markdown_body": body,
        "char_count": len(body),
    }


@app.get("/chunk/{chunk_id}")
def get_chunk(chunk_id: str) -> dict:
    c = _chunk_index().get(chunk_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"no such chunk: {chunk_id}")
    return {
        "chunk_id": c["chunk_id"],
        "text": c["text"],
        "article_id": c["article_id"],
        "article_title": c["article_title"],
        "section_title": c.get("section_title"),
        "section_title_is_descriptive": c.get("section_title_is_descriptive"),
        "is_footnote": c.get("is_footnote", False),
        "footnote_ref": c.get("footnote_ref"),
        "source_url": c.get("source_url"),
        "char_offset_start": c.get("char_offset_start"),
        "char_offset_end": c.get("char_offset_end"),
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """Stream an agent reply as Server-Sent Events.

    Event format (one per SSE message):
        data: {"type": "retrieved", "chunks": [...]}\n\n
        data: {"type": "text_delta", "delta": "..."}\n\n
        data: {"type": "done"}\n\n
    """
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="messages must end with a user turn")

    agent_messages = [agent.Message(role=m.role, content=m.content) for m in req.messages]

    async def event_stream():
        try:
            async for ev in agent.stream_reply(
                agent_messages,
                cited_chunk_ids=req.cited_chunk_ids,
                top_k=req.top_k,
            ):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable buffering in nginx if behind it
        },
    )
