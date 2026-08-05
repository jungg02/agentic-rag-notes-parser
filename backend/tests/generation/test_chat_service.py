import json
from typing import Iterator

from sqlalchemy import select

from app.generation.chat_service import stream_assistant_reply
from app.generation.prompts import build_system_prompt, parse_citations
from app.ingestion.embedder import embed_texts
from app.ingestion.image_embedder import embed_image_query
from app.models import ChatSession, Chunk, Course, Document, Figure, QueryTurn, RetrievedChunk
from app.providers.base import LLMMessage, LLMResponse


class FakeProvider:
    def __init__(self, reply_text: str):
        self._reply_text = reply_text
        self.last_system_prompt: str | None = None

    def generate(self, messages, system=None, max_tokens=2048):
        # Stands in for the query-understanding call chat_service now makes
        # before retrieval -- always reports a standalone query so these
        # tests keep exercising the original single-turn retrieve() path.
        payload = json.dumps({"intent": "factual_lookup", "needs_rewrite": False, "standalone_query": None})
        return LLMResponse(text=payload, input_tokens=1, output_tokens=1, stop_reason="end_turn")

    def generate_stream(self, messages: list[LLMMessage], system=None, max_tokens=2048) -> Iterator[str]:
        self.last_system_prompt = system
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


class ScriptedProvider:
    """Pops one understanding reply and one answer per stream_assistant_reply
    call, in order, so a multi-turn test can script a different rewrite/
    answer for each turn."""

    def __init__(self, understanding_replies: list[str], answer_replies: list[str]):
        self._understanding_replies = list(understanding_replies)
        self._answer_replies = list(answer_replies)

    def generate(self, messages, system=None, max_tokens=2048):
        payload = self._understanding_replies.pop(0)
        return LLMResponse(text=payload, input_tokens=1, output_tokens=1, stop_reason="end_turn")

    def generate_stream(self, messages, system=None, max_tokens=2048) -> Iterator[str]:
        for word in self._answer_replies.pop(0).split(" "):
            yield word + " "


def _seed_two_topics(db_session):
    course = Course(name="Cell Biology")
    db_session.add(course)
    db_session.flush()
    document = Document(
        course_id=course.id, original_filename="lecture1.pdf", original_format="pdf",
        original_path="/tmp/lecture1.pdf", file_sha256="e" * 64,
    )
    db_session.add(document)
    db_session.flush()

    texts = [
        "Mitochondria produce ATP through cellular respiration in the cell.",
        "Photosynthesis in chloroplasts converts sunlight into chemical energy using chlorophyll.",
    ]
    vectors = embed_texts(texts)
    mito_chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=0, text=texts[0],
        page_number=1, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[0],
    )
    photo_chunk = Chunk(
        document_id=document.id, course_id=course.id, chunk_index=1, text=texts[1],
        page_number=2, bboxes={"page_width": 612.0, "page_height": 792.0, "rects": []},
        token_count=10, embedding=vectors[1],
    )
    db_session.add_all([mito_chunk, photo_chunk])
    db_session.flush()

    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.commit()
    return session, mito_chunk, photo_chunk


def test_stream_assistant_reply_persists_query_turn_and_retrieved_chunks(db_session):
    session, chunk = _seed(db_session)
    provider = FakeProvider("Mitochondria produce ATP [1].")

    list(stream_assistant_reply(db_session, session, "What produces ATP?", provider))

    query_turn = db_session.scalars(select(QueryTurn)).one()
    assert query_turn.intent == "factual_lookup"
    assert query_turn.rewritten_query is None

    retrieved = db_session.scalars(select(RetrievedChunk)).all()
    assert len(retrieved) >= 1
    assert retrieved[0].chunk_id == chunk.id
    assert retrieved[0].rank == 1


