from datetime import datetime, timezone

from app.generation.prompts import build_system_prompt
from app.memory.retrieval import ScoredMemory
from app.models import Memory
from app.retrieval.rerank import ScoredChunk


def _chunk_stub(chunk_id, filename, page, text):
    from types import SimpleNamespace

    document = SimpleNamespace(original_filename=filename)
    return SimpleNamespace(id=chunk_id, document=document, page_number=page, text=text)


def test_no_memory_section_when_no_memories_passed():
    chunks = [ScoredChunk(chunk=_chunk_stub(1, "notes.pdf", 1, "ATP is produced here."), score=0.9)]
    prompt, marker_map = build_system_prompt("Cell Biology", chunks)
    assert "<student_context>" not in prompt
    assert marker_map == {1: 1}


def test_no_memory_section_when_empty_memory_list_passed():
    chunks = [ScoredChunk(chunk=_chunk_stub(1, "notes.pdf", 1, "ATP is produced here."), score=0.9)]
    prompt, _ = build_system_prompt("Cell Biology", chunks, memories=[])
    assert "<student_context>" not in prompt


def test_memory_section_included_and_marked_non_citable():
    chunks = [ScoredChunk(chunk=_chunk_stub(1, "notes.pdf", 1, "ATP is produced here."), score=0.9)]
    memory = Memory(
        id=1,
        course_id=1,
        content="Prefers worked examples over abstract theory.",
        embedding=[0.01] * 384,
        memory_type="preference",
        confidence=0.8,
        access_count=0,
        created_at=datetime.now(timezone.utc),
    )
    memories = [ScoredMemory(memory=memory, similarity=0.75)]

    prompt, marker_map = build_system_prompt("Cell Biology", chunks, memories)

    assert "<student_context>" in prompt
    assert "Prefers worked examples over abstract theory." in prompt
    assert "do NOT cite" in prompt
    # memories never enter the citation marker map -- only chunks do
    assert marker_map == {1: 1}


def test_memory_section_lists_multiple_memories():
    chunks: list[ScoredChunk] = []
    now = datetime.now(timezone.utc)
    memories = [
        ScoredMemory(
            memory=Memory(
                id=i, course_id=1, content=f"Fact {i}", embedding=[0.01] * 384,
                memory_type="topic", confidence=0.8, access_count=0, created_at=now,
            ),
            similarity=0.6,
        )
        for i in range(1, 3)
    ]

    prompt, _ = build_system_prompt("Cell Biology", chunks, memories)

    assert "Fact 1" in prompt
    assert "Fact 2" in prompt
