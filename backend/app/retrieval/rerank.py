from dataclasses import dataclass

from sentence_transformers import CrossEncoder

from app.models import Chunk

_MODEL = None


def _model() -> CrossEncoder:
    global _MODEL
    if _MODEL is None:
        _MODEL = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
    return _MODEL


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


def rerank(query: str, candidates: list[Chunk], top_k: int) -> list[ScoredChunk]:
    if not candidates:
        return []

    pairs = [(query, f"{c.context_header}\n{c.text}" if c.context_header else c.text) for c in candidates]
    raw_scores = _model().predict(pairs)

    scored = [ScoredChunk(chunk=c, score=float(s)) for c, s in zip(candidates, raw_scores)]
    scored.sort(key=lambda sc: -sc.score)
    return scored[:top_k]
