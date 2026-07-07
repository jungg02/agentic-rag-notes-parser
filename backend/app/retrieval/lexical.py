from sqlalchemy import text
from sqlalchemy.orm import Session


def search_lexical(db: Session, course_id: int, query: str, limit: int = 50) -> list[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM chunks, websearch_to_tsquery('english', :query) q
            WHERE course_id = :course_id AND tsv @@ q
            ORDER BY ts_rank_cd(tsv, q) DESC
            LIMIT :limit
            """
        ),
        {"query": query, "course_id": course_id, "limit": limit},
    ).all()
    return [row[0] for row in rows]
