import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface Citation {
  marker: number;
  chunk_id: number;
  document_id: number;
  filename: string;
  page_number: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
}

export interface ChatSession {
  id: number;
  course_id: number;
  title: string | null;
  created_at: string;
}

export function useChatSessions(courseId: number) {
  return useQuery({
    queryKey: ["chat-sessions", courseId],
    queryFn: () => apiFetch<ChatSession[]>(`/api/courses/${courseId}/sessions`),
  });
}

export function useCreateChatSession(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<ChatSession>(`/api/courses/${courseId}/sessions`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["chat-sessions", courseId] }),
  });
}

export function useChatMessages(sessionId: number | null) {
  return useQuery({
    queryKey: ["chat-messages", sessionId],
    queryFn: () => apiFetch<ChatMessage[]>(`/api/sessions/${sessionId}/messages`),
    enabled: sessionId !== null,
  });
}

export async function sendMessageStream(
  sessionId: number,
  content: string,
  onDelta: (text: string) => void,
  onDone: (data: { message_id: number; citations: Citation[] }) => void
): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.body) throw new Error("No response body for streaming message");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event: "));
      const dataLine = lines.find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const eventType = eventLine.slice("event: ".length);
      const data = JSON.parse(dataLine.slice("data: ".length));

      if (eventType === "delta") onDelta(data.text);
      if (eventType === "done") onDone(data);
    }
  }
}
