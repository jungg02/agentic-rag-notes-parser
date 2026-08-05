# 006 — Extraction: one conservative LLM call with a confidence field

**Chosen:** A single call to the existing `LLMProvider` (same pattern as
Phase 1's query understanding — ADR 001) reviews a session's messages and
emits a JSON list of candidate facts, each with a `memory_type`
(topic/struggle/preference/goal) and a self-reported `confidence` in
[0, 1]. The prompt instructs the model to default to an empty list and only
extract facts that are genuinely durable (would still be true/useful next
session), not transient conversational content. Only facts above
`MEMORY_CONFIDENCE_THRESHOLD` are persisted.

**Rejected:** Rule-based heuristics (regex for "I prefer X" / "I'm confused
about X") — cheap and deterministic, but real signal is rarely phrased that
literally; would miss most of what's worth remembering.

**Why:** "Be conservative — over-extraction poisons the store" (Phase 2
goal) is a prompting and thresholding problem, not a new-model problem — no
new dependency needed, consistent with the working agreement.
