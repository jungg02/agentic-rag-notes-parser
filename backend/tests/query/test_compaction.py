import app.query.compaction as compaction
from app.models import ChatMessage, ChatSession, Course
from app.providers.base import LLMResponse


class FakeProvider:
    def __init__(self, summary_text: str):
        self._summary_text = summary_text
        self.calls: list[list] = []

    def generate(self, messages, system=None, max_tokens=2048):
        self.calls.append(messages)
        return LLMResponse(text=self._summary_text, input_tokens=10, output_tokens=10, stop_reason="end_turn")

    def generate_stream(self, messages, system=None, max_tokens=2048):
        raise NotImplementedError


def _session(db_session) -> ChatSession:
    course = Course(name="Compaction Test Course")
    db_session.add(course)
    db_session.flush()
    session = ChatSession(course_id=course.id)
    db_session.add(session)
    db_session.flush()
    return session


def _add_message(db_session, session, role, content):
    m = ChatMessage(session_id=session.id, role=role, content=content)
    db_session.add(m)
    db_session.flush()
    return m


def test_no_compaction_when_under_budget(db_session):
    session = _session(db_session)
    _add_message(db_session, session, "user", "What is BM25?")
    _add_message(db_session, session, "assistant", "A lexical ranking function.")
    provider = FakeProvider("unused")

    history = compaction.get_working_history(db_session, session, provider)

    assert len(history) == 2
    assert history[0].content == "What is BM25?"
    assert provider.calls == []
    assert session.summary is None


def test_compaction_triggers_and_keeps_last_n_verbatim(db_session, monkeypatch):
    monkeypatch.setattr(compaction, "HISTORY_TOKEN_BUDGET", 1)
    monkeypatch.setattr(compaction, "HISTORY_KEEP_LAST_N", 2)
    session = _session(db_session)
    for i in range(6):
        _add_message(db_session, session, "user" if i % 2 == 0 else "assistant", f"turn {i}")
    provider = FakeProvider("Discussed turns 0 through 3.")

    history = compaction.get_working_history(db_session, session, provider)

    # summary turn + last 2 verbatim
    assert len(history) == 3
    assert "Discussed turns 0 through 3." in history[0].content
    assert history[1].content == "turn 4"
    assert history[2].content == "turn 5"
    assert session.summary == "Discussed turns 0 through 3."
    assert len(provider.calls) == 1


def test_incremental_compaction_only_sends_new_messages_to_summarizer(db_session, monkeypatch):
    monkeypatch.setattr(compaction, "HISTORY_TOKEN_BUDGET", 1)
    monkeypatch.setattr(compaction, "HISTORY_KEEP_LAST_N", 2)
    session = _session(db_session)
    for i in range(4):
        _add_message(db_session, session, "user" if i % 2 == 0 else "assistant", f"turn {i}")
    provider = FakeProvider("Summary A")

    compaction.get_working_history(db_session, session, provider)
    first_call_transcript = provider.calls[0][0].content
    assert "turn 0" in first_call_transcript and "turn 1" in first_call_transcript

    # two more turns arrive; only the newly-aged-out turn should be summarized next
    _add_message(db_session, session, "user", "turn 4")
    _add_message(db_session, session, "assistant", "turn 5")
    provider = FakeProvider("Summary A+B")
    compaction.get_working_history(db_session, session, provider)

    second_call_transcript = provider.calls[0][0].content
    assert "turn 2" in second_call_transcript
    assert "turn 0" not in second_call_transcript  # already folded into the existing summary
    assert "Summary A" in second_call_transcript  # existing summary carried forward as context


def test_no_new_summarization_call_when_nothing_new_to_fold_in(db_session, monkeypatch):
    monkeypatch.setattr(compaction, "HISTORY_TOKEN_BUDGET", 1)
    monkeypatch.setattr(compaction, "HISTORY_KEEP_LAST_N", 2)
    session = _session(db_session)
    for i in range(4):
        _add_message(db_session, session, "user" if i % 2 == 0 else "assistant", f"turn {i}")
    provider = FakeProvider("Summary A")

    compaction.get_working_history(db_session, session, provider)
    assert len(provider.calls) == 1

    # calling again with no new messages must not re-summarize
    compaction.get_working_history(db_session, session, provider)
    assert len(provider.calls) == 1
