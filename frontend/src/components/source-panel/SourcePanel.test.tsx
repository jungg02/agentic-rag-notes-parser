import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SourcePanel } from "./SourcePanel";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({
      getPage: () =>
        Promise.resolve({
          getViewport: () => ({ width: 100, height: 100 }),
          render: () => ({ promise: Promise.resolve() }),
        }),
    }),
  }),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("SourcePanel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            chunk_id: 5,
            document_id: 2,
            filename: "notes.pdf",
            pdf_url: "/api/documents/2/pdf",
            page_number: 3,
            bboxes: { page_width: 612, page_height: 792, rects: [] },
            text: "Some text",
            context_header: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when chunkId is null", () => {
    const { container } = renderWithClient(<SourcePanel chunkId={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("fetches and displays the filename and page number", async () => {
    renderWithClient(<SourcePanel chunkId={5} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("notes.pdf — page 3")).toBeInTheDocument());
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    renderWithClient(<SourcePanel chunkId={5} onClose={onClose} />);
    await waitFor(() => screen.getByLabelText("Close source panel"));
    screen.getByLabelText("Close source panel").click();
    expect(onClose).toHaveBeenCalled();
  });
});
