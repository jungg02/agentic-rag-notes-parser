import fitz
import pytesseract
from PIL import Image
from pytesseract import Output

from app.ingestion.parse import ExtractedLine

OCR_DPI = 300
MIN_OCR_CONFIDENCE = 40


def ocr_page(page: fitz.Page, dpi: int = OCR_DPI) -> list[ExtractedLine]:
    """Render a PDF page to an image and OCR it, returning lines in the same
    PDF-point coordinate space and ExtractedLine shape extract_pages()
    produces, so downstream chunking needs no changes to consume OCR'd text.

    Words are grouped into lines using Tesseract's (block, paragraph, line)
    keys, which preserves natural reading order in image_to_data's output --
    no separate sort is needed.
    """
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(image, output_type=Output.DICT)

    line_words: dict[tuple[int, int, int], list[int]] = {}
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        if float(data["conf"][i]) < MIN_OCR_CONFIDENCE:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_words.setdefault(key, []).append(i)

    lines: list[ExtractedLine] = []
    for indices in line_words.values():
        words = [data["text"][i].strip() for i in indices]
        text = " ".join(w for w in words if w)
        if not text:
            continue
        x0 = min(data["left"][i] for i in indices) / zoom
        y0 = min(data["top"][i] for i in indices) / zoom
        x1 = max(data["left"][i] + data["width"][i] for i in indices) / zoom
        y1 = max(data["top"][i] + data["height"][i] for i in indices) / zoom
        font_size = max(data["height"][i] for i in indices) / zoom
        lines.append(ExtractedLine(text=text, bbox=(x0, y0, x1, y1), font_size=font_size, bold=False))

    return lines
