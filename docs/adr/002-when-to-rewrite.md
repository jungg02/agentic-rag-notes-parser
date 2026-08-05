# 002 — Rewrite only when the query is detected as context-dependent

**Chosen:** One LLM call classifies intent AND decides `needs_rewrite`
(True when the query can't be answered standalone — pronouns, ellipsis,
dropped subjects referring to prior turns). Rewriting only runs when
`needs_rewrite` is True; the query goes to retrieval unchanged otherwise.

**Rejected:** Always rewriting every query — doubles the classification/
rewrite LLM call's cost for queries that were already standalone, and risks
the model silently drifting a fine query into something else.

**Why:** Matches the plan's own target-behaviour example (turn 2 depends on
turn 1; a first turn or a topic switch doesn't). Classification and the
rewrite decision are produced by the *same* call
(`app/query/understanding.py`), not two separate model calls, to avoid
paying two round-trips for one decision.
