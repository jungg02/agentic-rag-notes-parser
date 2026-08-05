"""Figure (embedded image) extraction from PDF pages (Phase 4 of the
retrieval upgrade plan). Reuses the same PyMuPDF page-access primitives
`ocr.py` already uses to touch page images, rather than a separate
PDF-rendering path.

MIN_FIGURE_DIMENSION_PX filters out decorative/junk fragments -- PDFs
exported from PowerPoint in particular can embed hundreds of tiny mask/
gradient-tile image fragments per page that PyMuPDF reports as ordinary
embedded images. Calibrated empirically against this app's own ingested
corpus: one course's real embedded figures (illustrations) were all
>=320px on their shorter side; another course's junk fragments had a
median area of 81px^2 (e.g. 9x9) with a clean, near-total separation from
its ~27 genuine chart/diagram images, all >=174px on their shorter side.
100px is comfortably below every real figure observed and comfortably
above the junk-fragment population.

MAX_FIGURE_PAGE_COVERAGE filters the opposite extreme: a scanned page
(this app's own OCR fallback path -- app/ingestion/ocr.py) embeds the
*entire page* as one image, which would otherwise pass the size filter
above easily and get indexed as a "figure". Found directly in this app's
own test fixtures (scanned.pdf, mixed.pdf) while building this module. A
genuine figure occupies part of a page; something covering nearly all of
it is the page itself.
"""
from dataclasses import dataclass
from pathlib import Path

import fitz

MIN_FIGURE_DIMENSION_PX = 100
MAX_FIGURE_PAGE_COVERAGE = 0.9


@dataclass
class FigureDraft:
    page_number: int
    bbox: dict
    image_bytes: bytes
    image_ext: str


def extract_figures(pdf_path: Path) -> list[FigureDraft]:
    doc = fitz.open(pdf_path)
    drafts: list[FigureDraft] = []
    try:
        for page in doc:
            for info in page.get_image_info(xrefs=True):
                xref = info.get("xref", 0)
                if xref <= 0:
                    continue  # inline images have no xref; extract_image() needs one
                width, height = info["width"], info["height"]
                if min(width, height) < MIN_FIGURE_DIMENSION_PX:
                    continue

                bx0, by0, bx1, by1 = info["bbox"]
                bbox_area = max(bx1 - bx0, 0) * max(by1 - by0, 0)
                page_area = page.rect.width * page.rect.height
                if page_area > 0 and bbox_area / page_area >= MAX_FIGURE_PAGE_COVERAGE:
                    continue  # covers the whole page -- a scanned page image, not a figure

                try:
                    base = doc.extract_image(xref)
                except Exception:  # noqa: BLE001 - one bad image must not fail the whole document
                    continue

                drafts.append(
                    FigureDraft(
                        page_number=page.number + 1,
                        bbox={
                            "page_width": page.rect.width,
                            "page_height": page.rect.height,
                            "x0": bx0, "y0": by0, "x1": bx1, "y1": by1,
                        },
                        image_bytes=base["image"],
                        image_ext=base["ext"],
                    )
                )
    finally:
        doc.close()
    return drafts


def save_figure_image(doc_dir: Path, draft: FigureDraft, index: int) -> Path:
    figures_dir = doc_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    path = figures_dir / f"p{draft.page_number}_{index}.{draft.image_ext}"
    path.write_bytes(draft.image_bytes)
    return path
