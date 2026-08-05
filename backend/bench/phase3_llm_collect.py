"""Collects every live-LLM result the Phase 3 ablation needs (query
rewrites, generated answers, judge verdicts) into a resumable JSONL cache
(bench/results/phase3_llm_cache.jsonl), one line per (kind, item_id,
condition). phase3_report.py reads the cache and does zero network calls.

Why a cache instead of computing this inline in the report: the configured
LLM endpoint (NVIDIA NIM, see .env) is measurably unreliable -- a single
trivial call was observed taking anywhere from ~1.3s to 381s during this
build. Every call here is a pure function of a fixed test item, so it never
needs to be repeated once it has succeeded. Each call runs with a bounded
per-call timeout (--timeout, default 75s) and max_retries=0 (the SDK's
default of 2 retries would silently multiply the wait up to timeout*3 --
see app/providers/openai_provider.py's docstring), so a stalled call fails
fast instead of hanging the whole run. Failed/missing entries are retried
on the next invocation; successful ones are skipped -- the same
watermark/resumable pattern as compaction (ADR 004) and memory extraction
(ADR 008) elsewhere in this codebase, applied to a batch job instead of a
per-request one. Re-run this script with a longer --timeout to mop up
whatever didn't finish in the first, fast pass.

Three kinds of work, run in three sequential stages (each internally
parallel across --workers threads -- Phase 1's evidence is that stalls are
per-connection, not endpoint-wide, so modest concurrency captures most of
the wall-clock win without hammering a shared endpoint):

  1. rewrite -- understand_query() for every multi_turn_coreference and
     topic_switch item, needed for the query-rewriting ablation.
  2. answer -- a generated answer for every cross_session_memory item under
     both with_memory/without_memory conditions, plus a fixed calibration
     subset (6 single_turn_factual + 6 comparison items, condition
     "default") for the answer-quality judge. Composed directly from
     retrieve() + retrieve_memories() + build_system_prompt() + one
     generate() call -- deliberately skips chat_service.py's own
     understand_query() call, both to isolate memory injection as the only
     variable under test and to halve the call count.
  3. judge -- one LLM-as-judge call per successfully collected answer,
     scoring faithfulness and citation correctness (and, for
     cross_session_memory answers, whether the answer appropriately used
     the seeded student-context fact). Depends on stage 2 having run first.
  3b. personalization baseline -- re-grades each without_memory answer's
      personalization specifically, this time telling the judge (not the
      generator) the student's fact. Stored under condition
      "without_memory_graded_with_fact" rather than overwriting the
      "without_memory" judge row, since it's answering a different
      question ("does this answer happen to satisfy the fact" vs. the
      "without_memory" row's faithfulness/citation grading of the same
      answer on its own terms). See phase3_report.py's memory-ablation
      section for why this baseline matters.

Usage (run inside the backend container so the DB and models are reachable):

    docker compose run --rm backend python bench/phase3_llm_collect.py \\
        --timeout 75 --workers 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.generation.prompts import build_system_prompt, parse_citations
from app.ingestion.embedder import embed_query
from app.memory.retrieval import ScoredMemory, retrieve_memories
from app.models import Course, Memory
from app.providers.base import LLMMessage, LLMProvider
from app.providers.openai_provider import OpenAIProvider
from app.query.understanding import understand_query
from app.retrieval.service import retrieve
from bench.phase3_dataset import DEFAULT_EVAL_FILE, MEMORY_CATEGORY, REWRITE_CATEGORIES, EvalItem, load_items

CACHE_PATH = Path("bench/results/phase3_llm_cache.jsonl")
CALIBRATION_EXTRA_IDS = [f"stf{i:02d}" for i in range(1, 7)] + [f"cmp{i:02d}" for i in range(1, 7)]

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

_JUDGE_SYSTEM_PROMPT = """You are grading one answer from a study-notes RAG assistant. You will be \
given the student's question, the numbered excerpts the assistant was allowed to use, the \
assistant's answer, and (sometimes) a background fact about the student the assistant was told \
to use for tone/approach only, never as a citable source.

