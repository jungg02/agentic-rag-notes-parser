import io
import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.ingestion.convert import ConversionError
from app.ingestion.parse import ExtractedLine, PageLines
from app.ingestion.pipeline import _ocr_low_text_pages, _set_status, run_ingestion
from app.models import Chunk, Course, Document, Figure


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
        assert all(not c.is_ocr for c in chunks)
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


def test_set_status_noop_when_document_already_deleted():
    """_set_status must not raise when the document row is gone -- it has
    nothing left to update."""
    session = MagicMock()
    session.get.return_value = None

    _set_status(session, 1, "failed", "boom")

    session.commit.assert_not_called()


def test_run_ingestion_stops_cleanly_if_document_deleted_before_pdf_path_write():
    """If the document is deleted between the initial existence check and the
    pdf_path write (e.g. via a concurrent delete-document request), run_ingestion
    must return cleanly instead of raising on a deleted row."""
    doc = MagicMock()
    doc.original_format = "pdf"
    doc.original_path = "/data/whatever.pdf"

    session = MagicMock()
    session.get.side_effect = [doc, None]

    run_ingestion(1, lambda: session)  # must not raise

    session.commit.assert_not_called()


def test_run_ingestion_survives_document_deleted_during_failure_handling(caplog):
    """A document can be deleted (via the delete-document endpoint) while its
    ingestion is still in flight. If that happens exactly as an unrelated
    ingestion error is being handled, the failure-handling _set_status call
    used to hit a deleted row and raise a second, uncaught exception that
    escaped the background task entirely -- masking the original failure.
    _set_status must no-op instead."""
    doc = MagicMock()
    doc.original_format = "pdf"
    doc.original_path = "/data/whatever.pdf"

    session = MagicMock()
    session.get.side_effect = [doc, doc, doc, None]

    with patch("app.ingestion.pipeline.extract_pages", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.ERROR):
            run_ingestion(1, lambda: session)  # must not raise

    named = [r for r in caplog.records if "ingesting document" in r.getMessage()]
    assert named, "the original failure must still be logged"


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


