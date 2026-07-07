from dataclasses import dataclass, field

from transformers import AutoTokenizer

from app.ingestion.parse import ExtractedLine, PageLines

_TOKENIZER = None


def _tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = AutoTokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
    return _TOKENIZER


def _token_count(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False))


@dataclass
class ChunkDraft:
    text: str
    context_header: str | None
    page_number: int
    bboxes: dict
    token_count: int


def _detect_header(lines: list[ExtractedLine]) -> str | None:
    """Largest-font, bold, top-of-page line is treated as this page's heading."""
    if not lines:
        return None
    candidates = [line for line in lines if line.bold or line.font_size >= 14]
    if not candidates:
        return None
    return max(candidates, key=lambda line: (line.font_size, -line.bbox[1])).text


def _merge_rects(lines: list[ExtractedLine]) -> list[dict]:
    return [{"x0": l.bbox[0], "y0": l.bbox[1], "x1": l.bbox[2], "y1": l.bbox[3]} for l in lines]


def _make_chunk(lines: list[ExtractedLine], page: PageLines, header: str | None) -> ChunkDraft:
    text = "\n".join(line.text for line in lines)
    embed_text = f"{header}\n{text}" if header else text
    return ChunkDraft(
        text=text,
        context_header=header,
        page_number=page.page_number,
        bboxes={"page_width": page.width, "page_height": page.height, "rects": _merge_rects(lines)},
        token_count=_token_count(embed_text),
    )


def chunk_pages(pages: list[PageLines], target_tokens: int = 350, overlap_tokens: int = 80) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []

    for page in pages:
        if not page.lines:
            continue

        header = _detect_header(page.lines)
        body_lines = [line for line in page.lines if line.text != header]

        current: list[ExtractedLine] = []
        current_tokens = 0

        for line in body_lines:
            line_tokens = _token_count(line.text)
            if current and current_tokens + line_tokens > target_tokens:
                chunks.append(_make_chunk(current, page, header))
                # carry the trailing lines forward as overlap
                overlap: list[ExtractedLine] = []
                overlap_tok = 0
                for prev_line in reversed(current):
                    t = _token_count(prev_line.text)
                    if overlap_tok + t > overlap_tokens:
                        break
                    overlap.insert(0, prev_line)
                    overlap_tok += t
                current = overlap
                current_tokens = overlap_tok

            current.append(line)
            current_tokens += line_tokens

        if current:
            chunks.append(_make_chunk(current, page, header))
        elif header and not body_lines:
            # header-only page (e.g. a title slide) still gets one small chunk
            chunks.append(_make_chunk([], page, header) if False else _make_chunk(
                [ExtractedLine(text=header, bbox=(0, 0, page.width, 20), font_size=18, bold=True)], page, None
            ))

    return chunks
