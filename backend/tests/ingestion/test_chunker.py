from app.ingestion.chunker import chunk_pages
from app.ingestion.parse import ExtractedLine, PageLines


def _line(text, y=100, size=11, bold=False):
    return ExtractedLine(text=text, bbox=(72.0, y, 500.0, y + 14), font_size=size, bold=bold)


def test_single_short_page_produces_one_chunk_with_header():
    page = PageLines(
        page_number=1,
        width=612.0,
        height=792.0,
        rotation=0,
        lines=[
            _line("Lecture 4: Photosynthesis", y=100, size=18, bold=True),
            _line("Light reactions occur in the thylakoid membrane.", y=140),
            _line("Dark reactions occur in the stroma.", y=160),
        ],
    )

    chunks = chunk_pages([page])

    assert len(chunks) == 1
    assert chunks[0].context_header == "Lecture 4: Photosynthesis"
    assert "thylakoid" in chunks[0].text
    assert chunks[0].page_number == 1
    assert chunks[0].bboxes["page_width"] == 612.0
    assert len(chunks[0].bboxes["rects"]) == 2  # header line excluded from body rects


def test_long_page_splits_into_multiple_chunks_with_overlap():
    body_lines = [_line(f"Sentence number {i} about cell biology topics in detail.", y=100 + i * 14) for i in range(80)]
    page = PageLines(page_number=2, width=612.0, height=792.0, rotation=0, lines=[_line("Overview", size=18, bold=True)] + body_lines)

    chunks = chunk_pages([page], target_tokens=100, overlap_tokens=20)

    assert len(chunks) > 1
    assert all(c.page_number == 2 for c in chunks)
    assert all(c.context_header == "Overview" for c in chunks)


def test_chunks_never_span_pages():
    page1 = PageLines(page_number=1, width=612, height=792, rotation=0, lines=[_line("Page one content sentence.")])
    page2 = PageLines(page_number=2, width=612, height=792, rotation=0, lines=[_line("Page two content sentence.")])

    chunks = chunk_pages([page1, page2])

    pages_seen = {c.page_number for c in chunks}
    assert pages_seen == {1, 2}
    for c in chunks:
        assert c.page_number in (1, 2)
