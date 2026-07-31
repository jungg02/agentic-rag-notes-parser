import fitz

from app.ingestion.ocr import ocr_page


def test_ocr_page_recovers_text_from_image_only_page(fixtures_dir):
    doc = fitz.open(f"{fixtures_dir}/scanned.pdf")
    try:
        page = doc[0]
        page_width, page_height = page.rect.width, page.rect.height
        lines = ocr_page(page)
    finally:
        doc.close()

    text = " ".join(line.text for line in lines)
    assert "Osmosis" in text
    assert "membrane" in text.lower()
    for line in lines:
        assert 0 <= line.bbox[0] <= page_width
        assert 0 <= line.bbox[1] <= page_height
        assert 0 <= line.bbox[2] <= page_width
        assert 0 <= line.bbox[3] <= page_height
        assert 5 < line.font_size < 40
        assert line.bold is False
