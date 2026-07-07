from pathlib import Path

from app.ingestion.parse import extract_pages


def test_extract_pages_returns_text_and_bboxes(fixtures_dir):
    pages = extract_pages(Path(fixtures_dir) / "sample.pdf")

    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[0].width > 0 and pages[0].height > 0

    all_text = " ".join(line.text for line in pages[0].lines)
    assert "Cell Biology" in all_text
    assert "mitochondria" in all_text.lower()

    heading_line = next(line for line in pages[0].lines if "Cell Biology" in line.text)
    assert heading_line.font_size > 14
    for x in heading_line.bbox:
        assert isinstance(x, float)


def test_extract_pages_second_page(fixtures_dir):
    pages = extract_pages(Path(fixtures_dir) / "sample.pdf")
    all_text = " ".join(line.text for line in pages[1].lines)
    assert "Genetics" in all_text
