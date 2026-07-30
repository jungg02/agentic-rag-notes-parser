# Per-Course Chat Session Reset — Design

**Date:** 2026-07-30
**Status:** Approved (pending spec review)

## Problem

Chat sessions are already scoped by course on the backend (`chat_sessions.course_id`,
`GET/POST /api/courses/{course_id}/sessions`) and in the query layer
(`useChatSessions(courseId)` keys its cache by `courseId`). But `ChatPane`
(`frontend/src/components/chat/ChatPane.tsx`) holds `sessionId` as local
component state, and `App.tsx` renders a single long-lived `ChatPane` instance
whose `courseId` prop just changes value on course switch. React reuses that
instance, so `sessionId` (and `streamingMessages`/`isSending`) survive the
switch. Two visible bugs result:

- Switching from a course with an active session to a course with no sessions
  still shows the old course's messages, because `sessionId` is non-null and
  the "pick first session" effect only fires `if (sessionId === null)`.
- Switching between two courses that both have sessions can show course A's
  messages under course B's UI until a session happens to change.

## Goal

Selecting a course must show that course's own chat: its existing session's
messages if one exists, or the "start a new chat" prompt if it doesn't —
never another course's session or messages.

Non-goal: any change to session/message data model, API, or multi-session UI
(session switching within a course is unchanged and stays out of scope).

## Approach

Force React to remount `ChatPane` on course change by keying it on
`selectedCourseId` in `App.tsx`:

```tsx
<ChatPane key={selectedCourseId} courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
```

A changed `key` makes React tear down the old `ChatPane` instance and mount a
fresh one, resetting all of its local state (`sessionId`, `streamingMessages`,
`isSending`) together. No changes needed inside `ChatPane` itself — the
existing "select first session if any, else show start-chat prompt" logic
already does the right thing once state starts fresh.

Rejected alternative: adding a `useEffect(() => resetState(), [courseId])`
inside `ChatPane`. This requires manually enumerating every piece of state to
reset (easy to miss one as the component grows) versus the one-line `key`
change getting all of it for free from React's own remount semantics.

## Edge case

If a message is actively streaming when the user switches courses, the
in-flight stream is abandoned (component unmounts; the `fetch` in
`sendMessageStream` keeps running but its callbacks target an unmounted
component and are dropped). This matches current behavior for any other
mid-stream navigation-like action and is acceptable: the assistant reply is
still persisted server-side and appears next time that session's messages are
fetched.

## Testing

Add `frontend/src/components/chat/ChatPane.test.tsx` (none exists today,
following the pattern of `DocumentList.test.tsx` / `CourseSelector.test.tsx`):

- Render with `courseId=1` where `useChatSessions` returns one existing
  session; assert its messages load.
- Re-render with a new `key`/`courseId=2` where `useChatSessions` returns no
  sessions; assert the "start a new chat" prompt is shown, not course 1's
  messages.

## Out of scope

- Session history / switching between multiple sessions within one course.
- Preserving or resuming an in-flight stream across a course switch.
