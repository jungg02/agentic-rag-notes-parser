from pathlib import Path

from app.ingestion.convert import convert_to_pdf


def test_convert_docx_to_pdf(fixtures_dir, tmp_path):
    input_path = Path(fixtures_dir) / "sample.docx"
    output = convert_to_pdf(input_path, tmp_path)
    assert output.exists()
    assert output.suffix == ".pdf"
    assert output.stat().st_size > 0
