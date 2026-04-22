"""Retrieval layer.

Layers (composable, each in a separate entry point so eval harness can A/B them):
    retrieve_dense(query, top_k)                 — faiss only
    retrieve_bm25(query, top_k)                  — rank_bm25 + jieba only
    retrieve_hybrid(query, top_k)                — vec + bm25 fused via RRF
    retrieve_hybrid_diverse(query, top_k)        — + article-diversity cap
    retrieve_with_rewrite(query, top_k)          — + LLM query rewriting (needs API key)
    retrieve_with_rerank(query, top_k)           — + LLM rerank pass (needs API key)

All loaders are module-level lazy singletons; reused across calls in the same process.
"""
from __future__ import annotations

import json
import os
import pickle
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

CORPUS = Path(__file__).resolve().parent.parent / "corpus"
INDEX_FAISS = CORPUS / "index.faiss"
CHUNK_ID_MAP = CORPUS / "chunk_id_map.json"
CHUNKS_JSONL = CORPUS / "chunks.jsonl"
BM25_INDEX = CORPUS / "bm25_index.pkl"
REWRITE_CACHE = CORPUS / "rewrite_cache.json"  # committable — shared across users

RRF_K = 60  # Reciprocal Rank Fusion constant
RECALL_PER_LIST = 30  # how many candidates each retriever contributes before fusion

# Module-level token usage counter (accumulated across rewrite_query calls)
USAGE = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cache_hits": 0, "fallbacks": 0}


@dataclass
class Chunk:
    chunk_id: str
    text: str
    article_id: str
    article_title: str
    volume: int
    year: int
    month: int | None
    stable_slug: str
    section_title: str | None
    section_title_is_descriptive: bool
    is_footnote: bool
    footnote_ref: str | None
    char_offset_start: int
    char_offset_end: int
    source_url: str
    upstream_provider: str
    score: float = 0.0

    @classmethod
    def from_record(cls, rec: dict, score: float = 0.0) -> "Chunk":
        # char_count field is in records but not in dataclass — drop it
        rec = {k: v for k, v in rec.items() if k != "char_count" and k != "score"}
        return cls(**rec, score=score)


# ───────────────────────── singleton loaders ─────────────────────────

