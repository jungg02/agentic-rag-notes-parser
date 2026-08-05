# 007 — Memories embedded with the same model as document chunks

**Chosen:** `BAAI/bge-small-en-v1.5`, the same singleton already loaded for
chunk embedding (`app/ingestion/embedder.py`), reused for memory content.

**Rejected:** A separate model tuned for short fact-like strings — plausibly
a marginally better fit for memory text (short, first-person facts) versus
document excerpts (long, third-person passages), but adds a second resident
model (startup/memory cost) for an unmeasured quality gain.

**Why:** Zero marginal infrastructure cost, and keeps memories and chunks in
the same vector space — a prerequisite if a later phase ever moves toward
ADR 005's rejected "unified ranking" alternative. Nothing here is lost by
switching later if this turns out to matter; it's not a one-way door.
