from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass
class ExtractedLine:
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float
    bold: bool


@dataclass
class PageLines:
    page_number: int
    width: float
    height: float
    rotation: int
    lines: list[ExtractedLine]


def extract_pages(pdf_path: Path) -> list[PageLines]:
    doc = fitz.open(pdf_path)
    pages: list[PageLines] = []
    try:
        for page_index, page in enumerate(doc):
            page_dict = page.get_text("dict")
            lines: list[ExtractedLine] = []
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    # PostgreSQL text columns cannot store NUL (0x00) bytes, which
                    # PyMuPDF occasionally emits for certain glyphs (common in
                    # PPTX->PDF slide decks). Strip them here at the source so they
                    # never reach the chunks table.
                    text = "".join(span["text"] for span in spans).replace("\x00", "").strip()
                    if not text:
                        continue
                    x0 = min(span["bbox"][0] for span in spans)
                    y0 = min(span["bbox"][1] for span in spans)
                    x1 = max(span["bbox"][2] for span in spans)
                    y1 = max(span["bbox"][3] for span in spans)
                    font_size = max(span["size"] for span in spans)
                    bold = any(span["flags"] & 2**4 for span in spans)
                    lines.append(ExtractedLine(text=text, bbox=(x0, y0, x1, y1), font_size=font_size, bold=bold))
            pages.append(
                PageLines(
                    page_number=page_index + 1,
                    width=page.rect.width,
                    height=page.rect.height,
                    rotation=page.rotation,
                    lines=lines,
                )
            )
    finally:
        doc.close()
    return pages
