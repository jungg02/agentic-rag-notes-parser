from app.models import Course, Document, Chunk


def test_create_course_document_chunk(db_session):
    course = Course(name="Biology 101")
    db_session.add(course)
    db_session.flush()

    doc = Document(
        course_id=course.id,
        original_filename="week1.pdf",
        original_format="pdf",
        original_path="/data/files/1/original.pdf",
        pdf_path="/data/files/1/original.pdf",
        file_sha256="a" * 64,
    )
    db_session.add(doc)
    db_session.flush()

    chunk = Chunk(
        document_id=doc.id,
        course_id=course.id,
        chunk_index=0,
        text="Mitochondria is the powerhouse of the cell.",
        page_number=1,
        bboxes={"page_width": 612.0, "page_height": 792.0, "rects": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]},
        token_count=8,
        embedding=[0.01] * 384,
    )
    db_session.add(chunk)
    db_session.flush()

    assert chunk.id is not None
    assert doc.ingest_status == "pending"
