# 003 — Retrieve on the rewritten query only

**Chosen:** When a rewrite happens, `retrieve()` is called with the
rewritten (standalone) query string only. `retrieve()`'s signature and
internals are unchanged from Phase 0.

**Rejected:** Fusing retrieval from both the original and rewritten query
(two full lexical+vector+RRF passes, three-way fused). Phase 0's baseline
shows lexical/vector/fusion/hydrate are cheap (<50ms combined even at p99)
relative to reranking (~530ms mean on a 401-chunk course), so the extra cost
would be affordable — but it's more moving parts than this phase can justify
without Phase 3's eval harness to prove it's needed.

**Why:** Simplest correct change for this phase. If a bad rewrite turns out
to be a real failure mode once Phase 3 exists, `reciprocal_rank_fusion`
already accepts an arbitrary number of ranked lists — fusing a second query
in is a small, isolated change, not a redesign.
