from app.models import ChatMessage, ChatSession, Course, Document, Chunk, QueryTurn, RetrievedChunk


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


def test_query_turn_and_retrieved_chunks_and_session_compaction_state(db_session):
    course = Course(name="Retrieval Upgrade Plan Course")
    db_session.add(course)
    db_session.flush()

    doc = Document(
        course_id=course.id,
        original_filename="week1.pdf",
        original_format="pdf",
        original_path="/data/files/1/original.pdf",
        file_sha256="b" * 64,
    )
    db_session.add(doc)
    db_session.flush()

    chunk = Chunk(
        document_id=doc.id,
        course_id=course.id,
        chunk_index=0,
        text="BM25 is a lexical ranking function.",
        page_number=1,
        bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=7,
        embedding=[0.01] * 384,
    )
    db_session.add(chunk)
    db_session.flush()

    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.flush()

    user_message = ChatMessage(session_id=session.id, role="user", content="which one handles typos better?")
    db_session.add(user_message)
    db_session.flush()

    query_turn = QueryTurn(
        message_id=user_message.id,
        intent="follow_up",
        rewritten_query="Does BM25 or dense retrieval handle typos better?",
    )
    retrieved = RetrievedChunk(message_id=user_message.id, chunk_id=chunk.id, rank=1)
    db_session.add_all([query_turn, retrieved])
    db_session.flush()

    session.summary = "Discussed BM25 vs dense retrieval."
    session.summarized_through_message_id = user_message.id
    db_session.flush()
    db_session.refresh(session)

    assert query_turn.id is not None
    assert retrieved.id is not None
    assert session.summary == "Discussed BM25 vs dense retrieval."
    assert session.summarized_through_message_id == user_message.id
    assert len(session.messages) == 1
