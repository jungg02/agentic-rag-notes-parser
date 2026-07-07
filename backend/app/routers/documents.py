import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.ingestion.pipeline import run_ingestion
from app.models import Course, Document
from app.schemas import DocumentOut

router = APIRouter(tags=["documents"])

_ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx"}


def _store_upload(course_id: int, upload: UploadFile) -> tuple[Path, str, str]:
    ext = Path(upload.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    data = upload.file.read()
    sha256 = hashlib.sha256(data).hexdigest()

    doc_dir = Path(get_settings().data_dir) / f"course_{course_id}" / sha256
    doc_dir.mkdir(parents=True, exist_ok=True)
    dest = doc_dir / f"original{ext}"
    dest.write_bytes(data)

    return dest, sha256, _ALLOWED_EXTENSIONS[ext]


@router.post("/api/courses/{course_id}/documents", response_model=list[DocumentOut], status_code=202)
def upload_documents(
    course_id: int,
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    created: list[Document] = []
    for upload in files:
        dest, sha256, fmt = _store_upload(course_id, upload)

        existing = db.scalar(
            select(Document).where(Document.course_id == course_id, Document.file_sha256 == sha256)
        )
        if existing:
            created.append(existing)
            continue

        document = Document(
            course_id=course_id,
            original_filename=upload.filename,
            original_format=fmt,
            original_path=str(dest),
            file_sha256=sha256,
        )
        db.add(document)
        db.flush()
        created.append(document)

    db.commit()
    for document in created:
        db.refresh(document)
        background_tasks.add_task(run_ingestion, document.id, session_factory)

    return created


@router.get("/api/courses/{course_id}/documents", response_model=list[DocumentOut])
def list_documents(course_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(Document).where(Document.course_id == course_id).order_by(Document.created_at)).all()


@router.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.post("/api/documents/{document_id}/retry", response_model=DocumentOut)
def retry_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    document.ingest_status = "pending"
    document.ingest_error = None
    db.commit()
    db.refresh(document)
    background_tasks.add_task(run_ingestion, document.id, session_factory)
    return document


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_dir = Path(document.original_path).parent
    db.delete(document)
    db.commit()
    shutil.rmtree(doc_dir, ignore_errors=True)


@router.get("/api/documents/{document_id}/pdf")
def get_document_pdf(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None or not document.pdf_path:
        raise HTTPException(status_code=404, detail="PDF not available")
    return FileResponse(document.pdf_path, media_type="application/pdf")