def test_multiturn_conversation_with_pronoun_reference_retrieves_correct_chunk(db_session):
    """Phase 1 acceptance criterion: a 3+ turn conversation with pronoun
    references retrieves correctly. Turn 2's "that" and turn 3's "the other
    one" only resolve via query understanding's rewrite -- if retrieval ran
    on the raw pronoun-laden text instead, it would have nothing distinctive
    to match against and could easily land on the wrong chunk."""
    session, mito_chunk, photo_chunk = _seed_two_topics(db_session)

    understanding_replies = [
        json.dumps({"intent": "factual_lookup", "needs_rewrite": False, "standalone_query": None}),
        json.dumps(
            {
                "intent": "follow_up",
                "needs_rewrite": True,
                "standalone_query": "Which organelle does ATP production happen in?",
            }
        ),
        json.dumps(
            {
                "intent": "follow_up",
                "needs_rewrite": True,
                "standalone_query": "How does photosynthesis in chloroplasts work?",
            }
        ),
    ]
    answer_replies = [
        "Mitochondria produce ATP [1].",
        "It happens in the mitochondria [1].",
        "Photosynthesis converts sunlight into chemical energy [1].",
    ]
    provider = ScriptedProvider(understanding_replies, answer_replies)

    turn1 = list(stream_assistant_reply(db_session, session, "What produces ATP in a cell?", provider))
    turn2 = list(stream_assistant_reply(db_session, session, "Which organelle does that happen in?", provider))
    turn3 = list(stream_assistant_reply(db_session, session, "How does the other one work?", provider))

    turn1_citations = [e for e in turn1 if e[0] == "done"][0][1]["citations"]
    turn2_citations = [e for e in turn2 if e[0] == "done"][0][1]["citations"]
    turn3_citations = [e for e in turn3 if e[0] == "done"][0][1]["citations"]

    assert turn1_citations[0]["chunk_id"] == mito_chunk.id
    assert turn2_citations[0]["chunk_id"] == mito_chunk.id  # "that" resolved correctly
    assert turn3_citations[0]["chunk_id"] == photo_chunk.id  # "the other one" resolved correctly

    query_turns = db_session.scalars(select(QueryTurn).order_by(QueryTurn.id)).all()
    assert query_turns[1].rewritten_query == "Which organelle does ATP production happen in?"
    assert query_turns[2].rewritten_query == "How does photosynthesis in chloroplasts work?"


def test_done_event_includes_rewritten_query_when_present(db_session):
    session, mito_chunk, photo_chunk = _seed_two_topics(db_session)
    understanding_replies = [
        json.dumps(
            {"intent": "follow_up", "needs_rewrite": True, "standalone_query": "What produces ATP in a cell?"}
        )
    ]
    provider = ScriptedProvider(understanding_replies, ["Mitochondria produce ATP [1]."])

    events = list(stream_assistant_reply(db_session, session, "What does that do?", provider))
    done_data = [e for e in events if e[0] == "done"][0][1]

    assert done_data["rewritten_query"] == "What produces ATP in a cell?"


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


def test_relevant_memory_is_injected_into_system_prompt_and_not_citable(db_session):
    from app.models import Memory

    session, chunk = _seed(db_session)
    memory_vector = embed_texts(["Prefers worked examples over abstract theory."])[0]
    db_session.add(
        Memory(
            course_id=session.course_id,
            content="Prefers worked examples over abstract theory.",
            embedding=memory_vector,
            memory_type="preference",
            confidence=0.85,
        )
    )
    db_session.commit()

    provider = FakeProvider("Mitochondria produce ATP [1].")
    events = list(
        stream_assistant_reply(db_session, session, "Can you show me a worked example of ATP production?", provider)
    )

    assert provider.last_system_prompt is not None
    assert "<student_context>" in provider.last_system_prompt
    assert "Prefers worked examples over abstract theory." in provider.last_system_prompt
    assert "do NOT cite" in provider.last_system_prompt

    # the memory must never show up in citations -- only chunk excerpts can
    done_data = [e for e in events if e[0] == "done"][0][1]
    assert all(c["chunk_id"] == chunk.id for c in done_data["citations"])


