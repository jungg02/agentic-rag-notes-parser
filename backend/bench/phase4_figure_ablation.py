"""Phase 4 acceptance criteria (RETRIEVAL_UPGRADE_PLAN.md):
"Text query retrieves relevant figures" / "Cross-modal results fused and
ranked sensibly" / "Evaluated with the Phase 3 harness -- not just
demoed."

Zero-LLM, mirrors phase3_retrieval_ablation.py's shape: recall@k/mrr@k/
nDCG@10 over scripts/eval/phase4_figure_queries.json, using
retrieve_figures() (SigLIP dense search fused with caption lexical
search, ADR 013) -- the actual production figure-retrieval path,
unmodified.

Reports two rows, "fused" and "dense_only", specifically because caption
generation was evaluated and deferred this build (ADR 014's addendum):
`figures.caption` is null for every row in this corpus, so the lexical
arm currently contributes nothing and "fused" is expected to equal
"dense_only" exactly. Both are reported anyway -- if a caption pipeline
is added later, this script (unchanged) would start showing them
diverge, and that diff is the only future signal needed to confirm
captions are actually improving figure retrieval.

Usage (run inside the backend container):

    docker compose run --rm backend python bench/phase4_figure_ablation.py \\
        --output bench/results/phase4_figure_ablation.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingestion.image_embedder import embed_image_query
from app.retrieval.figures import FIGURE_MAX_PER_QUERY, _dense_candidates, retrieve_figures
from bench.phase3_metrics import PageKey, first_hit_rank, mean, mrr_at_k, ndcg_at_k, recall_at_k

DEFAULT_EVAL_FILE = Path(__file__).resolve().parent.parent / "scripts" / "eval" / "phase4_figure_queries.json"
TOP_K = FIGURE_MAX_PER_QUERY  # 3 -- matches what chat_service.py actually surfaces per turn
NDCG_K = 10


def load_items(path: Path) -> tuple[int, list[dict]]:
    raw = json.loads(path.read_text())
    return raw["course_id"], raw["items"]


def _keys_for_ids(db: Session, figure_ids: list[int]) -> list[PageKey]:
    from app.models import Figure
    from sqlalchemy import select

    figures_by_id = {f.id: f for f in db.scalars(select(Figure).where(Figure.id.in_(figure_ids))).all()}
    return [
        (figures_by_id[fid].document.original_filename, figures_by_id[fid].page_number)
        for fid in figure_ids
        if fid in figures_by_id
    ]


def evaluate(db: Session, course_id: int, items: list[dict]) -> dict:
    fused_ranks, fused_ndcg = [], []
    dense_ranks, dense_ndcg = [], []
    latencies_ms = []

    # Untimed warm-up: loads the SigLIP model into memory so the first timed
    # iteration below isn't measuring model load instead of query latency.
    embed_image_query(items[0]["query"])

    for item in items:
        expected = {(e["document"], e["page"]) for e in item["expected"]}
        query = item["query"]

        t0 = time.perf_counter()
        query_embedding = embed_image_query(query)
        fused = retrieve_figures(db, course_id, query, query_embedding, limit=NDCG_K)
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        fused_keys = _keys_for_ids(db, [sf.figure.id for sf in fused])

        dense_ids = [fid for fid, _ in _dense_candidates(db, course_id, query_embedding)][:NDCG_K]
        dense_keys = _keys_for_ids(db, dense_ids)

        fused_ranks.append(first_hit_rank(fused_keys[:TOP_K], expected))
        fused_ndcg.append(ndcg_at_k(fused_keys, expected, NDCG_K))
        dense_ranks.append(first_hit_rank(dense_keys[:TOP_K], expected))
        dense_ndcg.append(ndcg_at_k(dense_keys, expected, NDCG_K))

    def summarize(ranks, ndcg) -> dict:
        return {
            f"recall_at_{TOP_K}": round(recall_at_k(ranks), 4),
            f"mrr_at_{TOP_K}": round(mrr_at_k(ranks), 4),
            "ndcg_at_10": round(mean(ndcg), 4),
        }

    return {
        "n_items": len(items),
        "fused": summarize(fused_ranks, fused_ndcg),
        "dense_only": summarize(dense_ranks, dense_ndcg),
        "latency_ms": {"mean": round(mean(latencies_ms), 1)},
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase4_figure_ablation.json"))
    args = parser.parse_args()

    course_id, items = load_items(args.eval_file)
    if not items:
        raise SystemExit("Eval file has no items.")

    db = SessionLocal()
    try:
        result = evaluate(db, course_id, items)
    finally:
        db.close()

    print(f"\nFigure retrieval ablation -- {result['n_items']} items, course_id={course_id}, top_k={TOP_K}")
    print(f"{'mode':<12} {f'recall@{TOP_K}':>10} {f'mrr@{TOP_K}':>10} {'ndcg@10':>10}")
    for mode in ["fused", "dense_only"]:
        m = result[mode]
        print(f"{mode:<12} {m[f'recall_at_{TOP_K}']:>10.2%} {m[f'mrr_at_{TOP_K}']:>10.3f} {m['ndcg_at_10']:>10.3f}")
    if result["fused"] == result["dense_only"]:
        print("\n(fused == dense_only exactly -- expected: every figure's caption is null this build, ADR 014)")
    print(f"\nMean latency per query (embed + retrieve): {result['latency_ms']['mean']}ms")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
