# 008 — Extraction runs at end-of-session, detected opportunistically

**Chosen:** A session counts as "ended" once `SESSION_INACTIVITY_MINUTES`
have passed since its last message. There is no scheduler in this app, so
staleness is checked opportunistically at two existing request paths that
already touch a course's sessions: `POST /api/courses/{id}/sessions`
(starting a new chat — the natural moment a user has moved on from the old
one) and `GET /api/courses/{id}/sessions` (listing them, e.g. on page
load). Either one finding a stale, not-yet-extracted session runs
extraction over it. `chat_sessions.memory_extracted_through_message_id`
tracks progress, mirroring `summarized_through_message_id` from Phase 1's
compaction (ADR 004) — same pattern, same reason: idempotent, resumable,
no double-extraction.

**Rejected:**
- Every turn — the app already has a per-turn LLM cost from Phase 1's
  query understanding (and sometimes compaction). Adding a third
  unconditional call compounds a cost Phase 1's own report flagged as
  worth reducing, not adding to. Reviewing a whole session at once instead
  of one exchange at a time also gives the extraction model more context
  to judge what's actually durable vs. transient.
- Explicit signal (a "remember this" button) — new user-facing product
  surface beyond what any other task in this phase touches, and memory
  silently doesn't build unless the user remembers to invoke it, which
  cuts against "durable, cross-session knowledge" being maintained
  automatically.

**Why:** No new infrastructure (no cron/scheduler dependency), no added
per-turn latency, and reuses a schema pattern this codebase already has
prior art for.