@lru_cache(maxsize=1)
def _load_chunks_by_id() -> dict[str, dict]:
    out = {}
    with open(CHUNKS_JSONL, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            out[c["chunk_id"]] = c
    return out


@lru_cache(maxsize=1)
def _load_faiss():
    import faiss
    index = faiss.read_index(str(INDEX_FAISS))
    ids_in_order = json.load(open(CHUNK_ID_MAP, encoding="utf-8"))
    return index, ids_in_order


@lru_cache(maxsize=1)
def _load_bm25():
    with open(BM25_INDEX, "rb") as f:
        data = pickle.load(f)
    # Re-seed jieba user dict in this process
    import jieba
    for term in data.get("user_dict", []):
        jieba.add_word(term, freq=10000)
    return data["bm25"], data["chunk_ids"]


@lru_cache(maxsize=1)
def _load_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-small-zh-v1.5")


# ───────────────────────── tokenization helper ─────────────────────────

_MD_NOISE_RE = re.compile(r"\\(\[|\]|\*)")


def _tokenize_query(query: str) -> list[str]:
    import jieba
    # Same normalization as build-side so query-time tokens match
    q = _MD_NOISE_RE.sub(r"\1", query)
    return [t for t in jieba.lcut(q) if t.strip()]


# ───────────────────────── individual retrievers ─────────────────────────

def retrieve_dense(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """Vector search via faiss. filters applied post-search (cheap, cap is small)."""
    import numpy as np
    model = _load_embedder()
    index, ids_in_order = _load_faiss()
    chunks_by_id = _load_chunks_by_id()

    vec = model.encode([query], normalize_embeddings=True, convert_to_numpy=True).astype("float32")
    # Over-fetch so filters don't starve top_k
    fetch = max(top_k * 3, RECALL_PER_LIST)
    D, I = index.search(vec, fetch)

    results: list[Chunk] = []
    for score, row in zip(D[0], I[0]):
        if row < 0:
            continue
        rec = chunks_by_id[ids_in_order[row]]
        if _passes_filters(rec, filters):
            results.append(Chunk.from_record(rec, score=float(score)))
        if len(results) >= top_k:
            break
    return results


def retrieve_bm25(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """BM25 search."""
    import numpy as np
    bm25, chunk_ids = _load_bm25()
    chunks_by_id = _load_chunks_by_id()

    tokens = _tokenize_query(query)
    if not tokens:
        return []
    scores = bm25.get_scores(tokens)
    order = np.argsort(scores)[::-1]

    results: list[Chunk] = []
    for row in order:
        if scores[row] <= 0:
            break
        rec = chunks_by_id[chunk_ids[row]]
        if _passes_filters(rec, filters):
            results.append(Chunk.from_record(rec, score=float(scores[row])))
        if len(results) >= top_k:
            break
    return results


def retrieve_hybrid(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    queries: Optional[Sequence[str]] = None,
) -> list[Chunk]:
    """Fuse dense + BM25 via Reciprocal Rank Fusion.

    If `queries` is provided (e.g. from query rewriting), each query contributes one
    dense result list + one BM25 result list to the fusion.
    """
    query_list = list(queries) if queries else [query]
    all_result_lists: list[list[Chunk]] = []
    for q in query_list:
        all_result_lists.append(retrieve_dense(q, top_k=RECALL_PER_LIST, filters=filters))
        all_result_lists.append(retrieve_bm25(q, top_k=RECALL_PER_LIST, filters=filters))

    # RRF: score per chunk_id = sum(1 / (k + rank)) across all lists (1-indexed rank)
    rrf_scores: dict[str, float] = {}
    best_chunk: dict[str, Chunk] = {}
    for lst in all_result_lists:
        for rank, ch in enumerate(lst, start=1):
            rrf_scores[ch.chunk_id] = rrf_scores.get(ch.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            # Keep the first-seen Chunk dataclass for metadata (score will be overwritten)
            if ch.chunk_id not in best_chunk:
                best_chunk[ch.chunk_id] = ch

    # Sort by fused score desc
    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])
    out: list[Chunk] = []
    for cid in sorted_ids[: top_k * 4]:  # keep a wider pool for downstream diversity/rerank
        ch = best_chunk[cid]
        ch.score = rrf_scores[cid]
        out.append(ch)
    return out[:top_k]


def retrieve_hybrid_diverse(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    queries: Optional[Sequence[str]] = None,
    per_article_cap: int = 2,
) -> list[Chunk]:
    """Hybrid fusion then walk the sorted list, capping per-article_id membership.

    Keeps the top-ranked chunk per article plus up to (cap-1) more. Surfaces additional
    articles once the first has filled its cap.
    """
    # Get a wider pool from hybrid to survive the diversity cap
    pool_size = max(top_k * 3, 20)
    wide = retrieve_hybrid(query, top_k=pool_size, filters=filters, queries=queries)

    article_counts: dict[str, int] = {}
    selected: list[Chunk] = []
    for ch in wide:
        if article_counts.get(ch.article_id, 0) >= per_article_cap:
            continue
        selected.append(ch)
        article_counts[ch.article_id] = article_counts.get(ch.article_id, 0) + 1
        if len(selected) >= top_k:
            break
    return selected


# ───────────────────────── legacy signatures ─────────────────────────

def retrieve(query: str, top_k: int = 8, filters: Optional[dict] = None) -> list[Chunk]:
    """Legacy name. Defaults to dense."""
    return retrieve_dense(query, top_k, filters)


def retrieve_with_rewrite(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    per_article_cap: int = 2,
) -> list[Chunk]:
    """Layer 2: rewrite user query with Haiku into 3-5 毛选-vocabulary reformulations,
    then run hybrid+diverse over the expanded query set."""
    queries = rewrite_query(query)
    return retrieve_hybrid_diverse(
        query, top_k=top_k, filters=filters, queries=queries, per_article_cap=per_article_cap
    )


def retrieve_with_rerank(
    query: str,
    top_k: int = 8,
    filters: Optional[dict] = None,
    per_article_cap: int = 2,
    pool_size: int = 16,
) -> list[Chunk]:
    """Layer 4: Haiku reranks top-`pool_size` candidates from hybrid+rewrite.

    Flow:
      1. Get top-16 from retrieve_with_rewrite (which does hybrid+diversity+rewrite).
      2. Send candidates + ORIGINAL query to Haiku; ask for top-K indices with reasons.
      3. Reorder candidates by Haiku's ranking; fall back to pre-rerank order on failure.
    """
    # Get a wider candidate pool. retrieve_with_rewrite calls hybrid_diverse which
    # already applies per-article cap — we preserve that, then widen top_k.
    queries = rewrite_query(query)
    candidates = retrieve_hybrid_diverse(
        query, top_k=pool_size, filters=filters, queries=queries, per_article_cap=per_article_cap
    )
    if len(candidates) <= top_k:
        return candidates

    ranked = _rerank_with_haiku(query, candidates, top_k=top_k)
    if ranked is None:
        # Fallback: pre-rerank order
        return candidates[:top_k]
    return ranked


RERANK_SYSTEM = (
    "你是检索结果重排器。给定用户的原始问题和若干候选段落(来自《毛泽东选集》),"
    "判断每个段落对回答用户问题的相关性,然后返回最相关的 top_k 个段落的索引 +"
    "一句话说明为什么相关。"
    "\n\n"
    "判断标准(按重要性递减):\n"
    "1. 段落是否包含能直接回应用户问题的方法论或分析框架\n"
    "2. 段落所属篇章是否与用户问题的主题高度相关\n"
    "3. 段落是否是该主题下最核心的原文(而非周边引述、注释、或泛泛的训示)\n"
    "\n"
    "只输出 JSON 数组,每个元素形如 {\"index\": N, \"why\": \"简短理由\"}。"
    "不要 markdown 围栏,不要其他文字。"
)


def _format_candidates_for_rerank(candidates: list[Chunk]) -> str:
    lines = []
    for i, ch in enumerate(candidates):
        # Keep each candidate compact — 300 chars of text is enough signal for Haiku
        txt = ch.text.replace("\n", " ").strip()[:300]
        tag = "[注释]" if ch.is_footnote else ""
        lines.append(
            f"[{i}] 《{ch.article_title}》 "
            f"section={ch.section_title or '(引言)'} {tag}\n"
            f"    {txt}"
        )
    return "\n\n".join(lines)


def _rerank_with_haiku(
    query: str,
    candidates: list[Chunk],
    top_k: int,
) -> list[Chunk] | None:
    """Returns reordered top_k candidates, or None on failure (caller falls back)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("ANTHROPIC_RERANK_MODEL", "claude-haiku-4-5")

    user_msg = (
        f"用户原始问题:\n{query}\n\n"
        f"候选段落(共 {len(candidates)} 个):\n\n"
        f"{_format_candidates_for_rerank(candidates)}\n\n"
        f"请返回最相关的 {top_k} 个段落索引 + 一句话理由,"
        f"JSON 数组格式,按相关性从高到低排序。"
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=800,
            system=RERANK_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        USAGE["input_tokens"] += resp.usage.input_tokens
        USAGE["output_tokens"] += resp.usage.output_tokens
        USAGE["calls"] += 1
    except Exception as e:
        import sys as _sys
        print(f"[rag.rerank] API call failed: {e}", file=_sys.stderr)
        return None

    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text += block.text

    parsed = _parse_json_array(text)
    if not parsed:
        return None

    # Each element should be a dict with "index". If elements are ints (simpler model
    # output), accept that too.
    ordered: list[Chunk] = []
    seen: set[int] = set()
    for entry in parsed:
        idx: int | None = None
        why: str = ""
        if isinstance(entry, dict) and "index" in entry:
            try:
                idx = int(entry["index"])
                why = str(entry.get("why", ""))
            except (ValueError, TypeError):
                continue
        else:
            try:
                idx = int(entry)
            except (ValueError, TypeError):
                continue
        if idx is None or idx < 0 or idx >= len(candidates):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        ch = candidates[idx]
        # Overwrite score with the rank position (1.0/rank so top=highest)
        ch.score = 1.0 / (len(ordered) + 1)
        ordered.append(ch)
        if len(ordered) >= top_k:
            break

    if not ordered:
        return None
    return ordered


# ───────────────────────── query rewriting (Layer 2) ─────────────────────────

REWRITE_SYSTEM = (
    "你是中文检索查询重写器。"
    "给定一个现代、口语化、生活化的中文问题(可能涉及工作、学习、人际、情绪、决策等),"
    "将其改写为 3-5 个使用《毛泽东选集》核心哲学方法论词汇的检索查询,"
    "目的是从毛选四卷语料中检索出最相关的段落。\n\n"
    "可用的核心概念词:矛盾论、实践论、论持久战、改造我们的学习、反对本本主义、"
    "矛盾的普遍性、矛盾的特殊性、矛盾的同一性、主要矛盾、次要矛盾、矛盾的主要方面、"
    "实事求是、没有调查没有发言权、调查研究、批评和自我批评、理论与实践、"
    "持久战、速决战、具体问题具体分析、一分为二、两点论、为人民服务、愚公移山 等。\n\n"
    "严格规则:\n"
    "1. 输出 3-5 个改写版本,最后再附上原问题不变。\n"
    "2. 每个改写版本必须突出一个具体的毛选方法论概念,不要堆砌。\n"
    "3. 改写之间应该彼此不同,覆盖不同的概念角度。\n"
    "4. 只输出 JSON 数组,形如 [\"改写1\",\"改写2\",\"改写3\",\"原问题\"],"
    "不要 markdown 围栏、不要前后解释文字、不要其他 JSON 字段。"
)

REWRITE_FEWSHOT = [
    {
        "query": "我不知道该先做什么",
        "rewrites": [
            "如何判断主要矛盾和次要矛盾",
            "多件事情中抓住主要的矛盾方面",
            "具体问题具体分析与分清先后",
            "实事求是地分析眼前任务的轻重缓急",
            "我不知道该先做什么",
        ],
    },
    {
        "query": "天天焦虑但什么都没做",
        "rewrites": [
            "实践论:认识离不开实践",
            "没有调查就没有发言权",
            "理论与实践、知与行的关系",
            "反对本本主义:从实际出发",
            "天天焦虑但什么都没做",
        ],
    },
]


def _load_rewrite_cache() -> dict:
    if not REWRITE_CACHE.exists():
        return {}
    try:
        return json.loads(REWRITE_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_rewrite_cache(cache: dict) -> None:
    REWRITE_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@lru_cache(maxsize=1)
def _get_rewrite_cache() -> dict:
    return _load_rewrite_cache()


def _parse_json_array(text: str) -> list | None:
    """Tolerant JSON-array extraction. Returns raw list (items may be str or dict).
    Callers filter by expected element type.
    """
    s = text.strip()
    # Strip markdown fence if present
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = s[start : end + 1]
    try:
        arr = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or not arr:
        return None
    return arr


def _parse_string_array(text: str) -> list[str] | None:
    """For the rewriter: extract a JSON array of strings."""
    arr = _parse_json_array(text)
    if not arr:
        return None
    out = [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]
    return out or None


def rewrite_query(query: str) -> list[str]:
    """Call Haiku to produce 3-5 毛选-vocabulary reformulations + the original query.

    Returns [query] unchanged if:
      - ANTHROPIC_API_KEY missing
      - both LLM attempts return unparseable output
      - any other exception during the call (logs warning to stderr)
    """
    cache = _get_rewrite_cache()
    if query in cache:
        USAGE["cache_hits"] += 1
        return cache[query]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        USAGE["fallbacks"] += 1
        return [query]

    try:
        import anthropic
    except ImportError:
        USAGE["fallbacks"] += 1
        return [query]

    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("ANTHROPIC_REWRITE_MODEL", "claude-haiku-4-5")

    # Build few-shot messages
    messages = []
    for ex in REWRITE_FEWSHOT:
        messages.append({"role": "user", "content": ex["query"]})
        messages.append({"role": "assistant", "content": json.dumps(ex["rewrites"], ensure_ascii=False)})
    messages.append({"role": "user", "content": query})

    rewrites: list[str] | None = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=400,
                system=REWRITE_SYSTEM + (
                    "\n\n再次强调:只输出 JSON 数组,不要任何其他文字。" if attempt == 1 else ""
                ),
                messages=messages,
            )
            USAGE["input_tokens"] += resp.usage.input_tokens
            USAGE["output_tokens"] += resp.usage.output_tokens
            USAGE["calls"] += 1
            # Extract text
            content_text = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    content_text += block.text
            rewrites = _parse_string_array(content_text)
            if rewrites:
                break
        except Exception as e:
            import sys as _sys
            print(f"[rag.rewrite_query] attempt {attempt + 1} failed: {e}", file=_sys.stderr)

    if not rewrites:
        # Both attempts failed — fall back to original
        USAGE["fallbacks"] += 1
        result = [query]
    else:
        # Ensure original query is present
        if query not in rewrites:
            rewrites = rewrites + [query]
        # Cap at 6 to keep retrieval bounded
        result = rewrites[:6]

    # Persist to cache
    cache[query] = result
    _save_rewrite_cache(cache)
    return result


# ───────────────────────── filter helper ─────────────────────────

def _passes_filters(rec: dict, filters: Optional[dict]) -> bool:
    if not filters:
        return True
    if "volume" in filters and rec.get("volume") not in filters["volume"]:
        return False
    if "year_range" in filters:
        lo, hi = filters["year_range"]
        y = rec.get("year", 0)
        if not (lo <= y <= hi):
            return False
    if "category" in filters and rec.get("category") != filters["category"]:
        return False
    if "exclude_articles" in filters and rec.get("article_id") in filters["exclude_articles"]:
        return False
    return True
