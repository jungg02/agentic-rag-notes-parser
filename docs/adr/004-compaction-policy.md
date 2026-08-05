# 004 — Compaction keeps the last N turns verbatim, summarizes the rest

**Chosen:** `chat_sessions` gets two nullable columns: `summary` (rolling
text) and `summarized_through_message_id` (high-water mark). When history
exceeds a configurable token budget, everything before the last
`HISTORY_KEEP_LAST_N` messages gets folded into `summary` via one
incremental LLM summarization call (only the *new* not-yet-summarized
messages are sent, appended to the existing summary — not the whole history
re-summarized every time). The last N messages are always sent verbatim.

**Rejected:** A token-count cutoff instead of a turn-count cutoff — more
literally matches "token budget," but risks truncating a message
mid-sentence at the boundary, which is worse for coreference resolution in
the *next* turn (Task 2's whole job) than losing exact phrasing of an old
turn.

**Acceptable to lose:** exact wording, tone, and minor detail of turns
older than the verbatim window. **Must be preserved:** topics discussed and
key facts/answers, so coreference resolution and follow-up detection still
work across a summarized boundary.
