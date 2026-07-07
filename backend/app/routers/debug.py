from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.embedder import embed_query
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.lexical import search_lexical
from app.retrieval.rerank import rerank
from app.models import Chunk
from sqlalchemy import select

router = APIRouter(prefix="/api/courses", tags=["debug"])


@router.get("/{course_id}/search")
def debug_search(course_id: int, q: str, db: Session = Depends(get_db)):
    from app.retrieval.vector import search_vector

    lexical_ids = search_lexical(db, course_id, q, limit=50)
    vector_ids = search_vector(db, course_id, embed_query(q), limit=50)
    fused_ids = reciprocal_rank_fusion([lexical_ids, vector_ids])[:20]

    chunks = db.scalars(select(Chunk).where(Chunk.id.in_(fused_ids))).all()
    chunks_by_id = {c.id: c for c in chunks}
    ordered = [chunks_by_id[cid] for cid in fused_ids if cid in chunks_by_id]
    reranked = rerank(q, ordered, top_k=6)

    return {
        "lexical_rank": lexical_ids,
        "vector_rank": vector_ids,
        "fused_rank": fused_ids,
        "reranked": [{"chunk_id": sc.chunk.id, "score": sc.score, "text": sc.chunk.text} for sc in reranked],
    }
