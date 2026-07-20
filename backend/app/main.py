from fastapi import FastAPI

from app.config import get_settings
from app.routers import chat, chunks, courses, coverage, debug, documents

app = FastAPI(title="Study Notes Parser")
app.include_router(courses.router)
app.include_router(documents.router)
app.include_router(debug.router)
app.include_router(chat.router)
app.include_router(chunks.router)
app.include_router(coverage.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.on_event("startup")
def startup() -> None:
    # Embedder/reranker singletons are loaded lazily on first import
    # (see ingestion/embedder.py, retrieval/rerank.py) rather than here,
    # so importing app.main alone (e.g. in tests) never triggers model
    # downloads.
    get_settings()
