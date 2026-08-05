# 001 — Query rewriting uses the existing LLM provider, not a local model

**Chosen:** Rewrite/classify via a single call to the already-configured
`LLMProvider` (Anthropic or OpenAI-compatible, `providers/factory.py`).

**Rejected:**
- A new local instruction model — adds a heavy dependency and a model
  download for a task the stack's existing LLM already does well; violates
  the working agreement's "no new heavy dependencies without asking."
- Rule-based heuristics — real coreference ("which one", "the second one")
  doesn't reduce to pattern matching reliably.

**Why:** Reuses infrastructure that's already there. The real cost is a
network round-trip before retrieval starts — that's the number this phase's
stop condition asks to measure, not a hidden one.
