# LLM rerank experiment — deferred (2026-04-22)

`backend/rag.py:retrieve_with_rerank` is implemented but not the production default. This note records what was tried, why it regressed, and concrete next steps.

## What was tried

Layer-4 of the retrieval stack: take top-16 candidates from `retrieve_with_rewrite` (hybrid + diversity + Haiku query rewriting), send them to Claude Haiku 4.5 along with the **original** user query, ask for top-K indices ordered by relevance with one-sentence justifications. Parse JSON, reorder candidates.

## Result on the 10-query test set

| | hybrid_rewrite (default) | hybrid_rewrite_rerank (experimental) |
|---|---|---|
| hit@1 | 33.3% | 33.3% |
| **hit@5** | **66.7%** | **44.4%** ← regressed |
| hit@10 | 77.8% | 77.8% |
| MRR | 0.455 | 0.405 |

Per-query change going `hybrid_rewrite → hybrid_rewrite_rerank`:

| Query | before | after | Δ |
|---|---|---|---|
| 我不知道该先做什么 → 矛盾论 | None | rank 9 | partial recovery (still not top-5) |
| 天天焦虑但什么都没做 → 实践论 | rank 5 ✓ | rank 6 ○ | regressed |
| 同龄人都比我强 → 论持久战 | None | None | unchanged |
| 和同事意见不合 → 矛盾论 | rank 1 ✓ | rank 5 ✓ | kept hit, degraded |
| 工作总是出错 → 改造/反对自由主义 | rank 4 ✓ | None | **regressed badly** |
| 选专业选错 → 实践论/反对本本主义 | rank 1 ✓ | rank 1 ✓ | unchanged |
| 创业看不到结果 → 论持久战 | rank 2 ✓ | rank 6 ○ | regressed |
| 跟父母想法不一样 → 矛盾论 | rank 1 ✓ | rank 1 ✓ | unchanged |
| 我做的事有什么意义 → 为人民服务/纪念白求恩 | rank 7 ○ | rank 1 ✓ | **big win** |

Net: 1 win, 3 regressions, 5 unchanged.

## Root cause

Haiku has a systematic bias toward "general methodology" chunks (反对本本主义 #2 调查就是解决问题, 反对党八股, 《农村调查》序). These chunks are *genuinely* high-relevance for almost any "how should I approach X" query, so Haiku ranks them high — even when the test set's ground-truth target is a more specific article. The pre-rewrite fusion was actually *better* at picking the canonical-target article because the rewrites had already focused the retrieval on specific concepts.

The reranker doesn't know what the rewriter was looking for; it only sees the original colloquial query and a candidate list.

## Four follow-up fixes to try

1. **Inject rewrites into rerank prompt.** Pass `original query + concept rewrites` so the reranker knows what conceptual targets the rewriter identified. Cheap, no extra API surface.
2. **Tighten rerank prompt.** Explicitly penalize generic methodology chunks; reward chunks whose article title matches a concept-keyword from the rewrites. Risk: brittle to query distribution.
3. **Use Sonnet for rerank.** Better reasoning over multi-candidate evaluation. ~5x cost ($0.04 → $0.20/eval). Try once, see if reasoning quality changes the bias.
4. **Diversity-aware rerank.** Score candidates not just by relevance but by marginal information gain over already-selected chunks (MMR-style). Could naturally suppress repeated methodology chunks.

## When to revisit

- Once real user query logs accumulate (Step 6+ ships chat UI). Real distribution will likely be different from this test set — rerank may win on real queries even though it lost on adversarial canonical-target queries.
- If a contributor has empirical evidence that rerank helps on their query mix.
- If the agent layer (Step 6) shows symptoms of "good chunks present in top-10 but agent picks the wrong one to quote" — rerank may help bubble the right one up.

## Reproducing

```bash
python -m backend.ingest.eval_retrieval --retriever incremental
# requires backend/.env with ANTHROPIC_API_KEY
```

Test set lives in `backend/ingest/eval_retrieval.py:TEST_SET`. Add real-user-query examples there as they accumulate.