Judge two things strictly:
1. faithful: true only if every factual claim in the answer is actually supported by the \
provided excerpts (no invented facts, no outside knowledge).
2. citations_correct: true only if every [n] citation marker in the answer points to an excerpt \
that actually supports the claim it's attached to.

If a background fact about the student is provided, also judge:
3. personalized: true only if the answer's content or framing plausibly reflects that fact where \
relevant (e.g. leads with a code example for a student who prefers code examples). If no \
background fact is provided, personalized must be null.

Respond with ONLY a JSON object and nothing else:
{"faithful": true or false, "citations_correct": true or false, "personalized": true, false, or null}"""


class Cache:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str, str]] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["ok"]:
                    self._seen.add((row["kind"], row["item_id"], row["condition"]))

    def has(self, kind: str, item_id: str, condition: str) -> bool:
        return (kind, item_id, condition) in self._seen

    def append(self, row: dict) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(json.dumps(row) + "\n")
            if row["ok"]:
                self._seen.add((row["kind"], row["item_id"], row["condition"]))

    def load_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        rows = {}
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["kind"], row["item_id"], row["condition"])
            if row["ok"] or key not in rows:  # a later successful row overrides an earlier failure
                rows[key] = row
        return list(rows.values())


def _bounded_provider(timeout_s: float) -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider != "openai":
        raise SystemExit(
            f"Phase 3 collection needs a bounded-timeout provider; LLM_PROVIDER={settings.llm_provider!r} "
            "doesn't support one yet (only the openai-compatible provider does). Switch LLM_PROVIDER=openai "
            "for this script, or extend AnthropicProvider with the same timeout/max_retries knobs first."
        )
    return OpenAIProvider(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=timeout_s,
        max_retries=0,
    )


def _parse_judge(raw_text: str) -> dict | None:
    match = _JSON_OBJECT.search(raw_text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data.get("faithful"), bool) or not isinstance(data.get("citations_correct"), bool):
        return None
    personalized = data.get("personalized")
    if personalized is not None and not isinstance(personalized, bool):
        return None
    return {"faithful": data["faithful"], "citations_correct": data["citations_correct"], "personalized": personalized}


def _run_one(cache: Cache, kind: str, item_id: str, condition: str, fn) -> None:
    if cache.has(kind, item_id, condition):
        return
    t0 = time.perf_counter()
    try:
        data = fn()
        cache.append(
            {
                "kind": kind, "item_id": item_id, "condition": condition, "ok": True,
                "elapsed_s": round(time.perf_counter() - t0, 2), "data": data, "error": None,
            }
        )
    except Exception as exc:  # noqa: BLE001 - one failed item must not stop the batch; retried next run
        cache.append(
            {
                "kind": kind, "item_id": item_id, "condition": condition, "ok": False,
                "elapsed_s": round(time.perf_counter() - t0, 2), "data": None, "error": f"{type(exc).__name__}: {exc}",
            }
        )


def _collect_rewrite(provider: LLMProvider, item: EvalItem) -> dict:
    history = [LLMMessage(role="user", content=t) for t in item.history]
    understanding = understand_query(provider, history, item.query)
    return {"intent": understanding.intent, "rewritten_query": understanding.rewritten_query}


def _seed_throwaway_memory(db: Session, course_id: int, item: EvalItem) -> int:
    vector = embed_query(item.memory.content)
    memory = Memory(
        course_id=course_id, content=item.memory.content, embedding=vector,
        memory_type=item.memory.memory_type, confidence=0.9,
    )
    db.add(memory)
    db.commit()
    return memory.id


def _collect_answer(
    db: Session, course_id: int, course_name: str, provider: LLMProvider, item: EvalItem, with_memory: bool
) -> dict:
    chunks = retrieve(db, course_id, item.query, top_k=6)

    scored_memories: list[ScoredMemory] | None = None
    seeded_memory_id: int | None = None
    if with_memory:
        seeded_memory_id = _seed_throwaway_memory(db, course_id, item)
        try:
            scored_memories = retrieve_memories(db, course_id, embed_query(item.query), bump_access=False)
        finally:
            db.execute(delete(Memory).where(Memory.id == seeded_memory_id))
            db.commit()

    system_prompt, marker_map = build_system_prompt(course_name, chunks, scored_memories)
    response = provider.generate([LLMMessage(role="user", content=item.query)], system=system_prompt, max_tokens=1500)
    used_markers = parse_citations(response.text, marker_map)

    return {
        "query": item.query,
        "answer_text": response.text,
        "chunks_shown": [
            {"marker": i, "document": sc.chunk.document.original_filename, "page": sc.chunk.page_number, "text": sc.chunk.text}
            for i, sc in enumerate(chunks, start=1)
        ],
        "citations_used": used_markers,
        "memory_injected": bool(scored_memories),
        "memory_content": item.memory.content if with_memory and item.memory else None,
    }


def _answer_job(course_id: int, course_name: str, provider: LLMProvider, item: EvalItem, with_memory: bool) -> dict:
    """Owns its own Session for the lifetime of one job -- Sessions aren't
    thread-safe, so each ThreadPoolExecutor worker needs its own, opened and
    closed within the job rather than shared or leaked across submissions."""
    db = SessionLocal()
    try:
        return _collect_answer(db, course_id, course_name, provider, item, with_memory)
    finally:
        db.close()


def _collect_judge(provider: LLMProvider, answer_row: dict, memory_content_override: str | None = None) -> dict:
    """memory_content_override lets the judge be told a background fact the
    *generator* never saw (see _collect_personalization_baseline below) --
    without it, the judge only sees whatever fact (if any) the answer's own
    generation actually had access to."""
    data = answer_row["data"]
    memory_content = memory_content_override if memory_content_override is not None else data.get("memory_content")
    excerpts = "\n\n".join(f"[{c['marker']}] (page {c['page']})\n{c['text']}" for c in data["chunks_shown"])
    prompt = f"Question: {data['query']}\n\nExcerpts:\n{excerpts}\n\nAssistant's answer:\n{data['answer_text']}"
    if memory_content:
        prompt += f"\n\nBackground fact about the student: {memory_content}"

    response = provider.generate([LLMMessage(role="user", content=prompt)], system=_JUDGE_SYSTEM_PROMPT, max_tokens=500)
    parsed = _parse_judge(response.text)
    if parsed is None:
        raise ValueError(f"Judge response wasn't parseable JSON: {response.text!r}")
    return parsed


def _collect_personalization_baseline(provider: LLMProvider, answer_row: dict, memory_content: str) -> dict:
    """Grades a without_memory answer for personalization by telling only
    the *judge* (not the generator) the student's known fact -- gives a real
    measured baseline ("does a memory-blind answer accidentally satisfy this
    fact anyway?") instead of a definitional 0%, which is what you'd get by
    just never asking the question (see docs/adr/010's note on this)."""
    return _collect_judge(provider, answer_row, memory_content_override=memory_content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--timeout", type=float, default=75.0)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    course_id, items = load_items(args.eval_file)
    items_by_id = {i.id: i for i in items}
    cache = Cache(args.cache)
    provider = _bounded_provider(args.timeout)

    db = SessionLocal()
    course_name = db.get(Course, course_id).name

    # Stage 1: rewrites.
    rewrite_items = [i for i in items if i.category in REWRITE_CATEGORIES]
    print(f"\n[1/3] rewrites: {len(rewrite_items)} items ({sum(1 for i in rewrite_items if cache.has('rewrite', i.id, 'default'))} cached)")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(_run_one, cache, "rewrite", item.id, "default", lambda item=item: _collect_rewrite(provider, item))
            for item in rewrite_items
        ]
        for f in as_completed(futures):
            f.result()

    # Stage 2: answers -- memory-ablation items (both conditions) + fixed calibration subset.
    # with_memory jobs seed a real (throwaway) Memory row into the shared
    # course before retrieve_memories() runs and delete it right after --
    # running two of those concurrently risks one job's retrieval picking up
    # another's still-live seeded memory (retrieve_memories has no per-job
    # scoping, only course_id). without_memory/calibration jobs never touch
    # the memories table, so those stay fully parallel; with_memory jobs run
    # one at a time (max_workers=1) to make that race impossible rather than
    # just unlikely.
    memory_items = [i for i in items if i.category == MEMORY_CATEGORY]
    calibration_items = [items_by_id[cid] for cid in CALIBRATION_EXTRA_IDS if cid in items_by_id]
    with_memory_jobs = [(item.id, "with_memory", item, True) for item in memory_items]
    parallel_jobs = [(item.id, "without_memory", item, False) for item in memory_items]
    parallel_jobs += [(item.id, "default", item, False) for item in calibration_items]

    n_cached = sum(1 for iid, cond, _, _ in with_memory_jobs + parallel_jobs if cache.has("answer", iid, cond))
    print(f"\n[2/3] answers: {len(with_memory_jobs) + len(parallel_jobs)} jobs ({n_cached} cached)")

    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = [
            pool.submit(
                _run_one, cache, "answer", item_id, condition,
                lambda item=item, with_memory=with_memory: _answer_job(course_id, course_name, provider, item, with_memory),
            )
            for item_id, condition, item, with_memory in with_memory_jobs
        ]
        for f in as_completed(futures):
            f.result()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                _run_one, cache, "answer", item_id, condition,
                lambda item=item, with_memory=with_memory: _answer_job(course_id, course_name, provider, item, with_memory),
            )
            for item_id, condition, item, with_memory in parallel_jobs
        ]
        for f in as_completed(futures):
            f.result()

    # Stage 3: judge -- one call per successfully collected answer.
    all_rows = {(r["kind"], r["item_id"], r["condition"]): r for r in cache.load_all()}
    answer_rows = [r for (kind, _, _), r in all_rows.items() if kind == "answer" and r["ok"]]
    print(f"\n[3/3] judgments: {len(answer_rows)} answers ({sum(1 for r in answer_rows if cache.has('judge', r['item_id'], r['condition']))} cached)")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(_run_one, cache, "judge", row["item_id"], row["condition"], lambda row=row: _collect_judge(provider, row))
            for row in answer_rows
        ]
        for f in as_completed(futures):
            f.result()

    # Stage 3b: personalization baseline -- grade each without_memory answer
    # for personalization too, telling only the judge (not the generator)
    # the student's fact. Without this, "0%" for the without-memory arm is a
    # definitional artifact of never asking the question, not a measured
    # baseline -- a memory-blind answer can still accidentally satisfy a
    # preference (e.g. it happens to include a code example anyway).
    without_memory_answers = [
        r for (kind, iid, cond), r in all_rows.items()
        if kind == "answer" and cond == "without_memory" and r["ok"]
    ]
    print(f"\n[3b/3] personalization baseline: {len(without_memory_answers)} answers "
          f"({sum(1 for r in without_memory_answers if cache.has('judge', r['item_id'], 'without_memory_graded_with_fact'))} cached)")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                _run_one, cache, "judge", row["item_id"], "without_memory_graded_with_fact",
                lambda row=row: _collect_personalization_baseline(provider, row, items_by_id[row["item_id"]].memory.content),
            )
            for row in without_memory_answers
        ]
        for f in as_completed(futures):
            f.result()

    db.close()

    final_rows = cache.load_all()
    ok = sum(1 for r in final_rows if r["ok"])
    failed = sum(1 for r in final_rows if not r["ok"])
    print(f"\nCache now has {len(final_rows)} entries: {ok} ok, {failed} failed (re-run to retry failures).")


if __name__ == "__main__":
    main()
