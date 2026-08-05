"""SigLIP image/text embeddings for figure retrieval (Phase 4 of the
retrieval upgrade plan, ADR 011).

Deliberately uses the raw `transformers` AutoModel/AutoProcessor API, not
sentence-transformers' generic wrapper: SigLIP's text tower requires
fixed-length padding (`padding="max_length"`, 64 tokens) rather than the
variable-length truncation sentence-transformers applies by default.
Verified directly during this build -- the sentence-transformers wrapper
produced text/image similarity scores with no discriminative signal at all
(a correct caption and an unrelated one scored within noise of each
other), while the same checkpoint through raw transformers with proper
padding cleanly separated matching from non-matching text (~0.19 cosine
for a true match vs. negative/near-zero for mismatches on a real slide-deck
figure). Also note: use `get_image_features()`/`get_text_features()`'s
`.pooler_output`, not `logits_per_image` -- the logits are scaled by a
learned temperature/bias for SigLIP's own sigmoid classification loss, not
meant for direct cosine-similarity ranking against arbitrary text.
"""
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

SIGLIP_CHECKPOINT = "google/siglip-base-patch16-224"
SIGLIP_EMBEDDING_DIM = 768

_MODEL = None
_PROCESSOR = None


def _model_and_processor():
    global _MODEL, _PROCESSOR
    if _MODEL is None:
        _MODEL = AutoModel.from_pretrained(SIGLIP_CHECKPOINT)
        _MODEL.eval()
        _PROCESSOR = AutoProcessor.from_pretrained(SIGLIP_CHECKPOINT)
    return _MODEL, _PROCESSOR


def _normalize(vectors: torch.Tensor) -> list[list[float]]:
    normalized = vectors / vectors.norm(p=2, dim=-1, keepdim=True)
    return normalized.tolist()


def embed_images(images: list[Image.Image]) -> list[list[float]]:
    if not images:
        return []
    model, processor = _model_and_processor()
    inputs = processor(images=images, return_tensors="pt")
    with torch.no_grad():
        output = model.get_image_features(**inputs)
    return _normalize(output.pooler_output)


def embed_image_query(query: str) -> list[float]:
    """Embeds free text with SigLIP's text tower -- for searching the
    `figures` table (image embeddings), never for searching `chunks`
    (bge-small text embeddings, a separate space entirely -- ADR 012)."""
    model, processor = _model_and_processor()
    inputs = processor(text=[query], padding="max_length", return_tensors="pt")
    with torch.no_grad():
        output = model.get_text_features(**inputs)
    return _normalize(output.pooler_output)[0]
