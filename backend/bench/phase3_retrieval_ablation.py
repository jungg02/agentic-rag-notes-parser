"""Phase 3 acceptance criterion (RETRIEVAL_UPGRADE_PLAN.md): "Ablation table
with per-configuration numbers" -- lexical only / dense only / RRF fused /
fused + cross-encoder rerank.

Zero-LLM by design: every item's *effective query* here is the raw text of
the turn being scored (EvalItem.query), never a rewritten one -- this
ablation isolates the retrieval mechanism, holding query processing
constant. The query-rewriting ablation (phase3_llm_collect.py /
phase3_report.py) is a separate axis layered on top of this one, only for
the categories where rewriting applies.

Two different top_k values are reported deliberately, not interchangeably:
  - Recall@6 / MRR@6 use top_k=6, matching FINAL_TOP_K -- what production
    retrieve() actually returns per turn.
  - nDCG@10 uses top_k=10, per the plan's literal "nDCG@10" wording. The
    "reranked" mode's top-10 list comes from a *separate* rerank() call
    over the same fused candidates (not a truncation of the top-6 list),
    since a top_k=6 rerank and a top_k=10 rerank over the same candidate
    pool aren't guaranteed to agree on the first 6 positions.

Usage (run inside the backend container so the DB and models are reachable):

    docker compose run --rm backend python bench/phase3_retrieval_ablation.py \\
        --output bench/results/phase3_retrieval_ablation.json
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingestion.embedder import embed_query
from app.models import Chunk
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import rerank
from app.retrieval.service import FUSED_CANDIDATES
from app.retrieval.vector import search_vector
from bench.phase3_dataset import DEFAULT_EVAL_FILE, EvalItem, load_items
from bench.phase3_metrics import PageKey, first_hit_rank, mean, mrr_at_k, ndcg_at_k, recall_at_k

MODES = ["lexical", "vector", "fused", "reranked"]
TOP_K_RECALL = 6  # matches app.retrieval.service.FINAL_TOP_K
TOP_K_NDCG = 10


def _chunk_keys(chunks_by_id: dict[int, Chunk], ids: list[int]) -> list[PageKey]:
    return [
        (chunks_by_id[cid].document.original_filename, chunks_by_id[cid].page_number)
        for cid in ids
        if cid in chunks_by_id
    ]


def _fetch_chunks(db: Session, chunk_ids: list[int]) -> dict[int, Chunk]:
    if not chunk_ids:
        return {}
    rows = db.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids))).all()
    return {c.id: c for c in rows}


def evaluate_item(db: Session, course_id: int, item: EvalItem) -> dict[str, list[PageKey]]:
    """Returns each mode's ranked (document, page) list, truncated to
    TOP_K_NDCG (the widest cutoff any metric here needs)."""
    query = item.query
    lexical_ids = search_lexical(db, course_id, query, limit=50)
    query_embedding = embed_query(query)
    vector_ids = search_vector(db, course_id, query_embedding, limit=50)
    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])

    fused_candidate_ids = fused_ids[:FUSED_CANDIDATES]
    needed_ids = list(set(lexical_ids[:TOP_K_NDCG]) | set(vector_ids[:TOP_K_NDCG]) | set(fused_candidate_ids))
    chunks_by_id = _fetch_chunks(db, needed_ids)

    candidates = [chunks_by_id[cid] for cid in fused_candidate_ids if cid in chunks_by_id]
    reranked = rerank(query, candidates, top_k=TOP_K_NDCG)
    reranked_ids = [sc.chunk.id for sc in reranked]
    for sc in reranked:
        chunks_by_id[sc.chunk.id] = sc.chunk

    return {
        "lexical": _chunk_keys(chunks_by_id, lexical_ids[:TOP_K_NDCG]),
        "vector": _chunk_keys(chunks_by_id, vector_ids[:TOP_K_NDCG]),
        "fused": _chunk_keys(chunks_by_id, fused_ids[:TOP_K_NDCG]),
        "reranked": _chunk_keys(chunks_by_id, reranked_ids),
    }


def run(db: Session, course_id: int, items: list[EvalItem]) -> dict:
    # Warm up model loads (embedding + cross-encoder) once, untimed.
    evaluate_item(db, course_id, items[0])

    per_mode_ranks: dict[str, list[int | None]] = {mode: [] for mode in MODES}
    per_mode_ndcg: dict[str, list[float]] = {mode: [] for mode in MODES}
    by_category: dict[str, dict[str, list[int | None]]] = defaultdict(lambda: {mode: [] for mode in MODES})
    by_category_ndcg: dict[str, dict[str, list[float]]] = defaultdict(lambda: {mode: [] for mode in MODES})
    latencies_ms: list[float] = []

    for item in items:
        t0 = time.perf_counter()
        mode_keys = evaluate_item(db, course_id, item)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        for mode in MODES:
            keys = mode_keys[mode]
            rank = first_hit_rank(keys[:TOP_K_RECALL], item.expected)
            per_mode_ranks[mode].append(rank)
            by_category[item.category][mode].append(rank)

            ndcg = ndcg_at_k(keys, item.expected, TOP_K_NDCG)
            per_mode_ndcg[mode].append(ndcg)
            by_category_ndcg[item.category][mode].append(ndcg)

    overall = {
        mode: {
            "recall_at_6": round(recall_at_k(per_mode_ranks[mode]), 4),
            "mrr_at_6": round(mrr_at_k(per_mode_ranks[mode]), 4),
            "ndcg_at_10": round(mean(per_mode_ndcg[mode]), 4),
        }
        for mode in MODES
    }
    per_category = {
        category: {
            mode: {
                "recall_at_6": round(recall_at_k(by_category[category][mode]), 4),
                "mrr_at_6": round(mrr_at_k(by_category[category][mode]), 4),
                "ndcg_at_10": round(mean(by_category_ndcg[category][mode]), 4),
                "n": len(by_category[category][mode]),
            }
            for mode in MODES
        }
        for category in by_category
    }

    return {
        "n_items": len(items),
        "overall": overall,
        "by_category": per_category,
        "latency_ms": {
            "mean": round(mean(latencies_ms), 1),
        },
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase3_retrieval_ablation.json"))
    args = parser.parse_args()

    course_id, items = load_items(args.eval_file)
    if not items:
        raise SystemExit("Eval file has no items.")

    db = SessionLocal()
    try:
        result = run(db, course_id, items)
    finally:
        db.close()

    print(f"\nRetrieval-mechanism ablation -- {result['n_items']} items, course_id={course_id}")
    print(f"{'mode':<10} {'recall@6':>10} {'mrr@6':>10} {'ndcg@10':>10}")
    for mode in MODES:
        o = result["overall"][mode]
        print(f"{mode:<10} {o['recall_at_6']:>10.2%} {o['mrr_at_6']:>10.3f} {o['ndcg_at_10']:>10.3f}")
    print(f"\nMean per-item latency (all 4 modes computed together): {result['latency_ms']['mean']}ms")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
