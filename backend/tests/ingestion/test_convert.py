import shutil
import sys

import pytest
from pathlib import Path

from app.ingestion.convert import convert_to_pdf


def test_convert_docx_to_pdf(fixtures_dir, tmp_path):
    input_path = Path(fixtures_dir) / "sample.docx"
    output = convert_to_pdf(input_path, tmp_path)
    assert output.exists()
    assert output.suffix == ".pdf"
    assert output.stat().st_size > 0


@pytest.mark.skipif(sys.platform != "win32", reason="drive-relative path handling is Windows-specific")
def test_convert_handles_drive_relative_input_path(fixtures_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copy(Path(fixtures_dir) / "sample.docx", tmp_path / "original.docx")

    # A "rooted but driveless" path, e.g. "\Users\...\original.docx" - the
    # same shape produced by joining the "/data/files" config default with
    # a subpath on Windows. Python's own file APIs resolve this against the
    # current drive transparently, but passing the raw string to soffice
    # (a separate process) does not - regression test for that gap.
    driveless_input = Path(str(tmp_path)[2:]) / "original.docx"

    output = convert_to_pdf(driveless_input, tmp_path)
    assert output.exists()
    assert output.suffix == ".pdf"
    assert output.stat().st_size > 0
