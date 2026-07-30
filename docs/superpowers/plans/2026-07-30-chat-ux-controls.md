# Chat UX Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three independent frontend-only UI affordances: auto-scroll the chat message list, a "Clear chat" button that deletes the current session, and a "Delete" button per uploaded document.

**Architecture:** All three ride on backend endpoints that already exist and are already tested (`DELETE /api/sessions/{id}` in `backend/app/routers/chat.py:78-84`, `DELETE /api/documents/{id}` in `backend/app/routers/documents.py:110-118`). No backend changes. Each task touches a different, disjoint set of frontend files, so the three tasks have no interfaces or types in common — they can be implemented and reviewed independently, in any order.

**Tech Stack:** React 18, TypeScript, Vitest, @testing-library/react, @tanstack/react-query.

## Global Constraints

- No backend changes anywhere in this plan.
- Near-bottom threshold for autoscroll: `scrollHeight - scrollTop - clientHeight < 80` (pixels).
- Clear Chat has no confirmation step (only the file-delete action gets one, per the approved design).
- Delete Document confirmation text, verbatim: `` `Delete ${filename}? This cannot be undone.` `` via `window.confirm`.
- Follow the existing test convention in this repo: mock `global.fetch` per test with a URL-dispatching `vi.fn`, wrap children needing query context in a fresh `QueryClient({ defaultOptions: { queries: { retry: false } } })` (see `frontend/src/components/courses/CourseSelector.test.tsx`).

---

### Task 1: Autoscroll in MessageList

**Files:**
- Modify: `frontend/src/components/chat/MessageList.tsx`
- Test: `frontend/src/components/chat/MessageList.test.tsx` (extend existing file)

**Interfaces:**
- Consumes: nothing new — `MessageListProps` (`messages: ChatMessage[]`, `onOpenSource`) is unchanged.
- Produces: nothing new consumed by other tasks — this task is self-contained.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `frontend/src/components/chat/MessageList.test.tsx` with:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../api/chat";
import { MessageList } from "./MessageList";

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
  }));
}

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

