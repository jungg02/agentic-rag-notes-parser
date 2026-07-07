import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CourseSelector } from "./CourseSelector";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("CourseSelector", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/courses") {
          return new Response(
            JSON.stringify([{ id: 1, name: "Biology", created_at: "2026-01-01T00:00:00Z", document_count: 2 }]),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        throw new Error(`Unexpected fetch to ${url}`);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders courses returned from the API", async () => {
    renderWithClient(<CourseSelector selectedCourseId={null} onSelect={() => {}} />);
    await waitFor(() => expect(screen.getByText("Biology (2)")).toBeInTheDocument());
  });

  it("calls onSelect when a course button is clicked", async () => {
    const onSelect = vi.fn();
    renderWithClient(<CourseSelector selectedCourseId={null} onSelect={onSelect} />);
    const button = await screen.findByText("Biology (2)");
    button.click();
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});
