import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/courses") {
        return jsonResponse([
          { id: 1, name: "Investment and Finance", created_at: "2026-01-01T00:00:00Z", document_count: 0 },
          { id: 2, name: "ST3131", created_at: "2026-01-01T00:00:00Z", document_count: 0 },
        ]);
      }
      if (url === "/api/courses/1/sessions") {
        return jsonResponse([{ id: 10, course_id: 1, title: null, created_at: "2026-01-01T00:00:00Z" }]);
      }
      if (url === "/api/courses/2/sessions") {
        return jsonResponse([]);
      }
      if (url === "/api/courses/1/documents" || url === "/api/courses/2/documents") {
        return jsonResponse([]);
      }
      if (url === "/api/sessions/10/messages") {
        return jsonResponse([
          {
            id: 100,
            role: "user",
            content: "Hello from Investment and Finance",
            created_at: "2026-01-01T00:00:00Z",
            citations: [],
            related_figures: [],
            rewritten_query: null,
          },
          {
            id: 101,
            role: "assistant",
            content: "Here's a diagram.",
            created_at: "2026-01-01T00:00:00Z",
            citations: [],
            related_figures: [{ figure_id: 9, document_id: 5, filename: "Week1.pdf", page_number: 2 }],
            rewritten_query: null,
          },
        ]);
      }
      throw new Error(`Unexpected fetch to ${url}`);
    })
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the app title", () => {
    render(<App />);
    expect(screen.getByText("Multi-Turn Hybrid Retrieval System with Agentic Memory")).toBeInTheDocument();
  });
});

describe("App course switching", () => {
  it("shows the new course's own chat instead of the previous course's session", async () => {
    render(<App />);

    const investmentButton = await screen.findByText("Investment and Finance (0)");
    fireEvent.click(investmentButton);
    await waitFor(() => expect(screen.getByText("Hello from Investment and Finance")).toBeInTheDocument());

    const st3131Button = await screen.findByText("ST3131 (0)");
    fireEvent.click(st3131Button);

    await waitFor(() => expect(screen.getByText("Start a new chat")).toBeInTheDocument());
    expect(screen.queryByText("Hello from Investment and Finance")).not.toBeInTheDocument();
  });

  it("keeps a message's related figures visible after switching away and back", async () => {
    render(<App />);

    const investmentButton = await screen.findByText("Investment and Finance (0)");
    fireEvent.click(investmentButton);
    await waitFor(() => expect(screen.getByTitle("Week1.pdf, page 2")).toBeInTheDocument());

    const st3131Button = await screen.findByText("ST3131 (0)");
    fireEvent.click(st3131Button);
    await waitFor(() => expect(screen.getByText("Start a new chat")).toBeInTheDocument());

    fireEvent.click(investmentButton);
    await waitFor(() => expect(screen.getByTitle("Week1.pdf, page 2")).toBeInTheDocument());
  });
});
