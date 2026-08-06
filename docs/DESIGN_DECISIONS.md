# Design decisions

This consolidates the 14 individual ADRs under `docs/adr/` into one
navigable narrative, grouped by the phase that produced them, plus a
cross-cutting section on patterns that show up repeatedly across
otherwise-unrelated decisions. Each ADR file remains in place as the
original, standalone record — this document is the readable synthesis,
not a replacement.

## Part 1 — Query understanding and working memory (Phase 1)

**Goal:** correctly answer follow-up questions that are meaningless in
isolation ("what about the second one?").

**Rewriting uses the existing LLM provider, not a new local model**
([001](adr/001-rewrite-model.md)). Classification and conditional rewrite
go through a single call to whichever `LLMProvider` is already configured
(Anthropic or OpenAI-compatible). Rejected: a dedicated local instruction
model (a new heavy dependency for a task the existing LLM already
handles) and rule-based heuristics (real coreference — "which one," "the
second one" — doesn't reduce to pattern matching).

**Rewrite only when the query is detected as context-dependent**
([002](adr/002-when-to-rewrite.md)). One call both classifies intent
*and* decides `needs_rewrite`; the rewrite step only fires when needed,
not on every turn. Rejected: always rewriting, which would double the
LLM cost for queries that were already standalone and risks silently
drifting a fine query into something else.

**Retrieve on the rewritten query only** ([003](adr/003-retrieval-fusion.md)).
No dual-query fusion of original + rewritten text. Rejected as
unnecessary complexity without Phase 3's eval harness yet in place to
prove a single-query approach was actually a problem — `reciprocal_rank_fusion`
already accepts an arbitrary number of ranked lists, so fusing a second
query in later is a small, isolated change if evidence ever calls for it.

**Compaction keeps the last N turns verbatim, summarizes the rest**
([004](adr/004-compaction-policy.md)). A turn-count cutoff, not a
token-count one — truncating mid-sentence at a token boundary would hurt
coreference resolution in the very next turn more than losing exact
phrasing of an old one. Acceptable to lose: exact wording of old turns.
Must be preserved: topics and key facts, so follow-up detection still
works across a summarized boundary.

## Part 2 — Semantic memory (Phase 2)

**Goal:** durable, cross-session knowledge about the student, retrieved
at query time.

**Context budget: relevance-thresholded memories, capped, non-citable**
([005](adr/005-context-budget.md)). Up to `MEMORY_MAX_PER_QUERY` memories
above a similarity threshold render in a separate, non-citable
`<student_context>` block — never in the numbered `<excerpts>` chunks
use, so the citation contract needed zero changes. Rejected: a fixed
memory/chunk slot split (wastes budget when no memory is relevant) and
one unified ranking across memories and chunks — the more principled
option, but memories have no page number to cite, so merging them would
mean extending the citation system to handle a non-document source type.
Bigger lift than this phase needed to prove the mechanism works.

**Extraction: one conservative LLM call with a confidence field**
([006](adr/006-extraction-approach.md)). A single call reviews a
session's messages and emits candidate facts, each self-scored for
confidence; only those above threshold persist. Rejected: regex/rule-based
heuristics — real signal ("I prefer video explanations") is rarely
phrased literally enough to pattern-match.

**Memories share the chunk embedding model** ([007](adr/007-memory-embedding-model.md)).
`BAAI/bge-small-en-v1.5`, already resident for chunks. Rejected: a
second model tuned for short fact-like strings — plausibly a marginal
quality win, unmeasured, for the cost of a second resident model. Keeps
memories and chunks in the same vector space, which is a prerequisite if
a later phase ever revisits 005's rejected unified-ranking option — not
a one-way door either way.

**Extraction runs at end-of-session, detected opportunistically**
([008](adr/008-write-policy.md)). No scheduler exists in this app, so
staleness (30 minutes since the last message) is checked at
`POST .../sessions` — a deliberate user action where a brief pause is at
least explainable — capped at one extraction per call. Originally also
hooked into `GET .../sessions` (page load), but that meant a page load
could hang against a slow LLM endpoint with no user action to explain
the wait; removed after observing exactly that hang against the
configured provider. Known limitation, accepted rather than solved: two
concurrent session-creation requests could both extract the same stale
session, producing a duplicate memory row — rare given the request
pattern, and a duplicate just competes on decay score rather than
corrupting state.

**Conflicts: keep both, resolve through decay at read time**
([009](adr/009-conflict-handling.md)). No contradiction detection at
write time; a newer, more-accessed fact naturally outranks a stale one
via the same decay score used for eviction. Rejected: supersede/version
schemes, which all require detecting that two memories contradict —
typically via embedding similarity, which is unreliable here because
genuinely conflicting facts ("prefers written explanations" vs. "prefers
video explanations") aren't semantic near-duplicates. The honest
limitation — a stale fact can surface until it decays or gets evicted —
was judged easier to name plainly than to paper over with a detector
that doesn't actually work.

## Part 3 — Evaluation harness (Phase 3)

**Goal:** prove Phases 1 and 2 actually improved retrieval, not just
added features.

**Grounded-by-construction test set, cached LLM calls, non-independent
judge** ([010](adr/010-phase3-eval-design.md)). 62 items hand-authored by
reading real chunk text and writing a question whose grounding is exact
by construction, not LLM-generated-then-checked. Live-LLM results
(rewrites, answers, judge verdicts) are collected to a resumable JSONL
cache before any metric runs, specifically because the configured
endpoint was observed taking anywhere from ~1s to 381s per call —
collection needed to survive that without hanging or discarding
legitimate-but-slow results. Known, accepted limitation: the LLM-as-judge
shares a model/provider with the answer generator, so shared blind spots
don't show up as disagreement; a second judge model was rejected as
doubling live-call exposure against an already-unreliable endpoint for a
phase that asked for a calibrated agreement rate, not judge independence.

## Part 4 — Multimodal retrieval (Phase 4)

**Goal:** unified retrieval over text and visual content.

**SigLIP for image/text embeddings** ([011](adr/011-image-embedding-model.md)).
Picked over CLIP on merit (generally better zero-shot accuracy,
comparable size) since nothing in the app had a prior CLIP commitment to
weigh against it. Revised mid-build: the original plan to reuse
`sentence-transformers` (already a dependency) turned out to be broken
for this checkpoint — its generic wrapper produced text/image
similarities with no discriminative signal, traced to SigLIP's text
tower needing fixed-length padding rather than the variable-length
truncation the wrapper applies. Shipped against the raw `transformers`
API instead, adding two small dependencies (`sentencepiece`, `protobuf`)
that the original ADR didn't anticipate — verified directly against real
extracted figures before adopting.

**Separate `figures` table and embedding space, not a unified index**
([012](adr/012-figure-index-design.md)). A second, independent embedding
space (SigLIP) rather than re-embedding chunk text with SigLIP's text
tower for one fused index. Rejected because chunk retrieval quality is
the one thing this whole plan has actually measured (Phase 3's ablation)
— using a general-purpose image-caption-length encoder for long-document
semantic search risked quietly regressing an already-reported number, to
buy a conceptually cleaner index. Confirmed, not just argued: re-running
the Phase 3 ablation after the corpus was re-ingested twice during Phase
4's build still reproduces the same reranked-row numbers exactly.

**Figures always searched, surfaced separately, never score-merged**
([013](adr/013-cross-modal-surfacing.md)). Every turn, figures are
searched independently and attached to the response as a distinct
`related_figures` list — never merged into the citation-numbered
excerpt block or RRF'd against chunk scores. Rejected: intent-gating
figure search behind a classifier call (an extra per-turn LLM round-trip
for a phase meant to be additive, not another latency-risk layer) and
merging chunk and figure similarity scores into one ranked list (a
cross-encoder rerank score and a SigLIP cosine similarity aren't
answering the same question — RRF's rank-based fusion needs the lists
being fused to represent comparable evidence).

**Captionless figures remain retrievable via image embedding alone**
([014](adr/014-captionless-figures.md)). A figure is retrievable the
moment it's extracted and embedded; captions are a best-effort
enrichment that only adds lexical-arm presence when they succeed.
Rejected: requiring a caption before indexing at all — simpler mental
model, but throws away real embeddable content and undercuts the whole
reason to use an image-embedding model. In practice, caption generation
never got a chance to fail or succeed: the configured LLM rejected an
`image_url` content part with a clean `"not a multimodal model"` error,
so it was deferred entirely rather than built against a model that
can't serve it — `figures.caption`/`caption_tsv` are real, queryable
columns, just empty this build.

## Post-phase fixes and tuning

Two changes made after Phase 4 was reported complete, evaluated and
committed with the same rigor as an in-phase decision, but outside the
plan's phase structure:

**`related_figures` persistence.** Originally existed only in the
one-time SSE `"done"` payload, kept in frontend component state.
Switching courses remounted that component and discarded the state, so
every already-rendered message's figures vanished — even though
citations, backed by `message_citations`, survived the same remount.
Fixed with `message_figures`, a join table mirroring `message_citations`'s
role for chunks, populated in the same commit as the assistant message.

**Reranker candidate pool halved.** `FUSED_CANDIDATES` (the number of
fused candidates sent to the cross-encoder reranker) dropped from 20 to
10 after the Phase 0 baseline flagged reranking as ~92% of end-to-end
latency. Re-running the Phase 3 harness confirmed recall@6 held exactly
(82.26%, unchanged in every one of 5 categories) while per-item latency
dropped roughly 60% (~700ms → ~280ms, reproduced across three runs) —
the dropped candidates (fused-list ranks 11-20) essentially never
contained the answer that survived to the top-6 anyway on this eval set.

## Cross-cutting patterns

A few shapes recur across decisions that were made independently, in
different phases, for different subsystems — worth naming once rather
than re-discovering per phase:

- **Watermark/resumable processing**, used for three unrelated things:
  compaction's `summarized_through_message_id` (004), memory extraction's
  `memory_extracted_through_message_id` (008), and Phase 3's resumable
  JSONL collection cache (010). Same shape every time: track how far
  processing has gotten, so a slow/failed step can resume instead of
  re-doing completed work or double-processing.
- **Graceful degradation for enrichment steps**: OCR (pre-dates this
  plan) and figure caption generation (014) both follow the rule that a
  best-effort enrichment failing must never destroy or block otherwise-good
  content. Figure *extraction* itself follows the same rule one level up
  — a failure there can't flip an otherwise-successful document ingestion
  to failed.
- **Additive, never merged into the core ranked list**: semantic memory
  (005) and figure retrieval (013) both attach as a separate response
  surface, searched unconditionally every turn, rather than being fused
  into the same numbered, citable list chunks use. Both times, the
  rejected alternative was a single unified ranking — principled, but
  requiring the citation system to handle a source type that has no page
  number to cite.
- **Decay/ranking instead of explicit detection**: memory conflict
  handling (009) resolves contradictions through the same recency/access
  decay score used for eviction, rather than building a separate
  contradiction detector. Chosen specifically because the detection
  alternative (embedding similarity between facts) is unreliable for the
  same reason in every case it was considered — real contradictions
  often aren't semantic near-duplicates.
- **Reuse the existing LLM call site rather than adding infrastructure**:
  query rewriting (001), memory extraction (006), and Phase 3's
  answer/judge calls all route through the same `LLMProvider` abstraction
  instead of introducing a second model or a new dependency for a
  narrower task. The one case this pattern was tried and had to be
  revised was image embeddings (011), where the existing
  `sentence-transformers` dependency turned out not to support the new
  checkpoint correctly — the fix stayed narrowly scoped to the one module
  that needed it.
