from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Course, Document
from app.schemas import CourseCreate, CourseOut, CourseUpdate

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.post("", response_model=CourseOut, status_code=201)
def create_course(payload: CourseCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Course).where(Course.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="A course with this name already exists")
    course = Course(name=payload.name)
    db.add(course)
    db.commit()
    db.refresh(course)
    return CourseOut(id=course.id, name=course.name, created_at=course.created_at, document_count=0)


@router.get("", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Course, func.count(Document.id))
        .outerjoin(Document, Document.course_id == Course.id)
        .group_by(Course.id)
        .order_by(Course.created_at)
    ).all()
    return [
        CourseOut(id=c.id, name=c.name, created_at=c.created_at, document_count=count)
        for c, count in rows
    ]


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, payload: CourseUpdate, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    course.name = payload.name
    db.commit()
    db.refresh(course)
    doc_count = db.scalar(select(func.count(Document.id)).where(Document.course_id == course_id))
    return CourseOut(id=course.id, name=course.name, created_at=course.created_at, document_count=doc_count)


@router.delete("/{course_id}", status_code=204)
def delete_course(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    db.delete(course)
    db.commit()
