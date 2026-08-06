import { useEffect, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type { ChatMessage, RelatedFigure } from "../../api/chat";
import {
  sendMessageStream,
  useChatMessages,
  useChatSessions,
  useCreateChatSession,
  useDeleteChatSession,
} from "../../api/chat";
import { ChatInput } from "./ChatInput";
import "./ChatPane.css";
import { MessageList } from "./MessageList";

interface ChatPaneProps {
  courseId: number;
  onOpenSource: (chunkId: number) => void;
  onOpenFigure: (figure: RelatedFigure) => void;
}

export function ChatPane({ courseId, onOpenSource, onOpenFigure }: ChatPaneProps) {
  const { data: sessions } = useChatSessions(courseId);
  const createSession = useCreateChatSession(courseId);
  const deleteSession = useDeleteChatSession(courseId);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const { data: persistedMessages } = useChatMessages(sessionId);
  const [streamingMessages, setStreamingMessages] = useState<ChatMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (sessions && sessions.length > 0 && sessionId === null) {
      setSessionId(sessions[0].id);
    }
  }, [sessions, sessionId]);

  const handleStartSession = () => {
    createSession.mutate(undefined, { onSuccess: (session) => setSessionId(session.id) });
  };

  const handleClearChat = () => {
    if (sessionId === null) return;
    deleteSession.mutate(sessionId, {
      onSuccess: () => {
        setSessionId(null);
        setStreamingMessages([]);
      },
    });
  };

  const handleSend = async (content: string) => {
    if (sessionId === null) return;
    setIsSending(true);

    const userMessage: ChatMessage = {
      id: -1,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      citations: [],
      related_figures: [],
      rewritten_query: null,
    };
    const assistantDraft: ChatMessage = {
      id: -2,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      citations: [],
      related_figures: [],
      rewritten_query: null,
    };
    setStreamingMessages([userMessage, assistantDraft]);

    await sendMessageStream(
      sessionId,
      content,
      (delta) => {
        setStreamingMessages((prev) => {
          const [user, assistant] = prev;
          return [user, { ...assistant, content: assistant.content + delta }];
        });
      },
      (data) => {
        setStreamingMessages((prev) => {
          const [user, assistant] = prev;
          return [
            { ...user, rewritten_query: data.rewritten_query },
            { ...assistant, id: data.message_id, citations: data.citations, related_figures: data.related_figures },
          ];
        });
        queryClient.invalidateQueries({ queryKey: ["chat-messages", sessionId] });
        setIsSending(false);
      }
    );
  };

  if (sessionId === null) {
    return (
      <div className="chat-pane">
        <div className="chat-pane-start">
          <p>Ask questions about this course's materials and get answers cited back to the source page.</p>
          <button className="btn-primary" onClick={handleStartSession}>
            Start a new chat
          </button>
        </div>
      </div>
    );
  }

  const allMessages = [...(persistedMessages ?? []), ...(isSending ? streamingMessages : [])];

  return (
    <div className="chat-pane">
      <div className="chat-pane-header">
        <button className="chat-pane-clear" onClick={handleClearChat} disabled={isSending}>
          Clear chat
        </button>
      </div>
      <MessageList messages={allMessages} onOpenSource={onOpenSource} onOpenFigure={onOpenFigure} />
      <ChatInput onSend={handleSend} disabled={isSending} />
    </div>
  );
}
