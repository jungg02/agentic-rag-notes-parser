from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.embedder import embed_query
from app.models import Chunk
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import ScoredChunk, rerank
from app.retrieval.vector import search_vector

FUSED_CANDIDATES = 20
FINAL_TOP_K = 6


def retrieve(db: Session, course_id: int, query: str, top_k: int = FINAL_TOP_K) -> list[ScoredChunk]:
    lexical_ids = search_lexical(db, course_id, query, limit=50)
    query_embedding = embed_query(query)
    vector_ids = search_vector(db, course_id, query_embedding, limit=50)

    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:FUSED_CANDIDATES]
    if not fused_ids:
        return []

    chunks = db.scalars(select(Chunk).where(Chunk.id.in_(fused_ids))).all()
    chunks_by_id = {c.id: c for c in chunks}
    ordered_candidates = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]

    return rerank(query, ordered_candidates, top_k=top_k)
