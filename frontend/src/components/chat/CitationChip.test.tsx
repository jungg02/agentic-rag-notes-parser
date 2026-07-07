import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CitationChip } from "./CitationChip";

describe("CitationChip", () => {
  it("renders the marker number and calls onOpenSource with the chunk id", () => {
    const onOpenSource = vi.fn();
    render(
      <CitationChip
        citation={{ marker: 1, chunk_id: 42, document_id: 7, filename: "week1.pdf", page_number: 3 }}
        onOpenSource={onOpenSource}
      />
    );
    const chip = screen.getByText("[1]");
    chip.click();
    expect(onOpenSource).toHaveBeenCalledWith(42);
  });
});
