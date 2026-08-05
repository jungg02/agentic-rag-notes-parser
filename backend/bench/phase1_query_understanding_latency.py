"""Phase 1 stop-condition measurement (RETRIEVAL_UPGRADE_PLAN.md):
"Report the latency cost of the rewriting step."

Measures understand_query()'s latency against the *real* configured LLM
provider (not a fake) -- this is a genuine network round-trip, so it's the
actual cost being added to every chat turn, not an estimate. Compares
against Phase 0's baseline end-to-end retrieval latency
(backend/bench/results/baseline.json) to report the total delta a follow-up
turn now pays.

Usage (run inside the backend container so the DB, models, and configured
LLM provider are reachable):

    docker compose run --rm backend python bench/phase1_query_understanding_latency.py \\
        --course-id 4 --seed 0 --n-queries 15 \\
        --output bench/results/phase1_query_understanding.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk
from app.providers.base import LLMMessage
from app.providers.factory import get_provider
from app.query.understanding import understand_query


def percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def sample_followup_turns(db, course_id: int, n: int, seed: int) -> list[tuple[list[LLMMessage], str]]:
    """Synthesizes (history, follow_up_query) pairs from real chunk text so
    the understanding call has genuine context to resolve against -- same
    spirit as bench/baseline.py's synthetic queries: realistic shape, not
    meant to be read as real user questions."""
    chunks = db.scalars(select(Chunk.text).where(Chunk.course_id == course_id).order_by(Chunk.id)).all()
    if not chunks:
        raise SystemExit(f"Course {course_id} has no chunks -- ingest a document into it first.")

    rng = random.Random(seed)
    pool = chunks if len(chunks) >= n else chunks * (n // len(chunks) + 1)
    sampled = rng.sample(pool, n) if len(pool) >= n else pool[:n]

    turns = []
    for text in sampled:
        topic = " ".join(text.split()[:8])
        history = [
            LLMMessage(role="user", content=f"Can you explain {topic}?"),
            LLMMessage(role="assistant", content=text[:300]),
        ]
        turns.append((history, "Can you give me another example of that?"))
    return turns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--course-id", type=int, required=True)
    parser.add_argument("--n-queries", type=int, default=15)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--baseline", type=Path, default=Path("bench/results/baseline.json"))
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase1_query_understanding.json"))
    args = parser.parse_args()

    db = SessionLocal()
    provider = get_provider()
    try:
        turns = sample_followup_turns(db, args.course_id, args.n_queries, args.seed)

        # Warm-up call, untimed -- excludes one-time connection setup from the
        # measured distribution (mirrors bench/baseline.py's model warm-up).
        understand_query(provider, turns[0][0], turns[0][1])

        latencies_ms = []
        rewrite_count = 0
        for history, query in turns:
            start = time.perf_counter()
            result = understand_query(provider, history, query)
            latencies_ms.append((time.perf_counter() - start) * 1000)
            if result.rewritten_query:
                rewrite_count += 1
    finally:
        db.close()

    summary = {
        "mean": round(statistics.mean(latencies_ms), 1),
        "p50": round(statistics.median(latencies_ms), 1),
        "p95": round(percentile(latencies_ms, 95), 1),
        "p99": round(percentile(latencies_ms, 99), 1),
    }

    print(f"\nQuery understanding latency (course_id={args.course_id}, n={len(turns)}, "
          f"{rewrite_count}/{len(turns)} rewritten):")
    print(f"  mean: {summary['mean']}ms  p50: {summary['p50']}ms  p95: {summary['p95']}ms  p99: {summary['p99']}ms")

    baseline_end_to_end = None
    if args.baseline.exists():
        baseline = json.loads(args.baseline.read_text())
        baseline_end_to_end = baseline["latency"]["end_to_end_ms"]
        new_mean = baseline_end_to_end["mean"] + summary["mean"]
        delta_pct = summary["mean"] / baseline_end_to_end["mean"] * 100
        print(f"\nPhase 0 baseline end-to-end retrieval (mean): {baseline_end_to_end['mean']}ms")
        print(f"Phase 1 adds this call in front of it: +{summary['mean']}ms ({delta_pct:.0f}% of baseline)")
        print(f"New theoretical end-to-end for a turn that pays this cost: ~{new_mean:.1f}ms")
    else:
        print(f"\nNo baseline found at {args.baseline} -- run bench/baseline.py first for a delta comparison.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "course_id": args.course_id,
                "n_queries": len(turns),
                "rewrite_count": rewrite_count,
                "understanding_latency_ms": summary,
                "baseline_end_to_end_ms": baseline_end_to_end,
            },
            indent=2,
        )
    )
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
