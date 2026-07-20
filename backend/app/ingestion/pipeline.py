import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ingestion.chunker import chunk_pages
from app.ingestion.convert import ConversionError, convert_to_pdf
from app.ingestion.embedder import embed_texts
from app.ingestion.parse import extract_pages
from app.models import Chunk, Document

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS_PER_PAGE = 50


class IngestionError(Exception):
    pass


def _set_status(db: Session, document_id: int, status: str, error: str | None = None) -> None:
    doc = db.get(Document, document_id)
    doc.ingest_status = status
    doc.ingest_error = error
    db.commit()


def run_ingestion(document_id: int, db_session_factory: Callable[[], Session]) -> None:
    db = db_session_factory()
    try:
        doc = db.get(Document, document_id)
        if doc is None:
            return

        try:
            if doc.original_format == "pdf":
                pdf_path = Path(doc.original_path)
            else:
                _set_status(db, document_id, "converting")
                output_dir = Path(doc.original_path).parent
                pdf_path = convert_to_pdf(Path(doc.original_path), output_dir)

            doc = db.get(Document, document_id)
            doc.pdf_path = str(pdf_path)
            db.commit()

            _set_status(db, document_id, "parsing")
            pages = extract_pages(pdf_path)

            total_chars = sum(len(line.text) for page in pages for line in page.lines)
            if pages and total_chars < MIN_TEXT_CHARS_PER_PAGE * len(pages):
                raise IngestionError("No extractable text found (scanned document?)")

            drafts = chunk_pages(pages)
            if not drafts:
                raise IngestionError("No chunks produced from document")

            _set_status(db, document_id, "embedding")
            embed_inputs = [f"{d.context_header}\n{d.text}" if d.context_header else d.text for d in drafts]
            vectors = embed_texts(embed_inputs)

            # Ingestion is re-runnable (the retry endpoint, and re-uploading an
            # already-stored file), so drop any chunks from a previous run before
            # inserting this one: the fresh chunk_index values start at 0 again and
            # would otherwise collide on uq_chunk_document_index. This runs in the
            # same transaction as the inserts below, so a failure rolls back and
            # leaves the existing chunks intact.
            db.execute(delete(Chunk).where(Chunk.document_id == document_id))

            for index, (draft, vector) in enumerate(zip(drafts, vectors)):
                db.add(
                    Chunk(
                        document_id=document_id,
                        course_id=doc.course_id,
                        chunk_index=index,
                        text=draft.text,
                        context_header=draft.context_header,
                        page_number=draft.page_number,
                        bboxes=draft.bboxes,
                        token_count=draft.token_count,
                        embedding=vector,
                    )
                )

            doc = db.get(Document, document_id)
            doc.page_count = len(pages)
            doc.ingest_status = "ready"
            doc.ingest_error = None
            db.commit()

        except (ConversionError, IngestionError) as exc:
            db.rollback()
            logger.warning("Ingestion failed for document %s: %s", document_id, exc)
            _set_status(db, document_id, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - any unexpected failure must not crash the background task
            db.rollback()
            logger.exception("Unexpected error ingesting document %s", document_id)
            _set_status(db, document_id, "failed", f"Unexpected error: {exc}")
    finally:
        db.close()
