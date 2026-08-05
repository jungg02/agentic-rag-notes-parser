"""Figure inspection + image serving (Phase 4, mirrors memories.py's
inspection endpoint and documents.py's PDF-serving pattern).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.image_embedder import embed_image_query
from app.models import Course, Figure
from app.retrieval.figures import retrieve_figures
from app.schemas import FigureOut

router = APIRouter(tags=["figures"])


@router.get("/api/courses/{course_id}/figures", response_model=list[FigureOut])
def list_figures(course_id: int, q: str | None = None, limit: int = 20, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    if q:
        query_embedding = embed_image_query(q)
        scored = retrieve_figures(db, course_id, q, query_embedding, limit=limit)
        return [s.figure for s in scored]

    return db.scalars(
        select(Figure).where(Figure.course_id == course_id).order_by(Figure.created_at.desc()).limit(limit)
    ).all()


@router.get("/api/figures/{figure_id}/image")
def get_figure_image(figure_id: int, db: Session = Depends(get_db)):
    figure = db.get(Figure, figure_id)
    if figure is None or not Path(figure.image_path).exists():
        raise HTTPException(status_code=404, detail="Figure not found")
    return FileResponse(figure.image_path)
