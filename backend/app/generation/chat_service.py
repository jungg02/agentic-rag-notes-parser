from dataclasses import asdict, dataclass
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.generation.prompts import build_system_prompt, parse_citations
from app.ingestion.embedder import embed_query
from app.ingestion.image_embedder import embed_image_query
from app.memory.retrieval import retrieve_memories
from app.models import ChatMessage, ChatSession, Course, MessageCitation, MessageFigure, QueryTurn, RetrievedChunk
from app.providers.base import LLMMessage, LLMProvider
from app.query.compaction import get_working_history
from app.query.understanding import understand_query
from app.retrieval.figures import retrieve_figures
from app.retrieval.service import retrieve


@dataclass
class CitationInfo:
    marker: int
    chunk_id: int
    document_id: int
    filename: str
    page_number: int


@dataclass
class RelatedFigureInfo:
    figure_id: int
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

    # Phase 2, ADR 005: memories are a separate, relevance-thresholded,
    # capped retrieval -- not fused with chunks, not citable. Embeds the
    # same (possibly rewritten) query text a second time rather than
    # threading retrieve()'s internal embedding through, to avoid touching
    # retrieval/service.py for this phase.
    memory_query_embedding = embed_query(understanding.retrieval_query)
    scored_memories = retrieve_memories(db, session.course_id, memory_query_embedding)

    # Phase 4, ADR 013: figures are searched every turn like memories, but
    # never shown to the generation model at all -- SigLIP embeddings and
    # the text system prompt aren't in a space this text-only model can
    # reason about together, and ADR 013 already ruled out merging figure
    # scores with chunk scores for ranking. Purely a retrieval-quality
    # surface, attached to the response for the frontend to render.
    figure_query_embedding = embed_image_query(understanding.retrieval_query)
    scored_figures = retrieve_figures(db, session.course_id, understanding.retrieval_query, figure_query_embedding)

    system_prompt, marker_map = build_system_prompt(course.name, scored_chunks, scored_memories)
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

    related_figures = [
        RelatedFigureInfo(
            figure_id=sf.figure.id,
            document_id=sf.figure.document_id,
            filename=sf.figure.document.original_filename,
            page_number=sf.figure.page_number,
        )
        for sf in scored_figures
    ]
    for sf in scored_figures:
        db.add(MessageFigure(message_id=assistant_message.id, figure_id=sf.figure.id))

    db.commit()
    yield "done", {
        "message_id": assistant_message.id,
        "citations": [asdict(c) for c in citations],
        "rewritten_query": understanding.rewritten_query,
        "related_figures": [asdict(f) for f in related_figures],
    }
