"""Phase 0 baseline benchmark (RETRIEVAL_UPGRADE_PLAN.md).

Measures, against the corpus currently ingested in the database:

  - corpus size: documents (by status), chunks, on-disk size of the chunks
    table and its two search indexes (tsv GIN, embedding HNSW)
  - retrieval latency p50/p95/p99, broken down by stage: lexical, embed
    (query encoding), vector (pgvector ANN search), fusion, candidate
    hydration, rerank
  - end-to-end query latency (the actual `retrieval.service.retrieve()`
    entrypoint, not the sum of the stages above)

This is a latency/scale baseline only — it does not score retrieval
*quality* (that needs a hand-labeled question set; see
`scripts/eval_retrieval.py` and Phase 3 of the plan). Benchmark queries are
synthesized from real chunk text in the target course (first several words
of a random sample of chunks) purely to drive realistic-shaped queries
through every stage; they are not meant to be read as questions.

Usage (run inside the backend container so the DB and models are reachable).
Pin --course-id and --seed for results comparable across runs — without
--course-id this benchmarks whichever course currently has the most chunks,
which can silently change as the corpus grows:

    docker compose run --rm backend python bench/baseline.py \\
        --course-id 6 --seed 0 --n-queries 30 \\
        --output bench/results/baseline.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingestion.embedder import embed_query
from app.models import Chunk, Document
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import rerank
from app.retrieval.service import retrieve
from app.retrieval.vector import search_vector

STAGES = ["lexical", "embed", "vector", "fusion", "hydrate", "rerank"]


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ["B", "kB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def corpus_size(db: Session, benchmarked_course_id: int) -> dict:
    """Global corpus totals (every course in the DB) plus the chunk count
    for the specific course the latency benchmark below actually queries
    against (search_lexical/search_vector both filter by course_id, so the
    two numbers are not the same thing and must not be conflated when
    reporting results)."""
    doc_counts = dict(
        db.execute(select(Document.ingest_status, func.count(Document.id)).group_by(Document.ingest_status)).all()
    )
    chunk_count = db.scalar(select(func.count(Chunk.id))) or 0
    benchmarked_course_chunk_count = db.scalar(
        select(func.count(Chunk.id)).where(Chunk.course_id == benchmarked_course_id)
    ) or 0

    per_table = {}
    for label, relation in [
        ("chunks_table", "chunks"),
        ("chunks_tsv_gin_index", "chunks_tsv_gin"),
        ("chunks_embedding_hnsw_index", "chunks_embedding_hnsw"),
    ]:
        size = db.execute(text("SELECT pg_total_relation_size(:rel)"), {"rel": relation}).scalar()
        per_table[label] = {"bytes": size, "human": _human_bytes(size)}

    return {
        "documents_by_status": doc_counts,
        "documents_total": sum(doc_counts.values()),
        "chunks_total": chunk_count,
        "benchmarked_course_chunks": benchmarked_course_chunk_count,
        "disk": per_table,
    }


def pick_course_id(db: Session, requested: int | None) -> int:
    if requested is not None:
        return requested
    row = db.execute(
        select(Chunk.course_id, func.count(Chunk.id)).group_by(Chunk.course_id).order_by(func.count(Chunk.id).desc())
    ).first()
    if row is None:
        raise SystemExit("No chunks in the database — ingest at least one document before benchmarking.")
    return row[0]


def sample_queries(db: Session, course_id: int, n: int, seed: int) -> list[str]:
    chunks = db.scalars(select(Chunk.text).where(Chunk.course_id == course_id).order_by(Chunk.id)).all()
    if not chunks:
        raise SystemExit(f"Course {course_id} has no chunks — ingest a document into it first.")

    rng = random.Random(seed)
    pool = chunks if len(chunks) >= n else chunks * (n // len(chunks) + 1)
    sampled = rng.sample(pool, n) if len(pool) >= n else pool[:n]
    return [" ".join(text.split()[:10]) for text in sampled]


def percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def run_benchmark(db: Session, course_id: int, queries: list[str]) -> dict:
    stage_ms: dict[str, list[float]] = defaultdict(list)
    end_to_end_ms: list[float] = []

    # Warm up model loads (embedding + cross-encoder) once, untimed.
    retrieve(db, course_id, queries[0])

    for query in queries:
        t0 = time.perf_counter()
        lexical_ids = search_lexical(db, course_id, query, limit=50)
        t1 = time.perf_counter()

        query_embedding = embed_query(query)
        t2 = time.perf_counter()
        vector_ids = search_vector(db, course_id, query_embedding, limit=50)
        t3 = time.perf_counter()

        fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:20]
        t4 = time.perf_counter()

        chunks = db.scalars(select(Chunk).where(Chunk.id.in_(fused_ids))).all()
        chunks_by_id = {c.id: c for c in chunks}
        ordered = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]
        t5 = time.perf_counter()

        rerank(query, ordered, top_k=6)
        t6 = time.perf_counter()

        stage_ms["lexical"].append((t1 - t0) * 1000)
        stage_ms["embed"].append((t2 - t1) * 1000)
        stage_ms["vector"].append((t3 - t2) * 1000)
        stage_ms["fusion"].append((t4 - t3) * 1000)
        stage_ms["hydrate"].append((t5 - t4) * 1000)
        stage_ms["rerank"].append((t6 - t5) * 1000)

        e0 = time.perf_counter()
        retrieve(db, course_id, query)
        e1 = time.perf_counter()
        end_to_end_ms.append((e1 - e0) * 1000)

    def summarize(values: list[float]) -> dict:
        return {
            "mean": round(statistics.mean(values), 1),
            "p50": round(statistics.median(values), 1),
            "p95": round(percentile(values, 95), 1),
            "p99": round(percentile(values, 99), 1),
        }

    return {
        "n_queries": len(queries),
        "stages_ms": {stage: summarize(vals) for stage, vals in stage_ms.items()},
        "end_to_end_ms": summarize(end_to_end_ms),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--course-id", type=int, default=None, help="Pin this to get comparable results run-to-run.")
    parser.add_argument("--n-queries", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0, help="Seeds synthetic-query sampling for reproducibility.")
    parser.add_argument("--output", type=Path, default=Path("bench/results/baseline.json"))
    args = parser.parse_args()

    db = SessionLocal()
    try:
        course_id = pick_course_id(db, args.course_id)
        size = corpus_size(db, course_id)
        queries = sample_queries(db, course_id, args.n_queries, args.seed)
        latency = run_benchmark(db, course_id, queries)
    finally:
        db.close()

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "course_id_benchmarked": course_id,
        "corpus": size,
        "latency": latency,
    }

    print(f"\nGlobal corpus: {size['documents_total']} documents ({size['documents_by_status']}), "
          f"{size['chunks_total']} chunks (all courses)")
    print(f"Benchmarked course {course_id}: {size['benchmarked_course_chunks']} chunks "
          f"(search is course-scoped — this is the actual working set queried below)")
    for label, info in size["disk"].items():
        print(f"  {label}: {info['human']}")

    print(f"\nLatency (course_id={course_id}, n={latency['n_queries']} synthetic queries, seed={args.seed}):")
    print(f"{'stage':<10} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8}  (ms)")
    for stage in STAGES:
        s = latency["stages_ms"][stage]
        print(f"{stage:<10} {s['mean']:>8} {s['p50']:>8} {s['p95']:>8} {s['p99']:>8}")
    e = latency["end_to_end_ms"]
    print(f"{'end-to-end':<10} {e['mean']:>8} {e['p50']:>8} {e['p95']:>8} {e['p99']:>8}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
