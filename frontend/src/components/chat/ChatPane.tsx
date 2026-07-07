import { useEffect, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type { ChatMessage } from "../../api/chat";
import { sendMessageStream, useChatMessages, useChatSessions, useCreateChatSession } from "../../api/chat";
import { ChatInput } from "./ChatInput";
import { MessageList } from "./MessageList";

interface ChatPaneProps {
  courseId: number;
  onOpenSource: (chunkId: number) => void;
}

export function ChatPane({ courseId, onOpenSource }: ChatPaneProps) {
  const { data: sessions } = useChatSessions(courseId);
  const createSession = useCreateChatSession(courseId);
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

  const handleSend = async (content: string) => {
    if (sessionId === null) return;
    setIsSending(true);

    const userMessage: ChatMessage = {
      id: -1,
      role: "user",
      content,
      created_at: new Date().toISOString(),
      citations: [],
    };
    const assistantDraft: ChatMessage = {
      id: -2,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      citations: [],
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
          return [user, { ...assistant, id: data.message_id, citations: data.citations }];
        });
        queryClient.invalidateQueries({ queryKey: ["chat-messages", sessionId] });
        setIsSending(false);
      }
    );
  };

  if (sessionId === null) {
    return (
      <div className="chat-pane">
        <button onClick={handleStartSession}>Start a new chat</button>
      </div>
    );
  }

  const allMessages = [...(persistedMessages ?? []), ...(isSending ? streamingMessages : [])];

  return (
    <div className="chat-pane">
      <MessageList messages={allMessages} onOpenSource={onOpenSource} />
      <ChatInput onSend={handleSend} disabled={isSending} />
    </div>
  );
}
