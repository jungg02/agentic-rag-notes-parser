# 010 — Phase 3 eval harness: grounded-by-construction test set, cached LLM calls, non-independent judge

Not a "Decisions to surface" item (Phase 3 has no such section in the plan)
but consequential enough to record anyway.

**Chosen (test set):** 62 items across 5 categories, hand-authored by
reading real chunk text from course_id=4 (DSA2101) and writing a question
whose `expected` (document, page) grounding is exact by construction —
never LLM-generated-then-checked. Removes one whole axis of dependency on
the flaky configured provider for this step, and grounding is provably
correct rather than trusted.

**Rejected:** LLM-generated candidate questions with a human review pass.
Faster to produce more items, but grounding would be *probably* correct
rather than *exactly* correct, and it adds live-LLM-call exposure to a
step that doesn't need it.

**Chosen (collection architecture):** `phase3_llm_collect.py` writes every
live-LLM result (rewrites, generated answers, judge verdicts) to a
resumable JSONL cache, keyed by (kind, item_id, condition), before any
metric is computed. Ablation/report scripts read the cache and make zero
network calls. Each call runs with a bounded per-call timeout and
`max_retries=0` (added to `OpenAIProvider`, additive) so a stalled call
fails in `timeout` seconds, not `timeout × 3` from the SDK's default
retry behavior.

**Why:** the configured endpoint (NVIDIA NIM) was observed taking
anywhere from ~1s to 381s for a single trivial call during this build.
Collection needed to survive that without either hanging the whole run or
silently discarding legitimate-but-slow results. Separating collection
from computation means flakiness costs once, re-runs are instant, and a
kill mid-flight keeps completed work — the same watermark/resumable
pattern this codebase already uses for compaction (ADR 004) and memory
extraction (ADR 008), applied to a batch job.

**Chosen (concurrency safety):** `with_memory` answer-generation jobs run
serialized (`max_workers=1`); everything else runs at `--workers` (default
5). `with_memory` jobs seed a real, course-scoped `Memory` row and delete
it around one `retrieve_memories()` call — running two concurrently risked
one job's retrieval picking up another's still-live seeded memory, since
`retrieve_memories` has no per-job scoping, only `course_id`.

**Known limitation (judge independence):** the LLM-as-judge uses the same
model/provider that generated the answers being judged. Shared blind
spots between generator and judge don't show up as disagreement, so the
calibration agreement rate (bench/results/ablation.md §4) bounds this
concern but doesn't eliminate it. Using a second, different model as
judge was considered and rejected for this phase — it would double the
live-call count against an already-unreliable endpoint, and the plan
asks for a calibrated agreement rate, not judge independence specifically.
