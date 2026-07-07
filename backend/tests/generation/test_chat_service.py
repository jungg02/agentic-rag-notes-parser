from typing import Iterator

from app.generation.chat_service import stream_assistant_reply
from app.generation.prompts import build_system_prompt, parse_citations
from app.ingestion.embedder import embed_texts
from app.models import ChatSession, Chunk, Course, Document
from app.providers.base import LLMMessage


class FakeProvider:
    def __init__(self, reply_text: str):
        self._reply_text = reply_text

    def generate(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError

    def generate_stream(self, messages: list[LLMMessage], system=None, max_tokens=2048) -> Iterator[str]:
        for word in self._reply_text.split(" "):
            yield word + " "


def _seed(db_session):
    course = Course(name="Cell Biology")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="lecture1.pdf", original_format="pdf",
        original_path="/tmp/lecture1.pdf", file_sha256="f" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = ["Mitochondria produce ATP through cellular respiration."]
    vectors = embed_texts(texts)
    chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0, text=texts[0],
        page_number=3, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[0],
    )
    db_session.add(chunk)
    db_session.flush()

    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.commit()
    return session, chunk


def test_parse_citations_extracts_valid_distinct_markers():
    marker_map = {1: 100, 2: 200}
    used = parse_citations("ATP is produced here [1]. Also true [2][1] and [9] is invalid.", marker_map)
    assert used == [1, 2]


def test_stream_assistant_reply_persists_messages_and_citations(db_session):
    session, chunk = _seed(db_session)
    provider = FakeProvider(f"Mitochondria produce ATP [1].")

    events = list(stream_assistant_reply(db_session, session, "What produces ATP?", provider))

    delta_events = [e for e in events if e[0] == "delta"]
    done_events = [e for e in events if e[0] == "done"]
    assert len(delta_events) > 0
    assert len(done_events) == 1

    done_data = done_events[0][1]
    assert len(done_data["citations"]) == 1
    assert done_data["citations"][0]["chunk_id"] == chunk.id
    assert done_data["citations"][0]["page_number"] == 3


def test_stream_assistant_reply_with_no_citations_in_reply(db_session):
    session, chunk = _seed(db_session)
    provider = FakeProvider("I'm not sure the notes cover this.")

    events = list(stream_assistant_reply(db_session, session, "Unrelated question?", provider))
    done_data = [e for e in events if e[0] == "done"][0][1]
    assert done_data["citations"] == []
