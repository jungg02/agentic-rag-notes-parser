"""Phase 2 acceptance criterion (RETRIEVAL_UPGRADE_PLAN.md): "Memory
retrieval adds < 50ms p95."

Measures retrieve_memories() alone -- the pgvector cosine search plus
Python-side decay-reranking, no LLM call involved. Unlike Phase 0's chunk
benchmark, scale doesn't need to match a large real corpus to be
representative: MEMORY_MAX_PER_COURSE caps a course at 50 memories
regardless of how long the app has been used, so benchmarking against
~20 memories *is* close to the realistic ceiling, not a shortcut.

chat_service.py calls embed_query() a second time specifically for the
memory-retrieval query (see its comment on why), so the *total* added
latency for a turn is embed_query() + retrieve_memories() -- reusing
Phase 0's baseline embed measurement (bench/results/baseline.json) rather
than re-measuring the same embedding model here.

Usage (run inside the backend container so the DB and models are reachable):

    docker compose run --rm backend python bench/phase2_memory_retrieval_latency.py \\
        --n-queries 30 --seed 0 --output bench/results/phase2_memory_retrieval.json
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

from app.db import SessionLocal
from app.ingestion.embedder import embed_query, embed_texts
from app.memory.retrieval import retrieve_memories
from app.models import Course, Memory

_SAMPLE_MEMORIES = [
    ("preference", "Prefers worked examples over abstract theory."),
    ("preference", "Likes step-by-step derivations rather than just the final formula."),
    ("struggle", "Struggles with recursive backtracking algorithms."),
    ("struggle", "Confused by the difference between covariance and correlation."),
    ("topic", "Has been studying hypothesis testing and p-values."),
    ("topic", "Recently covered dynamic programming and memoization."),
    ("goal", "Preparing for a final exam covering statistical inference."),
    ("goal", "Building a small project applying sorting algorithms."),
    ("preference", "Prefers concise answers over long explanations."),
    ("struggle", "Finds Bayesian reasoning unintuitive compared to frequentist methods."),
    ("topic", "Asked several questions about data frame manipulation in R."),
    ("topic", "Studied merge sort and quicksort time complexity."),
    ("goal", "Wants to understand the material well enough to explain it to a study group."),
    ("preference", "Responds well to real-world analogies."),
    ("struggle", "Mixed up variance and standard deviation in a recent question."),
    ("topic", "Covered linear regression assumptions in depth."),
    ("goal", "Preparing a project report due at the end of the term."),
    ("preference", "Prefers seeing code before the underlying math."),
    ("struggle", "Struggles to keep track of index arithmetic in nested loops."),
    ("topic", "Has asked about hash tables and collision resolution multiple times."),
]


def percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def seed_benchmark_course(db) -> Course:
    course = Course(name="Phase 2 Memory Retrieval Bench Course")
    db.add(course)
    db.flush()

    vectors = embed_texts([content for _, content in _SAMPLE_MEMORIES])
    for (memory_type, content), vector in zip(_SAMPLE_MEMORIES, vectors):
        db.add(
            Memory(
                course_id=course.id,
                content=content,
                embedding=vector,
                memory_type=memory_type,
                confidence=0.8,
            )
        )
    db.commit()
    return course


def sample_queries(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    templates = [
        "How do they like explanations delivered?",
        "What has this student been struggling with?",
        "What topics has this student covered recently?",
        "What is this student's goal this term?",
        "Can you tailor your answer to how they learn best?",
    ]
    pool = templates * (n // len(templates) + 1)
    return rng.sample(pool, n) if len(pool) >= n else pool[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-queries", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("bench/results/phase2_memory_retrieval.json"))
    args = parser.parse_args()

    db = SessionLocal()
    course = None
    try:
        course = seed_benchmark_course(db)
        queries = sample_queries(args.n_queries, args.seed)

        # Warm-up, untimed -- excludes one-time connection/index warm state.
        retrieve_memories(db, course.id, embed_query(queries[0]), bump_access=False)

        latencies_ms = []
        for query in queries:
            query_embedding = embed_query(query)
            start = time.perf_counter()
            retrieve_memories(db, course.id, query_embedding, bump_access=False)
            latencies_ms.append((time.perf_counter() - start) * 1000)
    finally:
        if course is not None:
            db.delete(course)
            db.commit()
        db.close()

    summary = {
        "mean": round(statistics.mean(latencies_ms), 2),
        "p50": round(statistics.median(latencies_ms), 2),
        "p95": round(percentile(latencies_ms, 95), 2),
        "p99": round(percentile(latencies_ms, 99), 2),
    }

    print(f"\nMemory retrieval latency (retrieve_memories() only, n={len(latencies_ms)}, "
          f"{len(_SAMPLE_MEMORIES)} memories):")
    print(f"  mean: {summary['mean']}ms  p50: {summary['p50']}ms  p95: {summary['p95']}ms  p99: {summary['p99']}ms")
    print(f"  acceptance criterion (<50ms p95): {'PASS' if summary['p95'] < 50 else 'FAIL'}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"n_memories": len(_SAMPLE_MEMORIES), "latency_ms": summary}, indent=2))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
