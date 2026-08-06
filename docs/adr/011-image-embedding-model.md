# 011 — SigLIP for image/text embeddings

**Chosen:** SigLIP base (`google/siglip-base-patch16-224`). Both the image
tower (embedding extracted figures) and text tower (embedding a user's
query for cross-modal search) come from the same checkpoint.

**Implementation note (revised from the original plan):** initially
intended to reuse `sentence-transformers`, already a dependency, with no
new pip package. That wrapper turned out to be broken for this checkpoint
— see `app/ingestion/image_embedder.py`'s module docstring: it produced
text/image similarity scores with no discriminative signal at all,
traced to SigLIP's text tower needing fixed-length padding
(`padding="max_length"`) rather than the variable-length truncation the
generic wrapper applies. Shipped against the raw `transformers`
`AutoModel`/`AutoProcessor` API instead, which did add two small new
dependencies (`sentencepiece`, `protobuf`) for the tokenizer. Verified
directly against real corpus figures before adopting, not assumed from
documentation.

**Rejected:** CLIP ViT-B/32. More prior art and slightly smaller, but
SigLIP generally reports better zero-shot accuracy at a comparable size,
and this app has no legacy CLIP dependency to stay compatible with — no
switching cost to weigh against the accuracy difference.

**Why:** with no existing commitment either way, pick on merit. Model
choice is isolated to `app/ingestion/image_embedder.py` and the new
`figures.embedding` column's dimensionality — swapping it later, if
SigLIP underperforms in practice, touches one module and one migration,
not the rest of the retrieval pipeline.
