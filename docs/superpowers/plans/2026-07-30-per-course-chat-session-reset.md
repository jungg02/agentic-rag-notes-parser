# Per-Course Chat Session Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switching the selected course in the sidebar must show that course's own chat (its existing session, or a fresh "start a new chat" prompt) and never another course's leftover session or messages.

**Architecture:** `ChatPane` (`frontend/src/components/chat/ChatPane.tsx`) already fetches sessions scoped by `courseId` (`useChatSessions(courseId)`, query key includes `courseId`) and the backend already scopes sessions to a course. The only bug is that `ChatPane`'s local `sessionId`/`streamingMessages`/`isSending` state survives a `courseId` prop change because `App.tsx` reuses the same component instance across course switches. Fix: give `<ChatPane>` a `key={selectedCourseId}` in `App.tsx` so React remounts it (and resets all its local state) on every course switch.

**Tech Stack:** React 18, TypeScript, Vitest, @testing-library/react, @tanstack/react-query.

## Global Constraints

- No backend changes — session scoping is already correct there.
- No changes to `ChatPane`'s internal logic — only how it's mounted from `App.tsx`.
- Follow the existing test convention in this repo: mock `global.fetch` per test with a URL-dispatching `vi.fn`, wrap children needing query context in a fresh `QueryClient` (see `frontend/src/components/courses/CourseSelector.test.tsx`).

---

### Task 1: Regression test + fix for course-switch chat isolation

**Files:**
- Modify: `frontend/src/App.tsx:37` (add `key={selectedCourseId}` to the `<ChatPane>` element)
- Modify: `frontend/src/App.test.tsx` (add the new test; keep the existing "renders the app title" test as-is)

**Interfaces:**
- Consumes: `App` (default export of `frontend/src/App.tsx`), `CourseSelector` course button text format `"{name} ({document_count})"` (see `frontend/src/components/courses/CourseSelector.tsx:43`), `ChatPane`'s rendered UI — start-of-chat prompt text `"Ask questions about this course's materials and get answers cited back to the source page."` and button text `"Start a new chat"` (see `frontend/src/components/chat/ChatPane.tsx:79-82`).
- Produces: nothing new consumed by later tasks — this is the only task.

- [ ] **Step 1: Write the failing test in `frontend/src/App.test.tsx`**

Replace the file contents with:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App", () => {
  it("renders the app title", () => {
    render(<App />);
    expect(screen.getByText("Study Notes Parser")).toBeInTheDocument();
  });
});

describe("App course switching", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/courses") {
          return jsonResponse([
            { id: 1, name: "Investment and Finance", created_at: "2026-01-01T00:00:00Z", document_count: 0 },
            { id: 2, name: "ST3131", created_at: "2026-01-01T00:00:00Z", document_count: 0 },
          ]);
        }
        if (url === "/api/courses/1/sessions") {
          return jsonResponse([{ id: 10, course_id: 1, title: null, created_at: "2026-01-01T00:00:00Z" }]);
        }
        if (url === "/api/courses/2/sessions") {
          return jsonResponse([]);
        }
        if (url === "/api/courses/1/documents" || url === "/api/courses/2/documents") {
          return jsonResponse([]);
        }
        if (url === "/api/sessions/10/messages") {
          return jsonResponse([
            {
              id: 100,
              role: "user",
              content: "Hello from Investment and Finance",
              created_at: "2026-01-01T00:00:00Z",
              citations: [],
            },
          ]);
        }
        throw new Error(`Unexpected fetch to ${url}`);
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the new course's own chat instead of the previous course's session", async () => {
    render(<App />);

    const investmentButton = await screen.findByText("Investment and Finance (0)");
    investmentButton.click();
    await waitFor(() => expect(screen.getByText("Hello from Investment and Finance")).toBeInTheDocument());

    const st3131Button = await screen.findByText("ST3131 (0)");
    st3131Button.click();

    await waitFor(() => expect(screen.getByText("Start a new chat")).toBeInTheDocument());
    expect(screen.queryByText("Hello from Investment and Finance")).not.toBeInTheDocument();
  });
});
```

Note: `frontend/src/components/documents/DocumentList.tsx` and `UploadDropzone` also render inside `App`'s workspace column and will fetch `/api/courses/{id}/documents` — the mock above returns `[]` for those so they don't throw on unmocked URLs.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: FAIL on the second test (`"shows the new course's own chat..."`) — after clicking `ST3131 (0)`, `"Hello from Investment and Finance"` is still present and/or `"Start a new chat"` never appears, because `ChatPane`'s `sessionId` state (10) survives the course switch.

- [ ] **Step 3: Apply the fix in `frontend/src/App.tsx`**

Change:

```tsx
                <ChatPane courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
```

to:

```tsx
                <ChatPane key={selectedCourseId} courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full frontend test suite to check for regressions**

Run: `cd frontend && npx vitest run`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "$(cat <<'EOF'
fix: reset chat session state when switching courses

ChatPane kept its sessionId/streamingMessages state across course
switches because App.tsx reused the same instance. Keying ChatPane on
selectedCourseId forces a remount so each course shows its own chat.
EOF
)"
```

## Self-Review Notes

- **Spec coverage:** The spec's single functional requirement (course switch shows that course's own session or a fresh prompt, never another course's data) is covered by the Task 1 test. The spec's "edge case" (abandoned in-flight stream) and "out of scope" items (session history/switching) are explicitly non-goals with no corresponding task, per the spec.
- **Placeholder scan:** No TBDs; all steps have full code/commands.
- **Type consistency:** No new types/functions introduced — this task only changes a JSX attribute and a test file.
