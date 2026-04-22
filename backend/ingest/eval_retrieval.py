"""Eval harness for the retrieval stack.

Runs the 10-query test set against a configurable retriever function, reports:
    hit@1, hit@5, hit@10, MRR
    attractors (chunks appearing in top-5 for ≥3 distinct queries)
    mean per-query diversity (distinct article_ids in top-10)

Negative tests are evaluated separately (pass if anti-expected title does NOT appear
in top-5).

CLI:
    python -m backend.ingest.eval_retrieval --retriever dense
    python -m backend.ingest.eval_retrieval --retriever bm25
    python -m backend.ingest.eval_retrieval --retriever hybrid
    python -m backend.ingest.eval_retrieval --retriever hybrid_diverse
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from backend import rag


# Test set — each entry is one of:
#   positive: expected titles (any match in top-K = hit)
#   negative: anti_expected (pass if NOT in top-5)
TEST_SET: list[dict] = [
    {"query": "我不知道该先做什么",       "expected": ["矛盾论"]},
    {"query": "天天焦虑但什么都没做",     "expected": ["实践论"]},
    {"query": "同龄人都比我强",           "expected": ["论持久战"]},
    {"query": "和同事意见不合",           "expected": ["矛盾论"]},  # 正确处理人民内部矛盾 is out-of-corpus
    {"query": "工作总是出错又不敢承认",   "expected": ["改造我们的学习", "反对自由主义"]},
    {"query": "选专业选错了想转行",       "expected": ["实践论", "反对本本主义"]},
    {"query": "创业一直看不到结果",       "expected": ["论持久战", "星星之火"]},  # prefix match
    {"query": "别人都不理解我",           "anti_expected": ["为人民服务"]},
    {"query": "跟父母想法不一样怎么办",   "expected": ["矛盾论"]},
    {"query": "我做的事有什么意义",       "expected": ["为人民服务", "纪念白求恩"]},
]


def _title_matches(needle: str, haystack: str) -> bool:
    """Match by substring — handles "星星之火" in "星星之火，可以燎原"."""
    return needle in haystack


def _rank_of_match(results, expected_titles: list[str]) -> int | None:
    """Lowest rank (1-indexed) where any expected title appears. None if absent."""
    for i, ch in enumerate(results, start=1):
        for t in expected_titles:
            if _title_matches(t, ch.article_title):
                return i
    return None


def _negative_in_top5(results, anti_titles: list[str]) -> str | None:
    """If any anti_title appears in top-5, return it. None if all pass."""
    for ch in results[:5]:
        for t in anti_titles:
            if _title_matches(t, ch.article_title):
                return t
    return None


@dataclass
class EvalReport:
    retriever: str
    n_positive: int
    hit_at_1: float
    hit_at_5: float
    hit_at_10: float
    mrr: float
    negative_pass: int
    negative_total: int
    attractors: list[tuple[str, str, int]]  # (chunk_id, article_title, n_queries)
    mean_diversity_top10: float
    per_query: list[dict]


def run(retriever_fn: Callable, name: str) -> EvalReport:
    per_query = []
    positive_matches_at: list[int | None] = []  # rank or None
    negative_pass = 0
    negative_total = 0
    top5_chunk_usage: Counter = Counter()
    top5_query_for: dict[str, set[str]] = {}
    diversity_counts: list[int] = []

    for tc in TEST_SET:
        q = tc["query"]
        results = retriever_fn(q, top_k=10)
        diversity_counts.append(len({ch.article_id for ch in results}))
        # Track top-5 chunk usage for attractor detection
        for ch in results[:5]:
            top5_chunk_usage[ch.chunk_id] += 1
            top5_query_for.setdefault(ch.chunk_id, set()).add(q)

        entry = {"query": q, "top_5": [(ch.article_title, ch.section_title) for ch in results[:5]]}
        if "expected" in tc:
            rank = _rank_of_match(results, tc["expected"])
            entry["expected"] = tc["expected"]
            entry["rank"] = rank
            positive_matches_at.append(rank)
        else:
            anti = tc["anti_expected"]
            hit = _negative_in_top5(results, anti)
            negative_total += 1
            if hit is None:
                negative_pass += 1
                entry["negative"] = f"PASS (anti={anti} absent from top-5)"
            else:
                entry["negative"] = f"FAIL (anti={hit} in top-5)"
        per_query.append(entry)

    n_pos = len(positive_matches_at)
    hit_at = lambda k: sum(1 for r in positive_matches_at if r is not None and r <= k) / max(n_pos, 1)
    mrr = sum(1.0 / r for r in positive_matches_at if r is not None) / max(n_pos, 1)

    attractors = []
    for cid, count in top5_chunk_usage.most_common():
        if count >= 3:
            queries = list(top5_query_for[cid])
            # Load chunk metadata once for display
            c = rag._load_chunks_by_id()[cid]
            attractors.append((cid, c["article_title"], count))

    return EvalReport(
        retriever=name,
        n_positive=n_pos,
        hit_at_1=hit_at(1),
        hit_at_5=hit_at(5),
        hit_at_10=hit_at(10),
        mrr=mrr,
        negative_pass=negative_pass,
        negative_total=negative_total,
        attractors=attractors,
        mean_diversity_top10=sum(diversity_counts) / len(diversity_counts),
        per_query=per_query,
    )


def print_report(r: EvalReport) -> None:
    print(f"\n{'='*72}\nRETRIEVER: {r.retriever}\n{'='*72}")
    print(f"Positive queries:    {r.n_positive}")
    print(f"  hit@1  = {r.hit_at_1*100:5.1f}%")
    print(f"  hit@5  = {r.hit_at_5*100:5.1f}%")
    print(f"  hit@10 = {r.hit_at_10*100:5.1f}%")
    print(f"  MRR    = {r.mrr:.4f}")
    print(f"Negative queries:    {r.negative_pass}/{r.negative_total} pass")
    print(f"Mean diversity in top-10: {r.mean_diversity_top10:.2f} distinct articles")
    print(f"Attractors (top-5 in ≥3 queries):")
    if r.attractors:
        for cid, title, count in r.attractors[:5]:
            print(f"  {count}× {cid}  ({title})")
    else:
        print(f"  (none)")
    print(f"\nPer-query detail:")
    for e in r.per_query:
        if "rank" in e:
            rk = e["rank"]
            mark = "✓" if rk and rk <= 5 else ("○" if rk and rk <= 10 else "✗")
            print(f"  {mark} {e['query']!r:<32}  rank={rk}  expected={e['expected']}")
            for t, s in e["top_5"][:3]:
                print(f"      → {t} / {s}")
        else:
            print(f"  ? {e['query']!r:<32}  {e['negative']}")


RETRIEVERS = {
    "dense": rag.retrieve_dense,
    "bm25": rag.retrieve_bm25,
    "hybrid": rag.retrieve_hybrid,
    "hybrid_diverse": rag.retrieve_hybrid_diverse,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retriever", choices=list(RETRIEVERS.keys()) + ["all"], default="all")
    args = ap.parse_args()

    names = list(RETRIEVERS.keys()) if args.retriever == "all" else [args.retriever]
    for n in names:
        r = run(RETRIEVERS[n], n)
        print_report(r)


if __name__ == "__main__":
    main()