describe("MessageList autoscroll", () => {
  const scrollIntoViewMock = vi.fn();

  beforeEach(() => {
    scrollIntoViewMock.mockClear();
    Element.prototype.scrollIntoView = scrollIntoViewMock;
  });

  it("scrolls to bottom when a new message arrives while near the bottom", () => {
    const { container, rerender } = render(<MessageList messages={makeMessages(1)} onOpenSource={() => {}} />);
    const listEl = container.querySelector(".message-list") as HTMLElement;
    setLayout(listEl, { scrollHeight: 500, scrollTop: 450, clientHeight: 100 });
    fireEvent.scroll(listEl);
    scrollIntoViewMock.mockClear();

    rerender(<MessageList messages={makeMessages(2)} onOpenSource={() => {}} />);

    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it("does not scroll when the user has scrolled up away from the bottom", () => {
    const { container, rerender } = render(<MessageList messages={makeMessages(1)} onOpenSource={() => {}} />);
    const listEl = container.querySelector(".message-list") as HTMLElement;
    setLayout(listEl, { scrollHeight: 1000, scrollTop: 0, clientHeight: 200 });
    fireEvent.scroll(listEl);
    scrollIntoViewMock.mockClear();

    rerender(<MessageList messages={makeMessages(2)} onOpenSource={() => {}} />);

    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `cd frontend && npx vitest run src/components/chat/MessageList.test.tsx`
Expected: the two pre-existing tests in `describe("MessageList", ...)` PASS unchanged; the two new tests in `describe("MessageList autoscroll", ...)` FAIL — `scrollIntoViewMock` is never called because `MessageList` has no scroll/ref logic yet.

- [ ] **Step 3: Implement autoscroll in `frontend/src/components/chat/MessageList.tsx`**

Replace the full file contents with:

```tsx
import { useEffect, useRef, type ReactNode } from "react";

import type { ChatMessage } from "../../api/chat";
import { CitationChip } from "./CitationChip";
import "./MessageList.css";

interface MessageListProps {
  messages: ChatMessage[];
  onOpenSource: (chunkId: number) => void;
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

export function MessageList({ messages, onOpenSource }: MessageListProps) {
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
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/chat/MessageList.test.tsx`
Expected: PASS (4/4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/MessageList.tsx frontend/src/components/chat/MessageList.test.tsx
git commit -m "$(cat <<'EOF'
feat: auto-scroll chat message list when near the bottom

Tracks scroll position via a scroll listener and only snaps to the
newest message when the user hasn't scrolled up to reread history.
EOF
)"
```

---

### Task 2: Clear Chat button

**Files:**
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/components/chat/ChatPane.tsx`
- Modify: `frontend/src/components/chat/ChatPane.css`
- Test: `frontend/src/components/chat/ChatPane.test.tsx` (new file)

**Interfaces:**
- Consumes: `apiFetch<T>` from `frontend/src/api/client.ts` (existing).
- Produces: `useDeleteChatSession(courseId: number)` — a `useMutation` whose `mutate`/`mutateAsync` takes a `sessionId: number` and resolves `void`. Not consumed by any other task in this plan.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/ChatPane.test.tsx`:

```tsx
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
    renderWithClient(<ChatPane courseId={1} onOpenSource={() => {}} />);

    await waitFor(() => expect(screen.getByText("Hi there")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Clear chat"));

    await waitFor(() => expect(screen.getByText("Start a new chat")).toBeInTheDocument());
    expect(screen.queryByText("Hi there")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/chat/ChatPane.test.tsx`
Expected: FAIL — `screen.getByText("Clear chat")` throws because no such button exists yet.

- [ ] **Step 3: Add `useDeleteChatSession` to `frontend/src/api/chat.ts`**

Add this function (place it after `useCreateChatSession`, before `useChatMessages`):

```ts
export function useDeleteChatSession(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: number) => apiFetch<void>(`/api/sessions/${sessionId}`, { method: "DELETE" }),
    onSuccess: (_data, deletedSessionId) => {
      queryClient.setQueryData<ChatSession[]>(["chat-sessions", courseId], (old) =>
        (old ?? []).filter((session) => session.id !== deletedSessionId)
      );
      queryClient.invalidateQueries({ queryKey: ["chat-sessions", courseId] });
    },
  });
}
```

`setQueryData` must run before `invalidateQueries`: it patches the cache
synchronously (same tick as the mutation's success handling), removing the
deleted session before `ChatPane`'s "select first session" effect
(`if (sessions.length > 0 && sessionId === null) setSessionId(sessions[0].id)`)
can run on the next render. `invalidateQueries` alone would leave the stale
cached list (still containing the deleted session) in place until its async
refetch resolves — which happens *after* the effect has already reselected
the deleted session and set `sessionId` back to non-null, permanently
defeating the reset. `invalidateQueries` is kept alongside `setQueryData`
purely as an eventual-consistency guard against server-side drift, not as
the mechanism the reset actually depends on.

- [ ] **Step 4: Wire the button into `frontend/src/components/chat/ChatPane.tsx`**

Change the import line:

```tsx
import { sendMessageStream, useChatMessages, useChatSessions, useCreateChatSession } from "../../api/chat";
```

to:

```tsx
import {
  sendMessageStream,
  useChatMessages,
  useChatSessions,
  useCreateChatSession,
  useDeleteChatSession,
} from "../../api/chat";
```

Add the mutation hook and a handler, right after the existing `createSession` line:

```tsx
  const createSession = useCreateChatSession(courseId);
  const deleteSession = useDeleteChatSession(courseId);
```

Add this handler next to `handleStartSession`:

```tsx
  const handleClearChat = () => {
    if (sessionId === null) return;
    deleteSession.mutate(sessionId, {
      onSuccess: () => {
        setSessionId(null);
        setStreamingMessages([]);
      },
    });
  };
```

Change the final `return` block from:

```tsx
  return (
    <div className="chat-pane">
      <MessageList messages={allMessages} onOpenSource={onOpenSource} />
      <ChatInput onSend={handleSend} disabled={isSending} />
    </div>
  );
```

to:

```tsx
  return (
    <div className="chat-pane">
      <div className="chat-pane-header">
        <button className="chat-pane-clear" onClick={handleClearChat} disabled={isSending}>
          Clear chat
        </button>
      </div>
      <MessageList messages={allMessages} onOpenSource={onOpenSource} />
      <ChatInput onSend={handleSend} disabled={isSending} />
    </div>
  );
```

- [ ] **Step 5: Add styles to `frontend/src/components/chat/ChatPane.css`**

Append:

```css
.chat-pane-header {
  display: flex;
  justify-content: flex-end;
  padding: var(--space-3) var(--space-5) 0;
}

.chat-pane-clear {
  font-size: var(--text-xs);
  padding: 0.35em 0.7em;
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/chat/ChatPane.test.tsx`
Expected: PASS (1/1 test).

- [ ] **Step 7: Run the full frontend suite to check for regressions**

Run: `cd frontend && npx vitest run`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/chat.ts frontend/src/components/chat/ChatPane.tsx frontend/src/components/chat/ChatPane.css frontend/src/components/chat/ChatPane.test.tsx
git commit -m "$(cat <<'EOF'
feat: add Clear chat button to delete the current session

No endpoint exists to wipe messages while keeping a session, so Clear
chat deletes the session outright and falls back to the existing
start-a-new-chat empty state.
EOF
)"
```

---

### Task 3: Delete uploaded document button

**Files:**
- Modify: `frontend/src/api/documents.ts`
- Modify: `frontend/src/components/documents/DocumentList.tsx`
- Modify: `frontend/src/components/documents/DocumentList.css`
- Test: `frontend/src/components/documents/DocumentList.test.tsx` (extend existing file)

**Interfaces:**
- Consumes: `apiFetch<T>` from `frontend/src/api/client.ts` (existing).
- Produces: `useDeleteDocument(courseId: number)` — a `useMutation` whose `mutate`/`mutateAsync` takes a `documentId: number` and resolves `void`. Not consumed by any other task in this plan.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `frontend/src/components/documents/DocumentList.test.tsx` with:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentList } from "./DocumentList";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("DocumentList", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify([
            {
              id: 1,
              course_id: 1,
              original_filename: "week1.pdf",
              original_format: "pdf",
              ingest_status: "ready",
              ingest_error: null,
              page_count: 3,
              created_at: "2026-01-01T00:00:00Z",
            },
            {
              id: 2,
              course_id: 1,
              original_filename: "week2.docx",
              original_format: "docx",
              ingest_status: "failed",
              ingest_error: "conversion failed",
              page_count: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders documents with status chips and a retry button for failed ones", async () => {
    renderWithClient(<DocumentList courseId={1} />);
    await waitFor(() => expect(screen.getByText("week1.pdf")).toBeInTheDocument());
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("week2.docx")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});

describe("DocumentList delete", () => {
  let deleted = false;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    deleted = false;
    fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url === "/api/courses/1/documents") {
        return new Response(
          JSON.stringify(
            deleted
              ? []
              : [
                  {
                    id: 1,
                    course_id: 1,
                    original_filename: "week1.pdf",
                    original_format: "pdf",
                    ingest_status: "ready",
                    ingest_error: null,
                    page_count: 3,
                    created_at: "2026-01-01T00:00:00Z",
                  },
                ]
          ),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url === "/api/documents/1" && options?.method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unexpected fetch to ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("deletes the document when the user confirms", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithClient(<DocumentList courseId={1} />);
    await waitFor(() => expect(screen.getByText("week1.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Delete"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/documents/1", expect.objectContaining({ method: "DELETE" }))
    );
    await waitFor(() => expect(screen.queryByText("week1.pdf")).not.toBeInTheDocument());
  });

  it("does not delete when the user cancels the confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithClient(<DocumentList courseId={1} />);
    await waitFor(() => expect(screen.getByText("week1.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Delete"));

    expect(window.confirm).toHaveBeenCalledWith("Delete week1.pdf? This cannot be undone.");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/documents/1", expect.objectContaining({ method: "DELETE" }));
    expect(screen.getByText("week1.pdf")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `cd frontend && npx vitest run src/components/documents/DocumentList.test.tsx`
Expected: the pre-existing "renders documents..." test PASSES unchanged; both new tests in `describe("DocumentList delete", ...)` FAIL — `screen.getByText("Delete")` throws because no such button exists yet.

- [ ] **Step 3: Add `useDeleteDocument` to `frontend/src/api/documents.ts`**

Add this function at the end of the file:

```ts
export function useDeleteDocument(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => apiFetch<void>(`/api/documents/${documentId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });
}
```

- [ ] **Step 4: Wire the button into `frontend/src/components/documents/DocumentList.tsx`**

Replace the full file contents with:

```tsx
import { useDeleteDocument, useDocuments, useRetryDocument } from "../../api/documents";
import "./DocumentList.css";

interface DocumentListProps {
  courseId: number;
}

export function DocumentList({ courseId }: DocumentListProps) {
  const { data: documents, isLoading } = useDocuments(courseId);
  const retry = useRetryDocument(courseId);
  const deleteDocument = useDeleteDocument(courseId);

  const handleDelete = (documentId: number, filename: string) => {
    if (!window.confirm(`Delete ${filename}? This cannot be undone.`)) return;
    deleteDocument.mutate(documentId);
  };

  return (
    <div className="document-list">
      <h2 className="panel-heading">Documents</h2>
      {isLoading ? (
        <p className="document-list-status">Loading documents...</p>
      ) : (documents ?? []).length === 0 ? (
        <p className="document-list-empty">No documents yet. Upload your notes above to get started.</p>
      ) : (
        <ul className="document-items">
          {(documents ?? []).map((doc) => (
            <li key={doc.id} className="document-item">
              <span className="document-name">{doc.original_filename}</span>
              <span className={`status-chip status-${doc.ingest_status}`}>{doc.ingest_status}</span>
              {doc.ingest_status === "failed" && (
                <button className="document-retry" onClick={() => retry.mutate(doc.id)}>
                  Retry
                </button>
              )}
              <button className="document-delete" onClick={() => handleDelete(doc.id, doc.original_filename)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Add styles to `frontend/src/components/documents/DocumentList.css`**

Append:

```css
.document-delete {
  flex-shrink: 0;
  font-size: var(--text-xs);
  padding: 0.35em 0.7em;
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/documents/DocumentList.test.tsx`
Expected: PASS (3/3 tests).

- [ ] **Step 7: Run the full frontend suite to check for regressions**

Run: `cd frontend && npx vitest run`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/documents.ts frontend/src/components/documents/DocumentList.tsx frontend/src/components/documents/DocumentList.css frontend/src/components/documents/DocumentList.test.tsx
git commit -m "$(cat <<'EOF'
feat: add Delete button for uploaded documents

Confirms via window.confirm before calling the existing
DELETE /api/documents/{id} endpoint, which already cascades to the
document's chunks and removes its on-disk directory.
EOF
)"
```

## Self-Review Notes

- **Spec coverage:** all three design sections (Autoscroll, Clear Chat, Delete Uploaded Document) map 1:1 to Tasks 1-3. The design's Testing section items 1-3 are each covered by the corresponding task's test steps.
- **Placeholder scan:** no TBDs; every step has complete code or exact commands.
- **Type consistency:** `useDeleteChatSession(courseId: number)` returns a mutation taking `sessionId: number` (Task 2); `useDeleteDocument(courseId: number)` returns a mutation taking `documentId: number` (Task 3) — distinct names, no cross-task confusion. Neither is consumed outside its own task, matching the plan's Architecture note that the three tasks share no interfaces.
