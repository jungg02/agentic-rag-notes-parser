import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_engine
from app.config import get_settings


@pytest.fixture(scope="session")
def test_engine():
    settings = get_settings()
    admin_engine = get_engine(settings.database_url.rsplit("/", 1)[0] + "/notes")
    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'notes_test'")
        ).first()
        if not exists:
            conn.execute(text("COMMIT"))
            conn.execute(text("CREATE DATABASE notes_test"))
    admin_engine.dispose()

    engine = get_engine(settings.database_url_test)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    """Wraps each test in an outer transaction that is always rolled back,
    using the standard SQLAlchemy "join a session into an external
    transaction" pattern (SAVEPOINT + restart-on-end listener). Without
    this, any `session.commit()` call inside application code (nearly
    every route and service commits) would commit straight through the
    connection instead of just releasing a SAVEPOINT, breaking the outer
    rollback and leaking committed rows into later tests.

    Use this fixture for ordinary CRUD tests. Do NOT use it for tests that
    exercise code taking a `db_session_factory`-style callable (e.g.
    `run_ingestion`, or any route that hands a session factory to a
    background task) — that code opens its OWN connection when the factory
    is called, and a second, independently-committing session sharing this
    fixture's connection would commit straight through this fixture's
    transaction too, breaking its rollback. Use `real_db_session` instead
    for those cases (see below).
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def real_db_session(test_engine):
    """A plain, actually-committing session bound directly to the shared
    engine (each operation may use a fresh connection from the pool,
    released back to the pool on close — exactly like production).

    Use this ONLY for tests that exercise a `db_session_factory`-style
    callable (e.g. `run_ingestion`, or a route that hands a session factory
    to a `BackgroundTasks` task): that code opens its own separate
    connection/session when the factory is called, so it can only see data
    that was genuinely committed — not data held inside `db_session`'s
    rolled-back transaction on a different connection.

    Tests using this fixture are responsible for cleaning up what they
    create (typically: delete the `Course` you made — it cascades to its
    documents/chunks), since nothing here rolls back automatically.
    """
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
