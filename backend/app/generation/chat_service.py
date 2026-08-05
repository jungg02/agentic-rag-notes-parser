from dataclasses import asdict, dataclass
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.prompts import build_system_prompt, parse_citations
from app.models import ChatMessage, ChatSession, Course, MessageCitation, QueryTurn, RetrievedChunk
from app.providers.base import LLMMessage, LLMProvider
from app.query.compaction import get_working_history
from app.query.understanding import understand_query
from app.retrieval.service import retrieve


@dataclass
class CitationInfo:
    marker: int
    chunk_id: int
    document_id: int
    filename: str
    page_number: int


def _history_messages(db: Session, session_id: int) -> list[LLMMessage]:
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
    ).all()
    return [LLMMessage(role=r.role, content=r.content) for r in rows]


def stream_assistant_reply(
    db: Session, session: ChatSession, user_content: str, provider: LLMProvider
) -> Iterator[tuple[str, dict]]:
    course = db.get(Course, session.course_id)

    # History *before* this turn -- query understanding resolves the new
    # message against what came before it, not against itself.
    prior_history = _history_messages(db, session.id)

    user_message = ChatMessage(session_id=session.id, role="user", content=user_content)
    db.add(user_message)
    db.commit()

    understanding = understand_query(provider, prior_history, user_content)
    db.add(
        QueryTurn(
            message_id=user_message.id,
            intent=understanding.intent,
            rewritten_query=understanding.rewritten_query,
        )
    )
    db.commit()

    # ADR 003: retrieval runs on the rewritten query when one exists,
    # original content in chat_messages.content is never touched.
    scored_chunks = retrieve(db, session.course_id, understanding.retrieval_query)
    for rank, scored in enumerate(scored_chunks, start=1):
        db.add(RetrievedChunk(message_id=user_message.id, chunk_id=scored.chunk.id, rank=rank))
    db.commit()

    system_prompt, marker_map = build_system_prompt(course.name, scored_chunks)
    # Compaction-aware history (ADR 004) -- includes the user message just
    # committed above, in its original (not rewritten) wording; the
    # rewrite only ever affects which chunks retrieve() found.
    history = get_working_history(db, session, provider)

    full_text = ""
    for delta in provider.generate_stream(history, system=system_prompt):
        full_text += delta
        yield "delta", {"text": delta}

    assistant_message = ChatMessage(session_id=session.id, role="assistant", content=full_text)
    db.add(assistant_message)
    db.flush()

    chunks_by_id = {sc.chunk.id: sc.chunk for sc in scored_chunks}
    used_markers = parse_citations(full_text, marker_map)

    citations: list[CitationInfo] = []
    for marker in used_markers:
        chunk_id = marker_map[marker]
        chunk = chunks_by_id[chunk_id]
        db.add(MessageCitation(message_id=assistant_message.id, chunk_id=chunk_id, marker_index=marker))
        citations.append(
            CitationInfo(
                marker=marker,
                chunk_id=chunk_id,
                document_id=chunk.document_id,
                filename=chunk.document.original_filename,
                page_number=chunk.page_number,
            )
        )

    db.commit()
    yield "done", {
        "message_id": assistant_message.id,
        "citations": [asdict(c) for c in citations],
        "rewritten_query": understanding.rewritten_query,
    }
