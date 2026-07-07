import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.generation.chat_service import stream_assistant_reply
from app.models import ChatMessage, ChatSession, Course, MessageCitation
from app.providers.base import LLMProvider
from app.providers.factory import get_provider
from app.schemas import ChatMessageCreate, ChatMessageOut, ChatSessionOut, CitationOut

router = APIRouter(tags=["chat"])


@router.post("/api/courses/{course_id}/sessions", response_model=ChatSessionOut, status_code=201)
def create_session(course_id: int, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    session = ChatSession(course_id=course_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/api/courses/{course_id}/sessions", response_model=list[ChatSessionOut])
def list_sessions(course_id: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(ChatSession).where(ChatSession.course_id == course_id).order_by(ChatSession.created_at)
    ).all()


@router.get("/api/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def get_messages(session_id: int, db: Session = Depends(get_db)):
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
    ).all()

    result = []
    for m in messages:
        citation_rows = db.scalars(select(MessageCitation).where(MessageCitation.message_id == m.id)).all()
        citations = [
            CitationOut(
                marker=c.marker_index,
                chunk_id=c.chunk_id,
                document_id=c.chunk.document_id,
                filename=c.chunk.document.original_filename,
                page_number=c.chunk.page_number,
            )
            for c in citation_rows
        ]
        result.append(ChatMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at, citations=citations))
    return result


@router.post("/api/sessions/{session_id}/messages")
def post_message(
    session_id: int,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_provider),
):
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    def event_stream():
        for event_type, data in stream_assistant_reply(db, session, payload.content, provider):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
