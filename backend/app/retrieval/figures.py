"""Figure retrieval (Phase 4 of the retrieval upgrade plan, ADR 013).

Two arms fused with the same reciprocal_rank_fusion() already used for
chunks -- but fused *within* the figures arm only, never against chunk
scores (ADR 013's core point: SigLIP cosine similarity and the
cross-encoder's chunk rerank score aren't comparable numbers):

  - dense: SigLIP image-embedding cosine similarity, thresholded (see
    FIGURE_SIMILARITY_THRESHOLD's calibration note below) since pgvector
    always returns *something* for any query regardless of relevance.
  - lexical: full-text search over `figures.caption_tsv`. Currently always
    empty in this build -- caption generation was evaluated and deferred
    (ADR 014's addendum) -- but the fusion degrades gracefully exactly the
    way chunk lexical search does for a chunk with zero keyword matches:
    ranking falls back to the dense arm alone, nothing errors.
"""
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Figure
from app.retrieval.fusion import reciprocal_rank_fusion

# Calibrated by spot-checking real top-1 results against this app's own
# ingested corpus, not a large-scale study: SigLIP's cosine similarities
# for genuinely correct matches on real course-slide queries landed
# ~0.10-0.19 (e.g. an Excel screenshot scored 0.121 for "how do I read
# data from an excel file", and a dplyr/SQL comparison diagram scored
# 0.0997 for a query about summarize()); clearly mismatched pairs scored
# negative-to-near-zero. 0.08 sits below every verified true positive
# observed and above the noise floor -- but this is a narrow-domain,
# small-corpus calibration (all figures in the same illustration style
# from one course), not validated at scale. See the Phase 4 honest
# limitations note.
FIGURE_SIMILARITY_THRESHOLD = 0.08
FIGURE_MAX_PER_QUERY = 3
_CANDIDATE_POOL_SIZE = 20


@dataclass
class ScoredFigure:
    figure: Figure
    similarity: float | None  # None when only the lexical arm matched it


def _dense_candidates(db: Session, course_id: int, query_embedding: list[float]) -> list[tuple[int, float]]:
    rows = db.execute(
        text(
            """
            SELECT id, 1 - (embedding <=> (:query_embedding)::vector) AS similarity
            FROM figures
            WHERE course_id = :course_id
            ORDER BY embedding <=> (:query_embedding)::vector
            LIMIT :pool_size
            """
        ),
        {"course_id": course_id, "query_embedding": str(query_embedding), "pool_size": _CANDIDATE_POOL_SIZE},
    ).all()
    return [(row[0], row[1]) for row in rows if row[1] >= FIGURE_SIMILARITY_THRESHOLD]


def _lexical_candidates(db: Session, course_id: int, caption_query: str) -> list[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM figures, websearch_to_tsquery('english', :query) q
            WHERE course_id = :course_id AND caption_tsv @@ q
            ORDER BY ts_rank_cd(caption_tsv, q) DESC
            LIMIT :pool_size
            """
        ),
        {"query": caption_query, "course_id": course_id, "pool_size": _CANDIDATE_POOL_SIZE},
    ).all()
    return [row[0] for row in rows]


def retrieve_figures(
    db: Session, course_id: int, query_text: str, query_embedding: list[float], limit: int = FIGURE_MAX_PER_QUERY
) -> list[ScoredFigure]:
    dense = _dense_candidates(db, course_id, query_embedding)
    lexical_ids = _lexical_candidates(db, course_id, query_text)
    if not dense and not lexical_ids:
        return []

    similarity_by_id = dict(dense)
    fused_ids = reciprocal_rank_fusion([[fid for fid, _ in dense], lexical_ids])[:limit]
    if not fused_ids:
        return []

    figures_by_id = {f.id: f for f in db.scalars(select(Figure).where(Figure.id.in_(fused_ids))).all()}
    return [
        ScoredFigure(figure=figures_by_id[fid], similarity=similarity_by_id.get(fid))
        for fid in fused_ids
        if fid in figures_by_id
    ]
