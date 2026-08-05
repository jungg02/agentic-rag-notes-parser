# Retrieval System Upgrade — Build Plan

## How to use this document

Drop this file in the repo root. Start a Claude Code session with:

> Read `RETRIEVAL_UPGRADE_PLAN.md`. Complete Phase 0 only, then stop and report back.

Run **one phase per session**. Do not let phases run together — each has a stop
condition for a reason. After each phase, review the diff yourself before moving on.

---

## Context for the agent

This is an existing, working hybrid-retrieval RAG system over study notes. Current stack:

- **Backend:** FastAPI
- **Storage:** PostgreSQL with pgvector (HNSW index)
- **Retrieval:** Postgres full-text search (lexical) + pgvector (dense), fused via
  Reciprocal Rank Fusion, then re-ranked with a local cross-encoder
- **Embeddings:** sentence-transformers
- **Frontend:** React / TypeScript
- **Ingestion:** format conversion → OCR fallback for scanned pages → page-bound
  chunking → embedding, with per-stage status tracking and retry-safe failure handling

The goal of this plan is to extend it into a multi-turn, memory-augmented,
multimodal retrieval system. **The existing pipeline works — do not rewrite it.**
Every phase is additive.

## Working agreement

1. **Ask before any refactor that touches more than one existing module.** Additive
   changes need no permission; structural ones do.
2. **The system must be runnable at the end of every phase.** No phase may leave
   `main` broken.
3. **Every new module gets tests.** Not exhaustive — enough to prove the behaviour.
4. **Every design decision listed under "Decisions to surface" must be raised
   explicitly**, with options and a recommendation, before implementing. Do not
   silently pick one.
5. **Write a short ADR** (`docs/adr/NNN-title.md`, ~10 lines) for each surfaced
   decision: what was chosen, what was rejected, why.
6. **Commit per logical unit**, with messages explaining the *why*.
7. **No new heavy dependencies without asking.** Prefer what's already in the stack —
   in particular, memory storage should reuse pgvector, not introduce a new store.

## Non-goals

Do not build: horizontal sharding, distributed query routing, replication, a
microservice split, or auth. Out of scope. Keep this a single-node system that does
sophisticated things, not a simple system spread across machines.

---

# Phase 0 — Audit and baseline

**Goal:** know what exists and establish numbers to improve against.

### Tasks

1. Read the codebase. Produce `docs/ARCHITECTURE.md`: module map, request flow from
   query to answer, where lexical/dense/RRF/re-rank each happen, and the data model.
2. Generate a Mermaid diagram of the ingestion and query paths. Embed it in that doc.
3. Report current state honestly: test coverage, dead code, TODOs, anything fragile.
4. Build `bench/baseline.py` measuring, on the current corpus:
   - corpus size (documents, chunks, index size on disk)
   - retrieval latency p50 / p95 / p99, broken down by stage (lexical, dense, fusion,
     re-rank) — stage breakdown matters more than the total
   - end-to-end query latency
5. Write results to `bench/results/baseline.json` and summarise in the README.
6. Flag any **correctness** concerns in the existing retrieval (e.g. RRF constant
   choice, chunk boundary handling, embedding truncation).

### Acceptance criteria

- [ ] `docs/ARCHITECTURE.md` exists and is accurate
- [ ] Baseline benchmark is reproducible via one command
- [ ] Latency numbers recorded with stage breakdown
- [ ] Honest list of weaknesses in the current system

### Stop condition

Report findings. **Do not start Phase 1.**

> Why this phase exists: without a baseline, every later improvement is unquantified,
> and unquantified improvements are worthless on a resume and indefensible in an
> interview.

---

# Phase 1 — Query understanding and working memory

**Highest-value phase. Closes two gaps at once.** Do not skip or reorder.

**Goal:** the system correctly answers follow-up questions that are meaningless in
isolation.

Target behaviour:

```
Turn 1: "What's the difference between BM25 and dense retrieval?"
Turn 2: "Which one handles typos better?"
         → rewritten to: "Does BM25 or dense retrieval handle typos better?"
         → retrieval runs on the rewritten query
```

### Tasks

1. **Session store.** Conversation turns keyed by session ID, in Postgres. Store the
   raw query, the rewritten query, retrieved chunk IDs, and the answer.
