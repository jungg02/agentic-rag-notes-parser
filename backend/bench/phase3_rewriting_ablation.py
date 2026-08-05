"""Phase 3 acceptance criterion: "Multi-turn improvement quantified (this
is the headline number)."

Zero new LLM calls -- reads the rewrites phase3_llm_collect.py already
cached and re-runs the (local-only) reranked retrieval path with the
rewritten query substituted in, for every multi_turn_coreference and
topic_switch item. Compares against phase3_retrieval_ablation.json's
"reranked" numbers for those same categories, which were computed on the
raw last-turn text -- i.e. the "no rewrite" condition.

Reported broken out by category, not pooled, and this is deliberate:
  - multi_turn_coreference is where a rewrite is actually needed (the raw
    last turn has an unresolved pronoun/reference) -- this is the headline
    uplift number the plan asks for.
  - topic_switch's raw last turn is *already* standalone by construction
    (the whole point of that category is testing that the system doesn't
    wrongly drag turn-1 context into an unrelated turn-2 query) -- correct
    behavior there is close-to-zero uplift, and pooling it with coreference
    would understate the real coreference win and misrepresent both.

Usage (run inside the backend container, after phase3_llm_collect.py):

    docker compose run --rm backend python bench/phase3_rewriting_ablation.py \\
        --output bench/results/phase3_rewriting_ablation.json
"""
from __future__ import annotations

import json
import sys
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
from app.retrieval.vector import search_vector
from bench.phase3_dataset import DEFAULT_EVAL_FILE, REWRITE_CATEGORIES, load_items
from bench.phase3_llm_collect import CACHE_PATH, Cache
from bench.phase3_metrics import PageKey, first_hit_rank, mean, mrr_at_k, ndcg_at_k, recall_at_k
from bench.phase3_retrieval_ablation import TOP_K_NDCG, TOP_K_RECALL, _chunk_keys, _fetch_chunks


def _reranked_keys(db: Session, course_id: int, query: str) -> list[PageKey]:
    lexical_ids = search_lexical(db, course_id, query, limit=50)
    vector_ids = search_vector(db, course_id, embed_query(query), limit=50)
    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:20]

    chunks_by_id = _fetch_chunks(db, fused_ids)
    candidates = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]
    reranked = rerank(query, candidates, top_k=TOP_K_NDCG)
    for sc in reranked:
        chunks_by_id[sc.chunk.id] = sc.chunk
    return _chunk_keys(chunks_by_id, [sc.chunk.id for sc in reranked])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase3_rewriting_ablation.json"))
    args = parser.parse_args()

    course_id, items = load_items(args.eval_file)
    cache_rows = {(r["item_id"]): r for r in Cache(args.cache).load_all() if r["kind"] == "rewrite" and r["ok"]}

    target_items = [i for i in items if i.category in REWRITE_CATEGORIES]
    missing = [i.id for i in target_items if i.id not in cache_rows]
    if missing:
        print(f"Warning: {len(missing)} items have no cached rewrite (run phase3_llm_collect.py first): {missing}")
    target_items = [i for i in target_items if i.id in cache_rows]
    if not target_items:
        raise SystemExit("No items with cached rewrites -- nothing to compare.")

    db = SessionLocal()
    by_category: dict[str, dict[str, dict[str, list]]] = defaultdict(
        lambda: {"raw": {"ranks": [], "ndcg": []}, "rewritten": {"ranks": [], "ndcg": []}}
    )
    try:
        for item in target_items:
            raw_keys = _reranked_keys(db, course_id, item.query)
            rewritten_query = cache_rows[item.id]["data"]["rewritten_query"] or item.query
            rewritten_keys = _reranked_keys(db, course_id, rewritten_query)

            cat = by_category[item.category]
            cat["raw"]["ranks"].append(first_hit_rank(raw_keys[:TOP_K_RECALL], item.expected))
            cat["raw"]["ndcg"].append(ndcg_at_k(raw_keys, item.expected, TOP_K_NDCG))
            cat["rewritten"]["ranks"].append(first_hit_rank(rewritten_keys[:TOP_K_RECALL], item.expected))
            cat["rewritten"]["ndcg"].append(ndcg_at_k(rewritten_keys, item.expected, TOP_K_NDCG))
    finally:
        db.close()

    result = {}
    for category, conditions in by_category.items():
        result[category] = {
            condition: {
                "recall_at_6": round(recall_at_k(vals["ranks"]), 4),
                "mrr_at_6": round(mrr_at_k(vals["ranks"]), 4),
                "ndcg_at_10": round(mean(vals["ndcg"]), 4),
                "n": len(vals["ranks"]),
            }
            for condition, vals in conditions.items()
        }

    print(f"\nQuery-rewriting ablation ({len(target_items)} items with cached rewrites)")
    for category, conditions in result.items():
        print(f"\n{category}:")
        print(f"  {'condition':<12} {'recall@6':>10} {'mrr@6':>10} {'ndcg@10':>10}")
        for condition, m in conditions.items():
            print(f"  {condition:<12} {m['recall_at_6']:>10.2%} {m['mrr_at_6']:>10.3f} {m['ndcg_at_10']:>10.3f}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
