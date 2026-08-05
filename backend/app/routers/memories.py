"""Semantic-memory inspection endpoint (Phase 2, Task 7).

List/search a course's memories and delete individual ones -- needed for
debugging (is extraction producing sensible facts? is decay evicting the
right ones?) and for demoing the mechanism directly, without needing a
long chat session to indirectly observe it.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.embedder import embed_query
from app.memory.retrieval import retrieve_memories
from app.models import Course, Memory
from app.schemas import MemoryOut

router = APIRouter(tags=["memories"])


@router.get("/api/courses/{course_id}/memories", response_model=list[MemoryOut])
def list_memories(course_id: int, q: str | None = None, limit: int = 20, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    if q:
        # Search view: read-only, must not perturb decay state just from
        # someone browsing/debugging.
        query_embedding = embed_query(q)
        scored = retrieve_memories(db, course_id, query_embedding, limit=limit, bump_access=False)
        return [s.memory for s in scored]

    return db.scalars(
        select(Memory).where(Memory.course_id == course_id).order_by(Memory.created_at.desc()).limit(limit)
    ).all()


@router.delete("/api/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    memory = db.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    db.delete(memory)
    db.commit()
