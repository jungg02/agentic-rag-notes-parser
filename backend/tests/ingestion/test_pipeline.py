import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import sessionmaker

from app.ingestion.convert import ConversionError
from app.ingestion.pipeline import run_ingestion
from app.models import Chunk, Course, Document


def test_run_ingestion_pdf_end_to_end(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course PDF")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc1"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "sample.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="sample.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="b" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        # A fresh connection from the pool each call — exactly like
        # production. This only works because `real_db_session`'s writes
        # above were genuinely committed, so this separate connection can
        # see them.
        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.page_count == 2

        chunks = real_db_session.query(Chunk).filter_by(document_id=document_id).order_by(Chunk.chunk_index).all()
        assert len(chunks) >= 2
        assert all(c.course_id == course.id for c in chunks)
        assert any("mitochondria" in c.text.lower() for c in chunks)
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_run_ingestion_replaces_chunks_on_reingest(real_db_session, test_engine, fixtures_dir, tmp_path):
    """Re-ingesting a document (a retry, or re-uploading an already-stored file)
    must replace its chunks. Otherwise the fresh chunk_index values collide with
    the existing rows on uq_chunk_document_index and the document fails."""
    course = Course(name="Pipeline Test Course Reingest")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc3"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "sample.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="sample.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="d" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        assert real_db_session.get(Document, document_id).ingest_status == "ready"
        first_count = real_db_session.query(Chunk).filter_by(document_id=document_id).count()
        assert first_count > 0

        # Second run over the same document: must replace, not collide.
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.ingest_error is None
        assert real_db_session.query(Chunk).filter_by(document_id=document_id).count() == first_count
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def _mock_session_returning(doc):
    session = MagicMock()
    session.get.return_value = doc
    return session


def test_run_ingestion_logs_unexpected_error_with_traceback(caplog):
    """An unhandled failure must be logged with its traceback, not silently
    swallowed into the database, so operators can see WHY ingestion failed."""
    doc = MagicMock()
    doc.original_format = "pdf"
    doc.original_path = "/data/whatever.pdf"
    session = _mock_session_returning(doc)

    with patch("app.ingestion.pipeline.extract_pages", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.ERROR):
            run_ingestion(1, lambda: session)

    assert doc.ingest_status == "failed"
    named = [r for r in caplog.records if "ingesting document" in r.getMessage()]
    assert named, "expected an error-level log naming the failed document"
    assert any(r.exc_info is not None for r in named), "traceback must be captured"


def test_run_ingestion_logs_handled_error(caplog):
    """A handled (expected) failure is logged at warning level with its message."""
    doc = MagicMock()
    doc.original_format = "pptx"
    doc.original_path = "/data/deck.pptx"
    session = _mock_session_returning(doc)

    with patch("app.ingestion.pipeline.convert_to_pdf", side_effect=ConversionError("boom")):
        with caplog.at_level(logging.WARNING):
            run_ingestion(1, lambda: session)

    assert doc.ingest_status == "failed"
    assert doc.ingest_error == "boom"
    assert any("Ingestion failed for document" in r.getMessage() for r in caplog.records)


def test_run_ingestion_docx_converts_and_embeds(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course DOCX")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc2"
        doc_dir.mkdir()
        original = doc_dir / "original.docx"
        shutil.copy(Path(fixtures_dir) / "sample.docx", original)

        document = Document(
            course_id=course.id,
            original_filename="sample.docx",
            original_format="docx",
            original_path=str(original),
            file_sha256="c" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.pdf_path is not None
        assert refreshed.pdf_path.endswith(".pdf")
    finally:
        real_db_session.delete(course)
        real_db_session.commit()
