import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPane } from "./ChatPane";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ChatPane clear chat", () => {
  let sessionDeleted = false;

  beforeEach(() => {
    sessionDeleted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options?: RequestInit) => {
        if (url === "/api/courses/1/sessions") {
          return jsonResponse(
            sessionDeleted ? [] : [{ id: 5, course_id: 1, title: null, created_at: "2026-01-01T00:00:00Z" }]
          );
        }
        if (url === "/api/sessions/5/messages") {
          return jsonResponse([
            { id: 100, role: "assistant", content: "Hi there", created_at: "2026-01-01T00:00:00Z", citations: [] },
          ]);
        }
        if (url === "/api/sessions/5" && options?.method === "DELETE") {
          sessionDeleted = true;
          return new Response(null, { status: 204 });
        }
        throw new Error(`Unexpected fetch to ${url}`);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("deletes the session and returns to the start-chat prompt when Clear chat is clicked", async () => {
    renderWithClient(<ChatPane courseId={1} onOpenSource={() => {}} onOpenFigure={() => {}} />);

    await waitFor(() => expect(screen.getByText("Hi there")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Clear chat"));

    await waitFor(() => expect(screen.getByText("Start a new chat")).toBeInTheDocument());
    expect(screen.queryByText("Hi there")).not.toBeInTheDocument();
  });
});
