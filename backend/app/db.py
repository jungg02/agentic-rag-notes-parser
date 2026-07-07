from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def get_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """FastAPI dependency provider for a session factory, so background tasks
    that need their own session (rather than the request-scoped `get_db`
    session) can be pointed at a different database in tests via
    `app.dependency_overrides`."""
    return SessionLocal