def test_run_ingestion_ocrs_scanned_pdf(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course OCR")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc4"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "scanned.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="scanned.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="e" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"

        chunks = real_db_session.query(Chunk).filter_by(document_id=document_id).all()
        assert len(chunks) >= 1
        assert all(c.is_ocr for c in chunks)
        assert any("osmosis" in c.text.lower() for c in chunks)
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_ocr_low_text_pages_keeps_native_text_when_ocr_is_worse(fixtures_dir):
    """A page with genuine, if sparse, native text (e.g. a title-only slide)
    must not be discarded in favor of OCR output that recovers less text --
    OCR is a fallback for pages that have nothing useful, not an override
    for every sparse page."""
    sparse_line = ExtractedLine(text="Title", bbox=(72, 100, 200, 120), font_size=18, bold=True)
    page = PageLines(page_number=1, width=612.0, height=792.0, rotation=0, lines=[sparse_line])

    with patch("app.ingestion.pipeline.ocr_page", return_value=[]):
        _ocr_low_text_pages(f"{fixtures_dir}/sample.pdf", [page])

    assert page.lines == [sparse_line]
    assert page.is_ocr is False


def test_ocr_low_text_pages_skips_page_on_ocr_error(fixtures_dir, caplog):
    """If ocr_page() raises for one page, that page's native text is kept
    and no exception propagates -- one page's OCR failure must not fail the
    whole document."""
    import logging

    sparse_line = ExtractedLine(text="Title", bbox=(72, 100, 200, 120), font_size=18, bold=True)
    page = PageLines(page_number=1, width=612.0, height=792.0, rotation=0, lines=[sparse_line])

    with patch("app.ingestion.pipeline.ocr_page", side_effect=RuntimeError("tesseract not found")):
        with caplog.at_level(logging.WARNING):
            _ocr_low_text_pages(f"{fixtures_dir}/sample.pdf", [page])  # must not raise

    assert page.lines == [sparse_line]
    assert page.is_ocr is False
    assert any("OCR failed for page" in r.getMessage() for r in caplog.records)


def test_run_ingestion_mixed_pdf_ocrs_only_the_scanned_page(real_db_session, test_engine, fixtures_dir, tmp_path):
    course = Course(name="Pipeline Test Course Mixed")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc5"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        shutil.copy(Path(fixtures_dir) / "mixed.pdf", original)

        document = Document(
            course_id=course.id,
            original_filename="mixed.pdf",
            original_format="pdf",
            original_path=str(original),
            file_sha256="f" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"

        chunks = real_db_session.query(Chunk).filter_by(document_id=document_id).all()
        page1_chunks = [c for c in chunks if c.page_number == 1]
        page2_chunks = [c for c in chunks if c.page_number == 2]
        assert page1_chunks and all(not c.is_ocr for c in page1_chunks)
        assert page2_chunks and all(c.is_ocr for c in page2_chunks)
        assert any("ribosomes" in c.text.lower() for c in page1_chunks)
        assert any("chlorophyll" in c.text.lower() for c in page2_chunks)
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def _make_pdf_with_text_and_figure(path: Path) -> None:
    """A synthetic PDF with enough real text to clear
    MIN_TEXT_CHARS_PER_PAGE plus one genuine embedded figure -- the
    checked-in fixtures (sample/scanned/mixed.pdf) all yield zero figures
    after the full-page-scan filter (see app/ingestion/figures.py), so
    they can't exercise the figure-extraction path in these tests."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 72),
        "Mitochondria produce ATP through cellular respiration. " * 3,
        fontsize=11,
    )
    img = Image.new("RGB", (300, 300), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(72, 200, 372, 500), stream=buf.getvalue())
    doc.save(path)
    doc.close()


def test_run_ingestion_extracts_and_embeds_figures(real_db_session, test_engine, tmp_path):
    course = Course(name="Pipeline Test Course Figures")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc_figures"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        _make_pdf_with_text_and_figure(original)

        document = Document(
            course_id=course.id, original_filename="figured.pdf", original_format="pdf",
            original_path=str(original), file_sha256="1" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"

        figures = real_db_session.query(Figure).filter_by(document_id=document_id).all()
        assert len(figures) == 1
        assert figures[0].page_number == 1
        assert len(figures[0].embedding) == 768
        assert Path(figures[0].image_path).exists()
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_run_ingestion_reextracts_figures_cleanly_on_reingest(real_db_session, test_engine, tmp_path):
    course = Course(name="Pipeline Test Course Figures Reingest")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc_figures_reingest"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        _make_pdf_with_text_and_figure(original)

        document = Document(
            course_id=course.id, original_filename="figured.pdf", original_format="pdf",
            original_path=str(original), file_sha256="2" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)
        real_db_session.expire_all()
        first_paths = [f.image_path for f in real_db_session.query(Figure).filter_by(document_id=document_id).all()]
        assert len(first_paths) == 1

        run_ingestion(document_id, session_factory)
        real_db_session.expire_all()
        second = real_db_session.query(Figure).filter_by(document_id=document_id).all()
        assert len(second) == 1  # not doubled
        assert Path(first_paths[0]).exists()  # re-extraction didn't leave the old file dangling either
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_run_ingestion_survives_figure_extraction_failure(real_db_session, test_engine, tmp_path):
    """Figures are an enrichment, not part of what makes a document
    'ready' (ADR 014's graceful-degradation philosophy) -- a failure in
    the figure-extraction step must not flip an otherwise-successful
    ingestion to 'failed'."""
    course = Course(name="Pipeline Test Course Figure Failure")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc_figure_failure"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        _make_pdf_with_text_and_figure(original)

        document = Document(
            course_id=course.id, original_filename="figured.pdf", original_format="pdf",
            original_path=str(original), file_sha256="3" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        with patch("app.ingestion.pipeline.extract_figures", side_effect=RuntimeError("boom")):
            run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
        assert refreshed.ingest_error is None
        assert real_db_session.query(Chunk).filter_by(document_id=document_id).count() > 0
        assert real_db_session.query(Figure).filter_by(document_id=document_id).count() == 0
    finally:
        real_db_session.delete(course)
        real_db_session.commit()


def test_run_ingestion_reingest_failure_preserves_previous_figures(real_db_session, test_engine, tmp_path):
    """A failure on re-ingestion (e.g. embed_images raising on a corrupt
    image) must not delete the previous run's figure rows or image files
    -- extraction writes to a staging directory and only replaces the old
    ones after both extraction and embedding succeed."""
    course = Course(name="Pipeline Test Course Figure Retry Failure")
    real_db_session.add(course)
    real_db_session.commit()

    try:
        doc_dir = tmp_path / "doc_figure_retry_failure"
        doc_dir.mkdir()
        original = doc_dir / "original.pdf"
        _make_pdf_with_text_and_figure(original)

        document = Document(
            course_id=course.id, original_filename="figured.pdf", original_format="pdf",
            original_path=str(original), file_sha256="4" * 64,
        )
        real_db_session.add(document)
        real_db_session.commit()
        document_id = document.id

        session_factory = sessionmaker(bind=test_engine)
        run_ingestion(document_id, session_factory)
        real_db_session.expire_all()
        first = real_db_session.query(Figure).filter_by(document_id=document_id).all()
        assert len(first) == 1
        first_path = first[0].image_path
        assert Path(first_path).exists()

        with patch("app.ingestion.pipeline.embed_images", side_effect=RuntimeError("corrupt image")):
            run_ingestion(document_id, session_factory)

        real_db_session.expire_all()
        second = real_db_session.query(Figure).filter_by(document_id=document_id).all()
        assert len(second) == 1
        assert second[0].image_path == first_path
        assert Path(first_path).exists()
        refreshed = real_db_session.get(Document, document_id)
        assert refreshed.ingest_status == "ready"
    finally:
        real_db_session.delete(course)
        real_db_session.commit()
