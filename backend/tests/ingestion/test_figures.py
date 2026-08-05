import io
from pathlib import Path

import fitz
from PIL import Image

from app.ingestion.figures import MAX_FIGURE_PAGE_COVERAGE, MIN_FIGURE_DIMENSION_PX, extract_figures, save_figure_image


def _make_pdf(tmp_path: Path, page_size: tuple[float, float], images: list[tuple[tuple[float, float, float, float], tuple[int, int]]]) -> Path:
    """images: list of (rect, (width_px, height_px))."""
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    for rect, (w, h) in images:
        img = Image.new("RGB", (w, h), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page.insert_image(fitz.Rect(*rect), stream=buf.getvalue())
    path = tmp_path / "test.pdf"
    doc.save(path)
    doc.close()
    return path


def test_extracts_a_real_embedded_figure(tmp_path):
    pdf_path = _make_pdf(tmp_path, (612, 792), [((50, 50, 350, 350), (300, 300))])

    drafts = extract_figures(pdf_path)

    assert len(drafts) == 1
    assert drafts[0].page_number == 1
    assert drafts[0].image_ext in ("png", "jpeg", "jpg")
    assert len(drafts[0].image_bytes) > 0
    assert drafts[0].bbox["page_width"] == 612
    assert drafts[0].bbox["page_height"] == 792


def test_filters_out_images_below_min_dimension(tmp_path):
    tiny = MIN_FIGURE_DIMENSION_PX - 10
    pdf_path = _make_pdf(tmp_path, (612, 792), [((50, 50, 50 + tiny, 50 + tiny), (tiny, tiny))])

    drafts = extract_figures(pdf_path)

    assert drafts == []


def test_filters_out_full_page_scanned_image(tmp_path):
    """A scanned page embeds the entire page as one image -- must not be
    indexed as a 'figure' (see the module docstring for why). Image pixel
    dimensions are kept proportional to the page rect so PyMuPDF places it
    without aspect-ratio letterboxing, which would otherwise shrink the
    placed bbox below the intended coverage ratio."""
    width, height = 612, 792
    coverage_rect = (0, 0, width, height)  # exactly the full page
    pdf_path = _make_pdf(tmp_path, (width, height), [(coverage_rect, (612, 792))])

    drafts = extract_figures(pdf_path)

    assert drafts == []
    assert MAX_FIGURE_PAGE_COVERAGE < 1.0  # sanity: the threshold below full coverage is what's being exercised


def test_keeps_a_figure_below_the_full_page_coverage_threshold(tmp_path):
    """Sanity check for the coverage filter's boundary: a figure that's
    large but clearly not the whole page must still be extracted."""
    width, height = 612, 792
    half_page_rect = (50, 50, width - 50, height / 2)
    pdf_path = _make_pdf(tmp_path, (width, height), [(half_page_rect, (500, 400))])

    drafts = extract_figures(pdf_path)

    assert len(drafts) == 1


def test_multiple_pages_and_multiple_figures_per_page(tmp_path):
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=612, height=792)
        for rect in [(50, 50, 350, 350), (400, 400, 580, 580)]:
            img = Image.new("RGB", (200, 200), color="red")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(*rect), stream=buf.getvalue())
    path = tmp_path / "multi.pdf"
    doc.save(path)
    doc.close()

    drafts = extract_figures(path)

    assert len(drafts) == 4
    assert sorted(d.page_number for d in drafts) == [1, 1, 2, 2]


def test_save_figure_image_writes_to_disk_under_figures_subdir(tmp_path):
    pdf_path = _make_pdf(tmp_path, (612, 792), [((50, 50, 350, 350), (300, 300))])
    drafts = extract_figures(pdf_path)
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()

    saved_path = save_figure_image(doc_dir, drafts[0], index=0)

    assert saved_path.exists()
    assert saved_path.parent.name == "figures"
    assert saved_path.read_bytes() == drafts[0].image_bytes
