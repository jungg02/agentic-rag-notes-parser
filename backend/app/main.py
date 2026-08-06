from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers import chat, chunks, courses, coverage, debug, documents, figures, memories


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Embedder/reranker singletons are loaded lazily on first import
    # (see ingestion/embedder.py, retrieval/rerank.py) rather than here,
    # so importing app.main alone (e.g. in tests) never triggers model
    # downloads.
    get_settings()
    yield


app = FastAPI(title="Multi-Turn Hybrid Retrieval System with Agentic Memory", lifespan=lifespan)
app.include_router(courses.router)
app.include_router(documents.router)
app.include_router(debug.router)
app.include_router(chat.router)
app.include_router(chunks.router)
app.include_router(coverage.router)
app.include_router(memories.router)
app.include_router(figures.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
