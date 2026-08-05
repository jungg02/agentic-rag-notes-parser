# 013 — Figures always searched, surfaced as a separate list, never score-merged with chunks

**Chosen:** Every turn, alongside chunk retrieval, `chat_service.py` also
searches `figures` independently — own similarity threshold, own top-k,
own RRF-within-the-figures-arm (SigLIP image-embedding rank fused with
caption lexical-search rank, mirroring how chunks already fuse a lexical
and a dense arm). Results attach to the response as a distinct
`related_figures` list, never merged into the citation-numbered
`<excerpts>` block or RRF'd against chunk scores.

**Rejected:**
- Intent-gating figure search behind a classification step (an extra LLM
  call deciding "does this query seem visual"). Same shape of complexity
  Phase 1 explicitly deferred (query-expansion, intent-based routing) —
  not worth a new per-turn LLM call for a phase that's supposed to be
  "only worth doing if Phases 1-3 are solid," i.e. additive, not another
  layer of latency risk on the validated core.
- Merging chunk-similarity and figure-similarity scores into one RRF pass.
  Chunk scores come from a cross-encoder reranker operating on bge-small
  candidates; figure scores come from SigLIP cosine similarity. Nothing
  ties those two numeric scales together — RRF's rank-based fusion needs
  the *lists being fused* to be answering the same question ("how well
  does this item match the query"), and "this chunk is the 3rd-best text
  match" and "this figure is the 3rd-best image match" aren't
  interchangeable evidence for one ranked answer.

**Why:** matches the existing pattern for genuinely separate-but-parallel
retrieval established by memory in Phase 2 (also always-run, also a
separate `<student_context>` block, also never merged into `<excerpts>`)
— proven additive, low-latency-risk, and doesn't ask the generation model
to reconcile two incomparable scoring systems into one ranked list.
