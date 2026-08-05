# 008 — Extraction runs at end-of-session, detected opportunistically

**Chosen:** A session counts as "ended" once `SESSION_INACTIVITY_MINUTES`
have passed since its last message. There is no scheduler in this app, so
staleness is checked opportunistically at `POST /api/courses/{id}/sessions`
(starting a new chat — the natural moment a user has moved on from the old
one, and a deliberate user action where a brief pause is at least
explainable). Finding a stale, not-yet-extracted session runs extraction
over at most `MAX_SESSIONS_PER_SWEEP` (currently 1) of them per call, so
the worst-case added latency on that request is bounded to a fixed number
of provider round-trips rather than one per stale session in the course.
`chat_sessions.memory_extracted_through_message_id` tracks progress,
mirroring `summarized_through_message_id` from Phase 1's compaction (ADR
004) — same pattern, same reason: idempotent, resumable, no
double-extraction; sessions left unprocessed by the cap are picked up on
a later call.

**Rejected:**
- Every turn — the app already has a per-turn LLM cost from Phase 1's
  query understanding (and sometimes compaction). Adding a third
  unconditional call compounds a cost Phase 1's own report flagged as
  worth reducing, not adding to. Reviewing a whole session at once instead
  of one exchange at a time also gives the extraction model more context
  to judge what's actually durable vs. transient.
- `GET /api/courses/{id}/sessions` (listing sessions) as a second trigger
  — this was the initial design, but it's a read path hit on every
  course-page load, not a deliberate action. Hooking a synchronous LLM
  call (sometimes multiple, before the sweep was capped) onto it meant a
  page load could block for seconds, or hang indefinitely against a slow
  or misbehaving provider endpoint — observed directly earlier in this
  same build against NVIDIA NIM. Removed in favor of the single
  `create_session` trigger, with the cap above as a second, independent
  bound on worst-case latency even there.
- Explicit signal (a "remember this" button) — new user-facing product
  surface beyond what any other task in this phase touches, and memory
  silently doesn't build unless the user remembers to invoke it, which
  cuts against "durable, cross-session knowledge" being maintained
  automatically.

**Why:** No new infrastructure (no cron/scheduler dependency), bounded
worst-case added latency on the one request path that triggers it, and
reuses a schema pattern this codebase already has prior art for.

**Known limitation:** two concurrent `create_session` requests for the
same course could both pick the same stale session before either advances
the watermark, producing a duplicate `Memory` row. Not addressed here —
`MAX_SESSIONS_PER_SWEEP=1` and course-level traffic being low make it
rare, and a duplicate near-identical memory just competes on decay score
(`app/memory/decay.py`) rather than corrupting state.
