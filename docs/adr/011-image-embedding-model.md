# 011 — SigLIP for image/text embeddings

**Chosen:** SigLIP base (via `sentence-transformers`, already a dependency
— no new pip package, just a new model checkpoint). Both the image tower
(embedding extracted figures) and text tower (embedding a user's query for
cross-modal search) come from the same checkpoint.

**Rejected:** CLIP ViT-B/32. More prior art and slightly smaller, but
SigLIP generally reports better zero-shot accuracy at a comparable size,
and this app has no legacy CLIP dependency to stay compatible with — no
switching cost to weigh against the accuracy difference.

**Why:** with no existing commitment either way, pick on merit. Model
choice is isolated to `app/ingestion/image_embedder.py` and the new
`figures.embedding` column's dimensionality — swapping it later, if
SigLIP underperforms in practice, touches one module and one migration,
not the rest of the retrieval pipeline.
