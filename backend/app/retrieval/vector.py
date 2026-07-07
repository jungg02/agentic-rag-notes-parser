from sqlalchemy import text
from sqlalchemy.orm import Session


def search_vector(db: Session, course_id: int, query_embedding: list[float], limit: int = 50) -> list[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM chunks
            WHERE course_id = :course_id
            ORDER BY embedding <=> (:query_embedding)::vector
            LIMIT :limit
            """
        ),
        {"course_id": course_id, "query_embedding": str(query_embedding), "limit": limit},
    ).all()
    return [row[0] for row in rows]
