def _document_coverage(
    *,
    document_id: int,
    filename: str,
    ingest_status: str,
    ingest_error: str | None,
    page_count: int | None,
    present_pages: set[int],
    chunk_count: int,
    token_sum: int,
) -> dict:
    if ingest_status == "ready" and page_count is not None:
        present = {p for p in present_pages if 1 <= p <= page_count}
        pages_with_text = len(present)
        dropped_pages = [p for p in range(1, page_count + 1) if p not in present]
        coverage_pct = round(pages_with_text / page_count * 100, 1) if page_count else 0.0
        return {
            "document_id": document_id,
            "filename": filename,
            "ingest_status": ingest_status,
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "coverage_pct": coverage_pct,
            "dropped_pages": dropped_pages,
            "chunks": chunk_count,
            "tokens": token_sum,
            "ingest_error": ingest_error,
        }
    return {
        "document_id": document_id,
        "filename": filename,
        "ingest_status": ingest_status,
        "page_count": None,
        "pages_with_text": None,
        "coverage_pct": None,
        "dropped_pages": None,
        "chunks": chunk_count,
        "tokens": token_sum,
        "ingest_error": ingest_error,
    }
