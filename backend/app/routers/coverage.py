from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Chunk, Course, Document

router = APIRouter(prefix="/api/courses", tags=["coverage"])


def _document_coverage(
    *,
    document_id: int,
    filename: str,
    ingest_status: str,
    ingest_error: str | None,
    page_count: int | None,
    present_pages: set[int],
    chunk_count: int,
    token_sum: int,
) -> dict:
    if ingest_status == "ready" and page_count is not None:
        present = {p for p in present_pages if 1 <= p <= page_count}
        pages_with_text = len(present)
        dropped_pages = [p for p in range(1, page_count + 1) if p not in present]
        coverage_pct = round(pages_with_text / page_count * 100, 1) if page_count else 0.0
        return {
            "document_id": document_id,
            "filename": filename,
            "ingest_status": ingest_status,
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "coverage_pct": coverage_pct,
            "dropped_pages": dropped_pages,
            "chunks": chunk_count,
            "tokens": token_sum,
            "ingest_error": ingest_error,
        }
    return {
        "document_id": document_id,
        "filename": filename,
        "ingest_status": ingest_status,
        "page_count": None,
        "pages_with_text": None,
        "coverage_pct": None,
        "dropped_pages": None,
        "chunks": chunk_count,
        "tokens": token_sum,
        "ingest_error": ingest_error,
    }


@router.get("/{course_id}/coverage")
def course_coverage(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    documents = db.scalars(
        select(Document).where(Document.course_id == course_id).order_by(Document.id)
    ).all()

    doc_ids = [d.id for d in documents]
    agg: dict[int, tuple[set[int], int, int]] = {}
    if doc_ids:
        rows = db.execute(
            select(
                Chunk.document_id,
                func.array_agg(distinct(Chunk.page_number)),
                func.count(Chunk.id),
                func.coalesce(func.sum(Chunk.token_count), 0),
            )
            .where(Chunk.document_id.in_(doc_ids))
            .group_by(Chunk.document_id)
        ).all()
        agg = {row[0]: (set(row[1]), row[2], row[3]) for row in rows}

    doc_reports = []
    for d in documents:
        present_pages, chunk_count, token_sum = agg.get(d.id, (set(), 0, 0))
        doc_reports.append(
            _document_coverage(
                document_id=d.id,
                filename=d.original_filename,
                ingest_status=d.ingest_status,
                ingest_error=d.ingest_error,
                page_count=d.page_count,
                present_pages=present_pages,
                chunk_count=chunk_count,
                token_sum=token_sum,
            )
        )

    # A "ready" document always has a page_count under the real pipeline
    # (pipeline sets page_count and status="ready" in the same commit), but a
    # manual insert or migration could leave page_count NULL. Sum coverage
    # totals only over ready docs that actually have page data so a stray NULL
    # can't turn the whole endpoint into a 500. summary["ready"] stays a true
    # count of ready-by-status documents.
    ready_with_pages = [
        r for r in doc_reports
        if r["ingest_status"] == "ready" and r["page_count"] is not None
    ]
    total_pages = sum(r["page_count"] for r in ready_with_pages)
    pages_with_text = sum(r["pages_with_text"] for r in ready_with_pages)
    summary = {
        "documents": len(doc_reports),
        "ready": sum(1 for r in doc_reports if r["ingest_status"] == "ready"),
        "failed": sum(1 for r in doc_reports if r["ingest_status"] == "failed"),
        "in_progress": sum(
            1 for r in doc_reports if r["ingest_status"] not in ("ready", "failed")
        ),
        "total_pages": total_pages,
        "pages_with_text": pages_with_text,
        "coverage_pct": round(pages_with_text / total_pages * 100, 1) if total_pages else 0.0,
        "total_chunks": sum(r["chunks"] for r in ready_with_pages),
        "total_tokens": sum(r["tokens"] for r in ready_with_pages),
    }

    return {"course_id": course_id, "summary": summary, "documents": doc_reports}
