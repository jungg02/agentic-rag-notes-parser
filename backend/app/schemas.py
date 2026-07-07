from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CourseCreate(BaseModel):
    name: str


class CourseUpdate(BaseModel):
    name: str


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    document_count: int = 0


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    original_filename: str
    original_format: str
    ingest_status: str
    ingest_error: str | None
    page_count: int | None
    created_at: datetime


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    title: str | None
    created_at: datetime


class ChatMessageCreate(BaseModel):
    content: str


class CitationOut(BaseModel):
    marker: int
    chunk_id: int
    document_id: int
    filename: str
    page_number: int


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    citations: list[CitationOut] = []