2. **Query understanding module** (`app/query/`), as a pipeline of independent steps:
   - **Intent classification** — at minimum: factual lookup, comparison, summarisation,
     follow-up/clarification. Keep the taxonomy small and defensible.
   - **Coreference and ellipsis resolution** — resolve "it", "that one", "the second
     one", and dropped subjects against session history.
   - **Query rewriting** — emit a standalone query. Preserve the original; never
     discard it.
   - **Query expansion** (optional) — synonyms or acronym expansion for the lexical arm
     only. Measure before keeping.
3. **Route by intent** where it helps: comparisons may warrant retrieving for both
   entities separately and merging.
4. **Compaction.** When a session exceeds a token budget, summarise older turns and
   keep recent ones verbatim. Make the budget configurable.
5. **Log every rewrite** (original → rewritten → intent) for evaluation in Phase 3.
6. **Expose it in the UI:** show the rewritten query when it differs from the input.
   Cheap to build, and it makes the feature legible to anyone looking at the project.

### Decisions to surface

- Rewrite with the local model, or an API call? Latency and cost versus quality.
- Rewrite *always*, or only when a follow-up is detected? What's the false-positive
  cost of rewriting a query that was already standalone?
- Retrieve on the rewritten query only, or fuse results from both original and
  rewritten?
- What gets kept verbatim versus summarised during compaction — and what is acceptable
  to lose?

### Acceptance criteria

- [ ] A 3+ turn conversation with pronoun references retrieves correctly
- [ ] Rewrites are logged and inspectable
- [ ] Compaction triggers at the configured budget and the system stays coherent after it
- [ ] Tests cover: pure follow-up, standalone query mid-session, topic switch mid-session
- [ ] Latency delta versus baseline is measured and recorded

### Stop condition

Report the latency cost of the rewriting step. **Stop.**

---

# Phase 2 — Semantic memory

**Goal:** durable, cross-session knowledge about the user, retrieved at query time.

Reuse the existing pgvector infrastructure. Memory retrieval *is* retrieval — that
symmetry is the point, and it's worth being able to articulate.

### Tasks

1. **Schema** (`memories` table): content, embedding, type, source session, created/
   updated timestamps, confidence, access count, last accessed.
2. **Extraction.** After a session (or on a signal), extract durable facts: topics
   studied, concepts the user struggled with, stated preferences, recurring goals.
   Be conservative — over-extraction poisons the store.
3. **Retrieval.** At query time, retrieve relevant memories by embedding similarity and
   inject them into context.
4. **Write policy.** Decide and document: extract every turn, end of session, or on
   explicit signal?
5. **Conflict handling.** New fact contradicts a stored one — supersede, version, or
   keep both with recency weighting? Implement one, document why.
6. **Forgetting.** Decay by recency and access count. Cap total memories. Unbounded
   growth is a design failure, and interviewers ask about it.
7. **Inspection endpoint** — list, search, and delete memories. Needed for debugging
   and for demoing.

### Decisions to surface

- **Context budget arbitration.** Memories and document chunks compete for the same
  context window. Fixed split, relevance-thresholded, or one unified ranking across
  both? **This is the most interesting decision in the entire plan — take it
  seriously.** Implement one, but be able to argue the alternatives.
- Extraction model and prompt: how do you avoid storing transient noise as durable fact?
- Same embedding model for memories and documents, or different? Justify.

### Acceptance criteria

- [ ] Facts persist and are retrieved in a *later* session
- [ ] Contradictions handled per the documented policy
- [ ] Forgetting/decay works, and the store is bounded
- [ ] Memory retrieval adds < 50ms p95
- [ ] ADR written for the context budget decision

### Stop condition

Report and stop.

---

# Phase 3 — Evaluation harness

**Do this before multimodal.** Both target job descriptions mention evaluation
repeatedly. A measured improvement beats an unmeasured feature.

**Goal:** prove Phases 1 and 2 actually improved retrieval.

### Tasks

1. **Test set.** 50–100 queries with known-relevant chunks. Cover: single-turn factual,
   multi-turn with coreference, comparison, topic switch, cross-session (needs memory
   to answer). Build it semi-automatically, then hand-check — a bad test set is worse
   than none.
2. **Metrics:** recall@k, MRR, nDCG@10 for retrieval; latency p50/p95/p99 per stage.
3. **Ablations** — run the same set across configurations:
   - lexical only / dense only / RRF fused / fused + cross-encoder re-rank
   - with and without query rewriting
   - with and without semantic memory
4. **Answer quality:** LLM-as-judge on faithfulness and citation correctness. Calibrate
   against ~20 hand-labelled examples and report the agreement rate — an uncalibrated
   judge is not evidence.
