import { useEffect, useRef, type ReactNode } from "react";

import type { ChatMessage, RelatedFigure } from "../../api/chat";
import { CitationChip } from "./CitationChip";
import "./MessageList.css";
import { RelatedFigures } from "./RelatedFigures";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenSource: (chunkId: number) => void;
  relatedFiguresByMessageId: Record<number, RelatedFigure[]>;
  onOpenFigure: (figure: RelatedFigure) => void;
}

const NEAR_BOTTOM_THRESHOLD_PX = 80;

function renderContentWithCitations(message: ChatMessage, onOpenSource: (chunkId: number) => void): ReactNode[] {
  const citationsByMarker = new Map(message.citations.map((c) => [c.marker, c]));
  const parts = message.content.split(/(\[\d+\])/g);

  return parts.map((part, index) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const marker = Number(match[1]);
      const citation = citationsByMarker.get(marker);
      if (citation) {
        return <CitationChip key={index} citation={citation} onOpenSource={onOpenSource} />;
      }
    }
    return <span key={index}>{part}</span>;
  });
}

export function MessageList({ messages, onOpenSource, relatedFiguresByMessageId, onOpenFigure }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef(true);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD_PX;
  };

  useEffect(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="message-list">
        <p className="message-list-empty">Ask a question about your course materials to get started.</p>
      </div>
    );
  }

  return (
    <div className="message-list" ref={containerRef} onScroll={handleScroll}>
      {messages.map((message) => (
        <div key={message.id} className={`message message-${message.role}`}>
          {renderContentWithCitations(message, onOpenSource)}
          {message.rewritten_query && message.rewritten_query !== message.content && (
            <div className="message-rewritten-query">Interpreted as: "{message.rewritten_query}"</div>
          )}
          {message.role === "assistant" && (
            <RelatedFigures figures={relatedFiguresByMessageId[message.id] ?? []} onOpenFigure={onOpenFigure} />
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
