from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Chunk

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


@router.get("/{chunk_id}")
def get_chunk(chunk_id: int, db: Session = Depends(get_db)):
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "filename": chunk.document.original_filename,
        "pdf_url": f"/api/documents/{chunk.document_id}/pdf",
        "page_number": chunk.page_number,
        "bboxes": chunk.bboxes,
        "text": chunk.text,
        "context_header": chunk.context_header,
    }
