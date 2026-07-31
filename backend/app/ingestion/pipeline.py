import logging
from pathlib import Path
from typing import Callable

import fitz
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ingestion.chunker import chunk_pages
from app.ingestion.convert import ConversionError, convert_to_pdf
from app.ingestion.embedder import embed_texts
from app.ingestion.ocr import ocr_page
from app.ingestion.parse import PageLines, extract_pages
from app.models import Chunk, Document

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS_PER_PAGE = 50


class IngestionError(Exception):
    pass


def _set_status(db: Session, document_id: int, status: str, error: str | None = None) -> None:
    doc = db.get(Document, document_id)
    if doc is None:
        return
    doc.ingest_status = status
    doc.ingest_error = error
    db.commit()


def _ocr_low_text_pages(pdf_path: Path, pages: list[PageLines]) -> None:
    """OCR any page whose native text is too sparse to be useful, adopting
    the OCR result only if it recovers more text than the page's native
    extraction -- a page with genuine (if sparse) native text, like a
    title-only slide, must never be discarded for a worse or failed OCR
    attempt. A single page's OCR failure is logged and skipped rather than
    failing the whole document. Mutates `pages` in place."""
    needs_ocr = [p for p in pages if sum(len(line.text) for line in p.lines) < MIN_TEXT_CHARS_PER_PAGE]
    if not needs_ocr:
        return

    fitz_doc = fitz.open(pdf_path)
    try:
        for page_lines in needs_ocr:
            try:
                ocr_lines = ocr_page(fitz_doc[page_lines.page_number - 1])
            except Exception:  # noqa: BLE001 - one page's OCR failure must not fail the whole document
                logger.warning("OCR failed for page %s", page_lines.page_number, exc_info=True)
                continue
            if sum(len(line.text) for line in ocr_lines) > sum(len(line.text) for line in page_lines.lines):
                page_lines.lines = ocr_lines
                page_lines.is_ocr = True
    finally:
        fitz_doc.close()


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
            if doc is None:
                return
            doc.pdf_path = str(pdf_path)
            db.commit()

            _set_status(db, document_id, "parsing")
            pages = extract_pages(pdf_path)
            _ocr_low_text_pages(pdf_path, pages)

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
                        is_ocr=draft.is_ocr,
                    )
                )

            doc = db.get(Document, document_id)
            if doc is None:
                return
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
