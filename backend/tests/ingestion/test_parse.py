from pathlib import Path
from unittest.mock import patch

from app.ingestion.parse import extract_pages


class _FakeRect:
    width = 100.0
    height = 200.0


class _FakePage:
    rotation = 0
    rect = _FakeRect()

    def __init__(self, page_dict):
        self._page_dict = page_dict

    def get_text(self, kind):
        assert kind == "dict"
        return self._page_dict


class _FakeDoc:
    def __init__(self, pages):
        self._pages = pages

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        pass


def test_extract_pages_strips_nul_bytes():
    """PyMuPDF occasionally emits NUL (0x00) chars for certain glyphs (common
    in PPTX->PDF slide decks). PostgreSQL text columns reject NUL bytes, so
    extraction must strip them at the source before they reach the chunks table.
    """
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {
                        "spans": [
                            {"text": "Hello\x00World", "bbox": (0.0, 0.0, 10.0, 5.0), "size": 12.0, "flags": 0}
                        ]
                    }
                ]
            }
        ]
    }
    with patch("app.ingestion.parse.fitz.open", return_value=_FakeDoc([_FakePage(page_dict)])):
        pages = extract_pages(Path("ignored.pdf"))

    assert len(pages) == 1
    assert len(pages[0].lines) == 1
    assert "\x00" not in pages[0].lines[0].text
    assert pages[0].lines[0].text == "HelloWorld"


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