5. **Report:** `bench/results/ablation.md`, table plus a short interpretation. State
   where the system is *weak*, not only where it improved.
6. One command runs the whole suite.

### Acceptance criteria

- [ ] Ablation table with per-configuration numbers
- [ ] Multi-turn improvement quantified (this is the headline number)
- [ ] Cross-session memory improvement quantified
- [ ] Judge agreement rate reported
- [ ] Honest weaknesses section

> The single most useful output of this entire plan is one sentence of the form:
> *"Query rewriting improved recall@5 on multi-turn queries from X to Y."*
> Everything else supports that sentence.

---

# Phase 4 — Multimodal retrieval

**Only worth doing if Phases 1–3 are solid.** Partially closes the multimodal gap.

**Goal:** unified retrieval over text and visual content.

### Tasks

1. **Figure extraction.** Pull figures, diagrams, and charts from documents during
   ingestion. The OCR pipeline already touches page images — extend it rather than
   duplicating it.
2. **Image embeddings.** CLIP or SigLIP. Store in pgvector alongside text embeddings.
3. **Cross-modal retrieval.** Text query → relevant figures. Extend RRF to fuse across
   modalities.
4. **Caption enrichment.** Generate captions for figures and index them lexically too —
   gives figures a presence in the lexical arm, not just the dense one.
5. **Serve figures in results** with page provenance, consistent with existing citation
   grounding.

### Stretch — do this if there's any time at all

6. **Keyframes.** Accept short video input, sample keyframes, embed, aggregate to a
   video-level representation.
   - Sampling strategy: uniform, scene-change detection, or adaptive?
   - Aggregation: mean-pool, max-pool, or attention-weighted?
   - Retrieval granularity: frame-level or video-level?

   This converts "image retrieval" into "video retrieval" for roughly a day of extra
   work, and video is the domain that actually matters for the target role.

7. **Projection head.** Train a small adapter aligning image embeddings to the text
   embedding space on your own data. This is the difference between *using* pretrained
   representations and doing representation learning — the honest claim changes.

### Decisions to surface

- Separate indexes per modality, or one unified index? Trade-offs in fusion and filtering.
- How to compare scores across modalities when their similarity distributions differ.
- Whether a figure without a caption should be retrievable at all.

### Acceptance criteria

- [ ] Text query retrieves relevant figures
- [ ] Cross-modal results fused and ranked sensibly
- [ ] Evaluated with the Phase 3 harness — not just demoed
- [ ] Honest note in the README on what is and isn't demonstrated (static figures ≠
      video understanding, frozen encoder ≠ representation learning)

---

# Phase 5 — Procedural memory (optional)

**Goal:** the system learns which retrieval strategy works for which query type.

Most differentiated, least necessary. Only if Phases 1–4 are done and polished.

### Tasks

1. Log per query: intent, which arm dominated the RRF fusion, whether re-ranking
   changed the top result, downstream quality signal.
2. Learn per-intent fusion weights from the log.
3. Apply learned weights at query time, with a safe fallback to defaults.
4. Evaluate against static weights using the Phase 3 harness. **Report honestly if it
   doesn't help** — a negative result you measured is respectable; an unmeasured claim
   is not.

---

# Final pass — presentation

Once phases are complete:

1. **README rewrite.** Architecture diagram, quickstart, results table, design
   decisions with rationale, honest limitations section.
2. **Rename the project.** "Study Notes Parser" undersells it. Something like
   *Multi-Turn Hybrid Retrieval System with Agentic Memory* describes the same artifact
   accurately.
3. **`docs/DESIGN_DECISIONS.md`** — consolidate the ADRs into one readable document.
4. **Repo hygiene** — no dead code, no commented-out blocks, consistent formatting,
   a working setup path from clone to running.
5. **Demo script** — a scripted sequence showing multi-turn resolution, cross-session
   memory, and cross-modal retrieval.

---

## Note to self (not for the agent)

Claude Code will write most of this. **You have to understand all of it.**

Before considering any phase done, check that you can, unprompted:

- Explain why each design decision went the way it did, and what you rejected
- Whiteboard the data flow from query to answer, including where memory enters
- Defend the context-budget arbitration choice from Phase 2 against its alternatives
- Quote your own latency and recall numbers from memory
- Name the system's weaknesses before anyone else does

Anything you can't do from memory is a liability on the resume, not an asset. If a
phase produces code you don't understand, the fix is to rebuild that part yourself —
not to move on.