def test_memory_extracted_from_one_session_surfaces_in_a_later_different_session(db_session):
    """Phase 2 acceptance criterion: facts persist and are retrieved in a
    *later* session. Session 1 reveals a preference and goes stale;
    extraction runs and produces a Memory. A brand new Session 2 in the
    same course then gets that memory injected into its system prompt --
    proving persistence and retrieval work across session boundaries, not
    just within one."""
    from datetime import datetime, timedelta, timezone

    from app.memory.session_extraction import extract_stale_sessions
    from app.models import ChatMessage

    session_1, chunk = _seed(db_session)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add(
        ChatMessage(
            session_id=session_1.id,
            role="user",
            content="I prefer worked examples over abstract theory, always.",
            created_at=old,
        )
    )
    db_session.commit()

    class ExtractionProvider:
        def generate(self, messages, system=None, max_tokens=2048):
            payload = json.dumps(
                [{"memory_type": "preference", "content": "Prefers worked examples over abstract theory.", "confidence": 0.9}]
            )
            return LLMResponse(text=payload, input_tokens=1, output_tokens=1, stop_reason="end_turn")

        def generate_stream(self, messages, system=None, max_tokens=2048):
            raise NotImplementedError

    processed = extract_stale_sessions(db_session, session_1.course_id, ExtractionProvider())
    assert processed == 1

    session_2 = ChatSession(course_id=session_1.course_id)
    db_session.add(session_2)
    db_session.commit()

    provider = FakeProvider("Mitochondria produce ATP [1].")
    list(stream_assistant_reply(db_session, session_2, "Can you give me a worked example of ATP production?", provider))

    assert provider.last_system_prompt is not None
    assert "Prefers worked examples over abstract theory." in provider.last_system_prompt


def test_related_figures_surfaced_in_done_event_but_not_shown_to_the_model(db_session):
    """Phase 4, ADR 013: figures are searched every turn like memories, but
    -- unlike memories -- never appear in the system prompt at all, since
    the (text-only) generation model can't do anything useful with a
    SigLIP embedding. They're purely a retrieval-quality surface for the
    frontend."""
    session, chunk = _seed(db_session)
    query = "Can you show me a diagram of the ATP production pathway?"
    # Seeding the figure's embedding via embed_image_query() on the exact
    # query chat_service will later resolve and re-embed is a deterministic
    # trick, not a claim about real image content: it guarantees a
    # perfect-similarity match without depending on SigLIP's zero-shot
    # judgment of an arbitrary real image, keeping this test fast and exact.
    figure = Figure(
        document_id=chunk.document_id, course_id=session.course_id, page_number=5,
        image_path="/tmp/fig.png",
        bbox={"page_width": 612.0, "page_height": 792.0, "x0": 0, "y0": 0, "x1": 100, "y1": 100},
        embedding=embed_image_query(query),
    )
    db_session.add(figure)
    db_session.commit()

    provider = FakeProvider("Mitochondria produce ATP [1].")
    events = list(stream_assistant_reply(db_session, session, query, provider))

    assert "SigLIP" not in (provider.last_system_prompt or "")
    assert figure.image_path not in (provider.last_system_prompt or "")

    done_data = [e for e in events if e[0] == "done"][0][1]
    assert done_data["related_figures"] == [
        {
            "figure_id": figure.id,
            "document_id": chunk.document_id,
            "filename": "lecture1.pdf",
            "page_number": 5,
        }
    ]


def test_related_figures_empty_when_nothing_relevant(db_session):
    session, chunk = _seed(db_session)
    # No Figure rows seeded at all -- retrieve_figures() must degrade to
    # an empty list, not error, the same way it does for an unrelated query.
    provider = FakeProvider("Mitochondria produce ATP [1].")

    events = list(stream_assistant_reply(db_session, session, "What produces ATP?", provider))

    done_data = [e for e in events if e[0] == "done"][0][1]
    assert done_data["related_figures"] == []
