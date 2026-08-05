# 012 — Separate `figures` table and embedding space, not a unified index

**Chosen:** A new `figures` table with its own SigLIP image-embedding
column, entirely independent of `chunks` (bge-small-en-v1.5 text
embeddings, 384-dim). A text query gets a *second* embedding — via
SigLIP's text tower — specifically for searching `figures`; the existing
chunk-retrieval embedding call and index are untouched.

**Rejected:** One unified index — re-embed chunk text with SigLIP's text
tower too, sharing one table/embedding space with figures for a single
fused search.

**Why:** the chunks pipeline's retrieval quality is the one thing this
whole plan has actually measured (Phase 3's ablation report). SigLIP's
text tower is a general-purpose image-caption-length encoder, not a
long-document semantic-search model like bge-small — using it for chunk
retrieval too would risk quietly regressing numbers already reported as a
result, to buy a conceptually cleaner unified index. Two independent,
narrow-purpose embeddings (bge-small for chunks, SigLIP for figures) cost
one extra query-encoding call per turn and keep both retrieval paths
independently reasoned about and independently regressable.
