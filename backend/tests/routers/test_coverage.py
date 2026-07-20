from app.routers.coverage import _document_coverage


def test_document_coverage_ready_partial():
    result = _document_coverage(
        document_id=7,
        filename="lecture03.pptx",
        ingest_status="ready",
        ingest_error=None,
        page_count=5,
        present_pages={1, 2, 4},
        chunk_count=9,
        token_sum=1800,
    )
    assert result["pages_with_text"] == 3
    assert result["dropped_pages"] == [3, 5]
    assert result["coverage_pct"] == 60.0
    assert result["chunks"] == 9
    assert result["tokens"] == 1800
    assert result["ingest_error"] is None


def test_document_coverage_ready_full():
    result = _document_coverage(
        document_id=8,
        filename="clean.pdf",
        ingest_status="ready",
        ingest_error=None,
        page_count=3,
        present_pages={1, 2, 3},
        chunk_count=6,
        token_sum=1200,
    )
    assert result["dropped_pages"] == []
    assert result["coverage_pct"] == 100.0
    assert result["pages_with_text"] == 3


def test_document_coverage_failed_reports_nulls_and_error():
    result = _document_coverage(
        document_id=9,
        filename="broken.pptx",
        ingest_status="failed",
        ingest_error="Unexpected error: boom",
        page_count=None,
        present_pages=set(),
        chunk_count=0,
        token_sum=0,
    )
    assert result["page_count"] is None
    assert result["pages_with_text"] is None
    assert result["coverage_pct"] is None
    assert result["dropped_pages"] is None
    assert result["ingest_status"] == "failed"
    assert result["ingest_error"] == "Unexpected error: boom"
