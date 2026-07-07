import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentList } from "./DocumentList";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("DocumentList", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify([
            {
              id: 1,
              course_id: 1,
              original_filename: "week1.pdf",
              original_format: "pdf",
              ingest_status: "ready",
              ingest_error: null,
              page_count: 3,
              created_at: "2026-01-01T00:00:00Z",
            },
            {
              id: 2,
              course_id: 1,
              original_filename: "week2.docx",
              original_format: "docx",
              ingest_status: "failed",
              ingest_error: "conversion failed",
              page_count: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders documents with status chips and a retry button for failed ones", async () => {
    renderWithClient(<DocumentList courseId={1} />);
    await waitFor(() => expect(screen.getByText("week1.pdf")).toBeInTheDocument());
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("week2.docx")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
