"""Phase 1 stop-condition measurement (RETRIEVAL_UPGRADE_PLAN.md):
"Report the latency cost of the rewriting step."

Measures two things against the *real* configured LLM provider (not a
fake) -- these are genuine network round-trips, so they're the actual cost
being added, not an estimate:

  1. understand_query() -- runs on EVERY turn (intent classification is not
     conditional; only whether its rewrite gets used is -- see ADR 002).
  2. compaction._summarize() -- runs only on turns where session history has
     just crossed the token budget, but pays a similarly-shaped LLM call.

Compares (1) against Phase 0's baseline end-to-end retrieval latency
(backend/bench/results/baseline.json) to report the total delta a turn now
pays. A turn that both classifies AND triggers compaction pays roughly the
sum of both numbers below, plus baseline retrieval.

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
from app.query.compaction import _summarize as compaction_summarize
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


def sample_summarization_batches(db, course_id: int, n: int, seed: int) -> list[list]:
    """Synthesizes small batches of fake ChatMessage-like objects (only
    .role/.content are read by compaction._summarize) from real chunk text,
    so the summarization call has genuine content to condense."""
    from types import SimpleNamespace

    chunks = db.scalars(select(Chunk.text).where(Chunk.course_id == course_id).order_by(Chunk.id)).all()
    rng = random.Random(seed + 1)  # different draw than sample_followup_turns
    pool = chunks if len(chunks) >= n * 2 else chunks * (n * 2 // len(chunks) + 1)
    sampled = rng.sample(pool, n * 2)

    batches = []
    for i in range(n):
        a, b = sampled[2 * i], sampled[2 * i + 1]
        topic = " ".join(a.split()[:8])
        batches.append(
            [
                SimpleNamespace(role="user", content=f"Can you explain {topic}?"),
                SimpleNamespace(role="assistant", content=a[:300]),
                SimpleNamespace(role="user", content="Can you say more about that?"),
                SimpleNamespace(role="assistant", content=b[:300]),
            ]
        )
    return batches


def _measure(label: str, calls: list) -> dict:
    latencies_ms = []
    for call in calls:
        start = time.perf_counter()
        call()
        latencies_ms.append((time.perf_counter() - start) * 1000)
    summary = {
        "mean": round(statistics.mean(latencies_ms), 1),
        "p50": round(statistics.median(latencies_ms), 1),
        "p95": round(percentile(latencies_ms, 95), 1),
        "p99": round(percentile(latencies_ms, 99), 1),
    }
    n = len(latencies_ms)
    caveat = " (n<30: p95/p99 are just the slowest 1-2 samples, not a stable tail estimate)" if n < 30 else ""
    print(f"\n{label} (n={n}){caveat}:")
    print(f"  mean: {summary['mean']}ms  p50: {summary['p50']}ms  p95: {summary['p95']}ms  p99: {summary['p99']}ms")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--course-id", type=int, required=True)
    parser.add_argument("--n-queries", type=int, default=15)
    parser.add_argument("--n-summarization-calls", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--baseline", type=Path, default=Path("bench/results/baseline.json"))
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase1_query_understanding.json"))
    args = parser.parse_args()

    db = SessionLocal()
    provider = get_provider()
    try:
        turns = sample_followup_turns(db, args.course_id, args.n_queries, args.seed)
        summarization_batches = sample_summarization_batches(
            db, args.course_id, args.n_summarization_calls, args.seed
        )

        # Warm-up call, untimed -- excludes one-time connection setup from the
        # measured distribution (mirrors bench/baseline.py's model warm-up).
        understand_query(provider, turns[0][0], turns[0][1])

        rewrite_count = 0

        def run_understanding(history=None, query=None):
            nonlocal rewrite_count
            result = understand_query(provider, history, query)
            if result.rewritten_query:
                rewrite_count += 1

        understanding_summary = _measure(
            f"Query understanding latency (course_id={args.course_id})",
            [lambda h=h, q=q: run_understanding(h, q) for h, q in turns],
        )
        print(f"  ({rewrite_count}/{len(turns)} of those calls produced a rewrite)")

        compaction_summary = _measure(
            "Compaction summarization latency (compaction._summarize, triggers only once a session crosses HISTORY_TOKEN_BUDGET)",
            [lambda b=batch: compaction_summarize(provider, None, b) for batch in summarization_batches],
        )
    finally:
        db.close()

    baseline_end_to_end = None
    if args.baseline.exists():
        baseline = json.loads(args.baseline.read_text())
        baseline_end_to_end = baseline["latency"]["end_to_end_ms"]
        classify_only_mean = baseline_end_to_end["mean"] + understanding_summary["mean"]
        classify_and_compact_mean = classify_only_mean + compaction_summary["mean"]
        print(f"\nPhase 0 baseline end-to-end retrieval (mean): {baseline_end_to_end['mean']}ms")
        print(f"+ query understanding (every turn, mean): {understanding_summary['mean']}ms "
              f"-> ~{classify_only_mean:.0f}ms")
        print(f"+ compaction summarization (only on turns that trigger it, mean): {compaction_summary['mean']}ms "
              f"-> ~{classify_and_compact_mean:.0f}ms for a turn that pays both")
    else:
        print(f"\nNo baseline found at {args.baseline} -- run bench/baseline.py first for a delta comparison.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "course_id": args.course_id,
                "n_queries": len(turns),
                "rewrite_count": rewrite_count,
                "understanding_latency_ms": understanding_summary,
                "compaction_summarization_latency_ms": compaction_summary,
                "baseline_end_to_end_ms": baseline_end_to_end,
            },
            indent=2,
        )
    )
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
