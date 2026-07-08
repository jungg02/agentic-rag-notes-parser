import type { ReactNode } from "react";

import type { ChatMessage } from "../../api/chat";
import { CitationChip } from "./CitationChip";
import "./MessageList.css";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenSource: (chunkId: number) => void;
}

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

export function MessageList({ messages, onOpenSource }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className="message-list">
        <p className="message-list-empty">Ask a question about your course materials to get started.</p>
      </div>
    );
  }

  return (
    <div className="message-list">
      {messages.map((message) => (
        <div key={message.id} className={`message message-${message.role}`}>
          {renderContentWithCitations(message, onOpenSource)}
        </div>
      ))}
    </div>
  );
}
