"""FastAPI entry point for the Thought Toolbox backend.

Serves both API and static frontend from a single uvicorn process on one port,
so the whole app can be exposed via one ngrok tunnel.

API endpoints:
    POST /chat              — stream agent response (SSE)
    GET  /articles          — list all 158 articles (metadata only)
    GET  /article/{id}      — rendered markdown of one article
    GET  /chunk/{chunk_id}  — one chunk record (for citation popovers)
    GET  /health            — status + counts

Static serving (registered AFTER the API routes so they take precedence):
    GET  /                  → index.html
    GET  /{page}.html       → allowlisted root HTML pages
    GET  /styles.css        → stylesheet
    GET  /js/**             → js/ directory
    GET  /assets/**         → assets/ directory
    GET  /data/**           → data/ directory (entries.json, schema.md)

Anything outside the allowlist (backend/, corpus/, manifest/, .env, .git, .venv)
is 404 — never served.

Run:
    uvicorn backend.main:app --reload --port 8000

.env is loaded automatically so ANTHROPIC_API_KEY propagates to rag/agent.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

from backend import agent  # noqa: E402 — must come after load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "manifest" / "maoxuan-index.json"
CORPUS_RAW = REPO_ROOT / "corpus" / "raw"
CHUNKS_JSONL = REPO_ROOT / "corpus" / "chunks.jsonl"

# Allowlist: which root-level files may be served as static HTML/CSS.
# Added here (not mounted as a StaticFiles dir) to prevent accidental exposure
# of backend/, corpus/, manifest/, .env, .git, .venv — any of which would
# leak if we naively mounted the repo root.
ROOT_STATIC_ALLOWLIST = {
    "index.html", "chat.html", "about.html", "browse.html", "styles.css",
}

app = FastAPI(title="Thought Toolbox backend", version="0.6.0")

# ───────────────────────── CORS ─────────────────────────
# Localhost dev + ngrok tunnel domains (free + paid).
# For the unified :8000 deployment, same-origin requests don't trigger CORS
# anyway; this middleware is for cases where a separate static server hits
# this backend across origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:8080", "http://127.0.0.1:8080",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"https://[a-z0-9\-]+\.(ngrok-free\.app|ngrok\.app)$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ───────────────────────── soft rate limit on /chat ─────────────────────────
# In-memory per-IP sliding-window counter. Acceptable for temporary public
# sharing via ngrok; not production-grade (single-process state, no persistence,
# no distributed lock). Tune via the constants below.
_CHAT_RATE_WINDOW_S = 600         # 10 minutes
_CHAT_RATE_MAX_REQUESTS = 10      # per window per IP
_chat_request_log: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    """Resolve client IP, honoring X-Forwarded-For set by ngrok / reverse proxies."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_chat_rate_limit(request: Request) -> None:
    ip = _client_ip(request)
    now = time.time()
    cutoff = now - _CHAT_RATE_WINDOW_S
    log = [t for t in _chat_request_log[ip] if t > cutoff]
    if len(log) >= _CHAT_RATE_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="使用较频繁,请稍后再试")
    log.append(now)
    _chat_request_log[ip] = log


# ───────────────────────── cached loaders ─────────────────────────

@lru_cache(maxsize=1)
def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _article_index() -> dict[str, dict]:
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


# ───────────────────────── request models ─────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    cited_chunk_ids: Optional[list[str]] = None
    top_k: int = 8


# ═════════════════════════ API endpoints ═════════════════════════

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
        "stage": "v0.6 — ngrok-ready unified server",
        "manifest_articles_total": total_articles,
        "manifest_articles_downloaded": downloaded,
        "chunks_loaded": chunks_loaded,
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "rate_limit": f"{_CHAT_RATE_MAX_REQUESTS} chat reqs / {_CHAT_RATE_WINDOW_S // 60} min per IP",
    }


@app.get("/articles")
def list_articles() -> list[dict]:
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
async def chat(req: ChatRequest, request: Request):
    _check_chat_rate_limit(request)

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
            "X-Accel-Buffering": "no",
        },
    )


# ═════════════════════════ static frontend ═════════════════════════
# Registered AFTER API routes so /chat, /health, /articles, /article/*, /chunk/*
# all take precedence. Subdirectory mounts are read-only and bounded to
# directories that only contain public files — corpus/, manifest/, backend/
# are NEVER mounted.

app.mount("/js", StaticFiles(directory=REPO_ROOT / "js"), name="js")
app.mount("/assets", StaticFiles(directory=REPO_ROOT / "assets"), name="assets")
app.mount("/data", StaticFiles(directory=REPO_ROOT / "data"), name="data")


@app.get("/", include_in_schema=False)
def serve_root():
    return FileResponse(REPO_ROOT / "index.html")


@app.get("/{filename}", include_in_schema=False)
def serve_root_file(filename: str):
    """Serve allowlisted root-level files (index.html, chat.html, styles.css, …)."""
    if filename not in ROOT_STATIC_ALLOWLIST:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(REPO_ROOT / filename)
