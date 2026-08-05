# 009 — Conflicts: keep both, resolve through recency/access decay at read time

**Chosen:** No contradiction detection at write time. A new fact that
contradicts an old one is simply inserted alongside it. Retrieval ranks
candidates by `similarity * decay_score`, and decay (Task 6, `app/memory/decay.py`)
weights recency and access count — so a newer, actively-surfacing fact
naturally outranks and eventually displaces a stale, contradicted one as
the store approaches its cap, without ever having explicitly "decided"
they conflict.

**Rejected:**
- Supersede/version — both require detecting that a new memory contradicts
  a specific old one, typically via embedding similarity between the two
  facts. That's the same unreliable step either way: contradictory facts
  are often *not* semantic near-duplicates ("prefers written explanations"
  vs. "prefers video explanations" aren't close in embedding space despite
  directly conflicting), so similarity-based contradiction detection would
  both miss real contradictions and false-positive on unrelated-but-similar
  facts.

**Why:** Reuses Task 6's decay mechanism instead of building a second,
less reliable system to solve a version of the same problem (which fact
matters now). Simpler, and the honest limitation (a genuinely stale fact
can still surface until it decays or the cap evicts it) is easy to name in
an interview rather than papering over with a detector that doesn't
actually work reliably.
