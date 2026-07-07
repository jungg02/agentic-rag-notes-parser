import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../api/chat";
import { MessageList } from "./MessageList";

describe("MessageList", () => {
  it("replaces [n] markers with clickable citation chips", () => {
    const onOpenSource = vi.fn();
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "assistant",
        content: "Mitochondria produce ATP [1].",
        created_at: "2026-01-01T00:00:00Z",
        citations: [{ marker: 1, chunk_id: 5, document_id: 2, filename: "notes.pdf", page_number: 1 }],
      },
    ];

    render(<MessageList messages={messages} onOpenSource={onOpenSource} />);

    expect(screen.getByText("Mitochondria produce ATP", { exact: false })).toBeInTheDocument();
    const chip = screen.getByText("[1]");
    chip.click();
    expect(onOpenSource).toHaveBeenCalledWith(5);
  });

  it("renders plain text markers with no matching citation as-is", () => {
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "assistant",
        content: "This has an unresolved marker [9].",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
      },
    ];
    render(<MessageList messages={messages} onOpenSource={() => {}} />);
    expect(screen.getByText("[9]", { exact: false })).toBeInTheDocument();
  });
});
