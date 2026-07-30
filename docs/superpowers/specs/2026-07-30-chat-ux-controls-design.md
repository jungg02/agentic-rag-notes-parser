# Chat UX Controls — Design

**Date:** 2026-07-30
**Status:** Approved (pending spec review)

## Problem

Three small usability gaps in the frontend:

1. `MessageList` never auto-scrolls, so a user has to manually scroll down
   to see new messages or streaming assistant replies as they arrive.
2. There is no way to clear a chat's history from the UI. The backend
   already supports it (`DELETE /api/sessions/{id}`,
   `backend/app/routers/chat.py:78-84`) but nothing calls it.
3. There is no way to remove an uploaded document from the UI. The backend
   already supports it (`DELETE /api/documents/{id}`,
   `backend/app/routers/documents.py:110-118`, which also deletes the
   file's chunks via cascade and its on-disk directory) but nothing calls
   it either.

## Goal

Wire up three independent, frontend-only UI affordances. No backend
changes — both delete endpoints already exist and are exercised by
existing backend tests.

## 1. Autoscroll

**Behavior:** the message list scrolls to the newest content automatically
when the user is already near the bottom (within 80px). If the user has
scrolled up to reread earlier messages, new messages/streaming deltas do
not yank them back down.

**Where:** `frontend/src/components/chat/MessageList.tsx`. Self-contained —
no new props, no changes to `ChatPane`.

**Mechanics:**
- A ref on the scrollable `.message-list` container tracks scroll
  position via a native `scroll` event listener, updating a
  `isNearBottomRef` (not state — this doesn't need to trigger renders).
  "Near bottom" = `scrollHeight - scrollTop - clientHeight < 80`.
- A sentinel empty `<div ref={bottomRef} />` after the last message.
- A `useEffect` keyed on `messages` calls
  `bottomRef.current?.scrollIntoView({ block: "end" })` when
  `isNearBottomRef.current` is true. This effect runs both when a new
  message is appended (message count changes) and on every streaming delta
  (the `messages` array is a new reference — and its last message's
  `content` grows — on every delta in `ChatPane`'s `handleSend`).
- On mount (chat opened with existing history), default `isNearBottomRef`
  to `true` so the initial render lands scrolled to the bottom.

## 2. Clear Chat

**Behavior:** a "Clear chat" button deletes the current session outright
(no endpoint exists to wipe messages while keeping the session — deleting
the session is the only supported operation) and returns to the existing
"Start a new chat" empty state.

**Where:**
- `frontend/src/api/chat.ts`: add `useDeleteChatSession(courseId)`:
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
  **Deviation from the naive `invalidateQueries`-only version:** `invalidateQueries`
  only marks the query stale and triggers an async background refetch — it does not
  clear cached data synchronously. `ChatPane`'s "select first session" effect
  (`if (sessions.length > 0 && sessionId === null) setSessionId(sessions[0].id)`)
  runs on the very next render, before that refetch resolves, and would still see
  the stale cache still containing the just-deleted session — reselecting it and
  permanently undoing the reset (the later refetch resolving to the correct list
  no longer matters, since `sessionId` is non-null by then and the effect's guard
  no longer fires). `setQueryData` patches the cache synchronously, in the same
  tick as the mutation's success handling, so the reselect effect always sees a
  cache already missing the deleted session on the render where `sessionId` flips
  to `null`. `invalidateQueries` is kept alongside it purely as an eventual-consistency
  guard against server-side drift.
- `frontend/src/components/chat/ChatPane.tsx`: add a header row above
  `MessageList` containing a "Clear chat" button, rendered whenever
  `sessionId !== null`. Disabled while `isSending` (don't let a user
  delete a session mid-stream — the in-flight assistant reply would try to
  persist against a session that no longer exists).
  - `onClick`: `deleteSession.mutate(sessionId, { onSuccess: () => { setSessionId(null); setStreamingMessages([]); } })`.

## 3. Delete Uploaded Document

**Behavior:** a "Delete" button per document row, gated by a
`window.confirm` prompt naming the file, then removes it.

**Where:**
- `frontend/src/api/documents.ts`: add `useDeleteDocument(courseId)`:
  ```ts
  export function useDeleteDocument(courseId: number) {
    const queryClient = useQueryClient();
    return useMutation({
      mutationFn: (documentId: number) => apiFetch<void>(`/api/documents/${documentId}`, { method: "DELETE" }),
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
    });
  }
  ```
- `frontend/src/components/documents/DocumentList.tsx`: add a "Delete"
  button next to the existing status chip / Retry button on each row,
  visible regardless of `ingest_status` (unlike Retry, which only shows for
  `failed`). `onClick` calls
  `window.confirm(\`Delete ${doc.original_filename}? This cannot be undone.\`)`
  and only fires the mutation if confirmed.

## Non-goals

- No change to backend behavior — both delete endpoints already exist and
  are already covered by backend tests (`delete_document` cascades to
  chunks and removes the on-disk directory; `delete_session` cascades to
  messages/citations via the existing model relationships).
- No "undo" for either delete action.
- No confirmation step for Clear Chat (matches the existing pattern of
  irreversible-but-low-stakes actions in this app; only the file delete —
  which also destroys parsed content — gets a confirm per the approved
  design).
- No changes to scroll behavior/position restoration when switching
  sessions or courses.

## Testing

Following the existing component-test conventions (`DocumentList.test.tsx`,
`CourseSelector.test.tsx`: mock `global.fetch` per test with a
URL-dispatching `vi.fn`, wrap in a fresh `QueryClient` with
`retry: false`).

1. **`MessageList.test.tsx`**: a jsdom scroll-container test needs
   `Element.prototype.scrollIntoView` and layout properties
   (`scrollHeight`/`scrollTop`/`clientHeight`) stubbed, since jsdom doesn't
   implement layout. Assert: when the container reports "near bottom" and
   a new message is appended, `scrollIntoView` is called; when the
   container reports "scrolled up" (large `scrollHeight`, `scrollTop`
   near 0), appending a new message does not call `scrollIntoView`.
2. **`ChatPane.test.tsx`** (new file — none exists today): render with an
   existing session, click "Clear chat", assert the delete mutation fires
   with the right session id and the pane falls back to the "Start a new
   chat" prompt.
3. **`DocumentList.test.tsx`** (extend existing file): clicking "Delete"
   with `window.confirm` stubbed to return `true` calls
   `DELETE /api/documents/{id}`; stubbed to return `false`, no request is
   made.
