import re

from app.memory.retrieval import ScoredMemory
from app.retrieval.rerank import ScoredChunk

_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

_SYSTEM_TEMPLATE = """You are a study assistant. Answer ONLY from the provided excerpts of the \
user's course notes for "{course_name}". If the excerpts don't contain the answer, say so \
plainly — do not use outside knowledge for factual claims.

Cite your sources: after each claim, add the marker [n] where n is the excerpt number it came \
from. Use multiple markers [1][3] when a claim draws on several excerpts. Every factual sentence \
must carry at least one marker. Do not invent excerpt numbers; only 1 through {count} exist.

<excerpts>
{excerpts}
</excerpts>"""

# Separate from <excerpts> deliberately (ADR 005): memories are never
# citable -- they have no source page, and mixing them into the numbered
# excerpt list would let the model attach a [n] marker to something
# MessageCitation can't resolve to a real chunk.
_MEMORY_SECTION_TEMPLATE = """

<student_context>
Background about this student from earlier sessions, not from the course notes -- use it to \
tailor tone/approach, but do NOT cite it with [n] markers, it has no source page:
{memory_lines}
</student_context>"""


def build_system_prompt(
    course_name: str, chunks: list[ScoredChunk], memories: list[ScoredMemory] | None = None
) -> tuple[str, dict[int, int]]:
    marker_map: dict[int, int] = {}
    excerpt_blocks = []
    for i, scored in enumerate(chunks, start=1):
        marker_map[i] = scored.chunk.id
        excerpt_blocks.append(
            f'[{i}] (from "{scored.chunk.document.original_filename}", page {scored.chunk.page_number})\n'
            f"{scored.chunk.text}"
        )

    system_prompt = _SYSTEM_TEMPLATE.format(
        course_name=course_name,
        count=len(chunks),
        excerpts="\n\n".join(excerpt_blocks) if excerpt_blocks else "(no relevant excerpts found)",
    )

    if memories:
        memory_lines = "\n".join(f"- {scored.memory.content}" for scored in memories)
        system_prompt += _MEMORY_SECTION_TEMPLATE.format(memory_lines=memory_lines)

    return system_prompt, marker_map


def parse_citations(text: str, marker_map: dict[int, int]) -> list[int]:
    used: list[int] = []
    for match in _CITATION_PATTERN.finditer(text):
        marker = int(match.group(1))
        if marker in marker_map and marker not in used:
            used.append(marker)
    return used
