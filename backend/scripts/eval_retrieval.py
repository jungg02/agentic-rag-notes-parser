"""Retrieval-quality and latency evaluation for the hybrid RAG pipeline.

Compares four retrieval modes on a hand-labeled question set — lexical-only,
vector-only, RRF-fused (no rerank), and the full production path
(fused + cross-encoder rerank, i.e. `retrieval.service.retrieve`) — and
reports Recall@k / MRR@k for each, plus end-to-end query latency for the
production path.

This exists to put a number on the design choice of hybrid fusion + rerank
over a single-leg baseline, and is not part of the app itself (see
scripts/make_fixtures.py for a similar one-off tool).

Eval file format (see scripts/eval/example_questions.json):

    {
      "course_id": 1,
      "questions": [
        {
          "question": "What is the time complexity of quicksort's average case?",
          "expected_document": "Lecture4_Sorting.pdf",
          "expected_pages": [12, 13]
        }
      ]
    }

A question counts as a hit at a given mode if any chunk in that mode's
top-k results came from `expected_document` on one of `expected_pages`.
`course_id` can also be set per-question to mix courses in one eval file.

Usage (run inside the backend container so the DB and models are reachable):

    docker compose run --rm backend python scripts/eval_retrieval.py \\
        --eval-file scripts/eval/example_questions.json

Options:
    --top-k N        Cutoff for Recall@k / MRR@k and for the reranked
                      result size (default: 6, matching FINAL_TOP_K).
    --course-id N     Default course_id for questions that omit it.
    --output PATH     Write full results (including per-question detail) to
                      a JSON file in addition to the printed summary.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingestion.embedder import embed_query
from app.models import Chunk
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.service import retrieve
from app.retrieval.vector import search_vector

MODES = ["lexical", "vector", "fused", "reranked"]


@dataclass
class EvalQuestion:
    question: str
    course_id: int
    expected: set[tuple[str, int]]  # (document filename, page_number)


@dataclass
class ModeResult:
    hit_ranks: list[int | None] = field(default_factory=list)  # 1-indexed rank of first hit, None = miss

    def recall(self) -> float:
        return sum(1 for r in self.hit_ranks if r is not None) / len(self.hit_ranks)

    def mrr(self) -> float:
        return sum(1.0 / r if r is not None else 0.0 for r in self.hit_ranks) / len(self.hit_ranks)


def load_eval_set(path: Path, default_course_id: int | None) -> list[EvalQuestion]:
    raw = json.loads(path.read_text())
    items = raw["questions"] if isinstance(raw, dict) else raw
    file_course_id = raw.get("course_id") if isinstance(raw, dict) else None

    questions = []
    for item in items:
        course_id = item.get("course_id", file_course_id if file_course_id is not None else default_course_id)
        if course_id is None:
            raise ValueError(f"No course_id for question (set --course-id, or a course_id in the file): {item['question']!r}")
        expected = {(item["expected_document"], page) for page in item["expected_pages"]}
        questions.append(EvalQuestion(question=item["question"], course_id=course_id, expected=expected))
    return questions


def chunk_key(chunk: Chunk) -> tuple[str, int]:
    return (chunk.document.original_filename, chunk.page_number)


def first_hit_rank(ranked_chunks: list[Chunk], expected: set[tuple[str, int]]) -> int | None:
    for rank, chunk in enumerate(ranked_chunks, start=1):
        if chunk_key(chunk) in expected:
            return rank
    return None


def fetch_chunks(db: Session, chunk_ids: list[int]) -> dict[int, Chunk]:
    if not chunk_ids:
        return {}
    rows = db.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids))).all()
    return {c.id: c for c in rows}


def evaluate(db: Session, questions: list[EvalQuestion], top_k: int) -> tuple[dict[str, ModeResult], list[float], list[dict]]:
    results = {mode: ModeResult() for mode in MODES}
    latencies_ms: list[float] = []
    per_question: list[dict] = []

    # First call lazy-loads the embedding + cross-encoder models (multi-second
    # one-time cost) — warm up once, untimed, so reported latency reflects
    # steady-state query cost, not cold start.
    retrieve(db, questions[0].course_id, questions[0].question, top_k=top_k)

    for q in questions:
        start = time.perf_counter()
        reranked = retrieve(db, q.course_id, q.question, top_k=top_k)
        latencies_ms.append((time.perf_counter() - start) * 1000)

        lexical_ids = search_lexical(db, q.course_id, q.question, limit=50)
        query_embedding = embed_query(q.question)
        vector_ids = search_vector(db, q.course_id, query_embedding, limit=50)
        fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])

        needed_ids = list(set(lexical_ids[:top_k]) | set(vector_ids[:top_k]) | set(fused_ids[:top_k]))
        chunks_by_id = fetch_chunks(db, needed_ids)

        mode_chunks = {
            "lexical": [chunks_by_id[cid] for cid in lexical_ids[:top_k] if cid in chunks_by_id],
            "vector": [chunks_by_id[cid] for cid in vector_ids[:top_k] if cid in chunks_by_id],
            "fused": [chunks_by_id[cid] for cid in fused_ids[:top_k] if cid in chunks_by_id],
            "reranked": [sc.chunk for sc in reranked],
        }

        question_detail = {"question": q.question, "course_id": q.course_id, "ranks": {}}
        for mode, chunks in mode_chunks.items():
            rank = first_hit_rank(chunks, q.expected)
            results[mode].hit_ranks.append(rank)
            question_detail["ranks"][mode] = rank
        per_question.append(question_detail)

    return results, latencies_ms, per_question


def percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def print_report(results: dict[str, ModeResult], latencies_ms: list[float], top_k: int, n: int) -> None:
    print(f"\nRetrieval eval — {n} questions, top_k={top_k}\n")
    print(f"{'mode':<10} {'recall@k':>10} {'mrr@k':>10}")
    for mode in MODES:
        r = results[mode]
        print(f"{mode:<10} {r.recall():>10.2%} {r.mrr():>10.3f}")

    print(f"\nEnd-to-end query latency (retrieve(), n={len(latencies_ms)}):")
    print(f"  mean: {statistics.mean(latencies_ms):.0f}ms")
    print(f"  p50:  {statistics.median(latencies_ms):.0f}ms")
    print(f"  p95:  {percentile(latencies_ms, 95):.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--course-id", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    questions = load_eval_set(args.eval_file, args.course_id)
    if not questions:
        raise SystemExit("Eval file has no questions.")

    db = SessionLocal()
    try:
        results, latencies_ms, per_question = evaluate(db, questions, args.top_k)
    finally:
        db.close()

    print_report(results, latencies_ms, args.top_k, len(questions))

    if args.output:
        summary = {
            "top_k": args.top_k,
            "n_questions": len(questions),
            "modes": {mode: {"recall": r.recall(), "mrr": r.mrr()} for mode, r in results.items()},
            "latency_ms": {
                "mean": statistics.mean(latencies_ms),
                "p50": statistics.median(latencies_ms),
                "p95": percentile(latencies_ms, 95),
            },
            "per_question": per_question,
        }
        args.output.write_text(json.dumps(summary, indent=2))
        print(f"\nWrote full results to {args.output}")


if __name__ == "__main__":
    main()
