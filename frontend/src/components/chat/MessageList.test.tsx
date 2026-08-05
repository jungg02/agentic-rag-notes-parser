import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, RelatedFigure } from "../../api/chat";
import { MessageList } from "./MessageList";

const noFigures: Record<number, RelatedFigure[]> = {};
const noopOpenFigure = () => {};

function setLayout(
  el: HTMLElement,
  { scrollHeight, scrollTop, clientHeight }: { scrollHeight: number; scrollTop: number; clientHeight: number }
) {
  Object.defineProperty(el, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(el, "scrollTop", { value: scrollTop, configurable: true, writable: true });
  Object.defineProperty(el, "clientHeight", { value: clientHeight, configurable: true });
}

function makeMessages(count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Message ${i + 1}`,
    created_at: "2026-01-01T00:00:00Z",
    citations: [],
    rewritten_query: null,
  }));
}

const scrollIntoViewMock = vi.fn();

beforeEach(() => {
  scrollIntoViewMock.mockClear();
  Element.prototype.scrollIntoView = scrollIntoViewMock;
});

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
        rewritten_query: null,
      },
    ];

    render(<MessageList messages={messages} onOpenSource={onOpenSource} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);

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
        rewritten_query: null,
      },
    ];
    render(<MessageList messages={messages} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);
    expect(screen.getByText("[9]", { exact: false })).toBeInTheDocument();
  });

  it("shows the rewritten query when it differs from the original message", () => {
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "user",
        content: "Which one handles typos better?",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
        rewritten_query: "Does BM25 or dense retrieval handle typos better?",
      },
    ];
    render(<MessageList messages={messages} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);
    expect(screen.getByText(/Does BM25 or dense retrieval handle typos better\?/)).toBeInTheDocument();
  });

  it("does not show a rewritten-query note when there was no rewrite", () => {
    const messages: ChatMessage[] = [
      {
        id: 1,
        role: "user",
        content: "What is BM25?",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
        rewritten_query: null,
      },
    ];
    render(<MessageList messages={messages} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);
    expect(screen.queryByText(/Interpreted as/)).not.toBeInTheDocument();
  });

  it("renders related figures for an assistant message and opens one on click", () => {
    const onOpenFigure = vi.fn();
    const messages: ChatMessage[] = [
      {
        id: 7,
        role: "assistant",
        content: "The skip argument tells R to skip rows.",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
        rewritten_query: null,
      },
    ];
    const figures: Record<number, RelatedFigure[]> = {
      7: [{ figure_id: 42, document_id: 3, filename: "Week3_slides.pdf", page_number: 31 }],
    };

    render(
      <MessageList messages={messages} onOpenSource={() => {}} relatedFiguresByMessageId={figures} onOpenFigure={onOpenFigure} />
    );

    const thumb = screen.getByTitle("Week3_slides.pdf, page 31");
    thumb.click();
    expect(onOpenFigure).toHaveBeenCalledWith(figures[7][0]);
  });

  it("does not render a related-figures row for a user message even if present in the map", () => {
    const messages: ChatMessage[] = [
      {
        id: 8,
        role: "user",
        content: "What does skip do?",
        created_at: "2026-01-01T00:00:00Z",
        citations: [],
        rewritten_query: null,
      },
    ];
    const figures: Record<number, RelatedFigure[]> = {
      8: [{ figure_id: 42, document_id: 3, filename: "Week3_slides.pdf", page_number: 31 }],
    };
    render(<MessageList messages={messages} onOpenSource={() => {}} relatedFiguresByMessageId={figures} onOpenFigure={() => {}} />);
    expect(screen.queryByTitle("Week3_slides.pdf, page 31")).not.toBeInTheDocument();
  });
});

describe("MessageList autoscroll", () => {
  it("scrolls to bottom when a new message arrives while near the bottom", () => {
    const { container, rerender } = render(<MessageList messages={makeMessages(1)} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);
    const listEl = container.querySelector(".message-list") as HTMLElement;
    setLayout(listEl, { scrollHeight: 500, scrollTop: 450, clientHeight: 100 });
    fireEvent.scroll(listEl);
    scrollIntoViewMock.mockClear();

    rerender(<MessageList messages={makeMessages(2)} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);

    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it("does not scroll when the user has scrolled up away from the bottom", () => {
    const { container, rerender } = render(<MessageList messages={makeMessages(1)} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);
    const listEl = container.querySelector(".message-list") as HTMLElement;
    setLayout(listEl, { scrollHeight: 1000, scrollTop: 0, clientHeight: 200 });
    fireEvent.scroll(listEl);
    scrollIntoViewMock.mockClear();

    rerender(<MessageList messages={makeMessages(2)} onOpenSource={() => {}} relatedFiguresByMessageId={noFigures} onOpenFigure={noopOpenFigure} />);

    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });
});
