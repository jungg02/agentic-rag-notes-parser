import shutil
from pathlib import Path

from sqlalchemy.orm import sessionmaker

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
