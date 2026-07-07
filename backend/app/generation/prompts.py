import re

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


def build_system_prompt(course_name: str, chunks: list[ScoredChunk]) -> tuple[str, dict[int, int]]:
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
    return system_prompt, marker_map


def parse_citations(text: str, marker_map: dict[int, int]) -> list[int]:
    used: list[int] = []
    for match in _CITATION_PATTERN.finditer(text):
        marker = int(match.group(1))
        if marker in marker_map and marker not in used:
            used.append(marker)
    return used
