"""Pure retrieval-quality scoring functions for the Phase 3 ablation harness
(RETRIEVAL_UPGRADE_PLAN.md). No DB, no LLM, no I/O -- kept separate from
phase3_retrieval_ablation.py specifically so these can be unit tested
directly (see tests/bench/test_phase3_metrics.py), same reasoning as
baseline.py's _human_bytes/percentile split in Phase 0.

Relevance is binary throughout: a chunk is relevant if its (document,
page) key is in the item's `expected` set, irrelevant otherwise. Multiple
expected pages for one query (e.g. a comparison question spanning two
source pages) all count as equally relevant -- there's no partial-credit
notion of "more relevant" between them.
"""
from __future__ import annotations

import math

PageKey = tuple[str, int]


def first_hit_rank(ranked_keys: list[PageKey], expected: set[PageKey]) -> int | None:
    """1-indexed rank of the first relevant result, or None if none of the
    ranked results are relevant."""
    for rank, key in enumerate(ranked_keys, start=1):
        if key in expected:
            return rank
    return None


def recall_at_k(hit_ranks: list[int | None]) -> float:
    if not hit_ranks:
        return 0.0
    return sum(1 for r in hit_ranks if r is not None) / len(hit_ranks)


def mrr_at_k(hit_ranks: list[int | None]) -> float:
    if not hit_ranks:
        return 0.0
    return sum(1.0 / r if r is not None else 0.0 for r in hit_ranks) / len(hit_ranks)


def ndcg_at_k(ranked_keys: list[PageKey], expected: set[PageKey], k: int) -> float:
    """Binary-relevance nDCG@k. IDCG is computed from min(len(expected), k)
    -- the best achievable DCG when the top of the ranking is filled with
    exactly the relevant items available, capped at k. Without that cap, a
    query with more expected pages than k could never reach 1.0 even with a
    perfect ranking, which would make nDCG@k values incomparable across
    items that have different numbers of expected pages."""
    if not expected or k <= 0:
        return 0.0

    dcg = 0.0
    for i, key in enumerate(ranked_keys[:k]):
        if key in expected:
            dcg += 1.0 / math.log2(i + 2)  # i is 0-indexed, rank = i+1, log2(rank+1)

    ideal_hits = min(len(expected), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
