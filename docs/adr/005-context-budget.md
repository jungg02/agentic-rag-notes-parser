# 005 — Context budget: relevance-thresholded memories, capped, non-citable

**Chosen:** At query time, retrieve up to `MEMORY_MAX_PER_QUERY` memories by
cosine similarity to the query embedding, keeping only those above
`MEMORY_SIMILARITY_THRESHOLD`. Memories render in a separate, non-citable
"what we know about this student" section of the system prompt — they never
enter the numbered `<excerpts>` block chunks use, so `MessageCitation` and
the citation contract (every excerpt has a real chunk_id and page number)
are untouched.

**Rejected:**
- Fixed split (always N memory + M chunk slots) — wastes budget when no
  memory is relevant to the current query, which is the common case for a
  single factual lookup.
- One unified ranking across both — the plan calls this "the most
  interesting decision," and it's the most principled (memory retrieval
  *is* retrieval), but memories have no page number to cite, so merging
  them into the same ranked, citable list means extending
  `MessageCitation`/`prompts.py`/the frontend citation chip to handle a
  source type that isn't a document excerpt. Bigger lift than this phase
  needs to prove the mechanism works; revisit once Phase 3's eval harness
  can show whether it's worth it.

**Why:** Adaptive without touching a system (citations) this phase doesn't
need to change.
