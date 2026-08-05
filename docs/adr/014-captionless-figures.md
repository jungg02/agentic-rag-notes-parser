# 014 — Captionless figures remain retrievable via image embedding alone

**Chosen:** A figure is indexed and retrievable as soon as it's extracted
and embedded; caption generation is a separate, best-effort enrichment
step that populates `figures.caption` (and its lexical `tsvector`) when
it succeeds, and leaves both null when it doesn't. A null caption never
excludes a figure from dense (image-embedding) search — it only means
that figure has no presence in the lexical arm of figure retrieval
(ADR 013), same as how a chunk with zero lexical-search matches still
surfaces through the dense arm in the existing hybrid pipeline.

**Rejected:** requiring a caption before a figure is indexed at all.
Simpler mental model (every retrievable figure has a human-readable
label) but throws away real, embeddable visual content the moment
caption generation fails or is skipped — and undercuts the actual reason
to use an image-embedding model in the first place, which is that SigLIP
understands image content without needing a text description of it.

**Why:** matches this codebase's established graceful-degradation
philosophy for enrichment steps — the OCR fallback already never lets a
failed enrichment destroy otherwise-good content (`docs/ARCHITECTURE.md`'s
"don't let OCR destroy native text or fail whole documents"). Caption
generation here is exactly that kind of enrichment: valuable when it
works, not load-bearing when it doesn't.

**Addendum — caption generation deferred entirely this build:** this
decision assumed captions would sometimes fail to generate; in practice,
they never got a chance to. Probed directly against the configured
provider (`LLM_MODEL` via NVIDIA NIM): a chat-completion call with an
`image_url` content part returns `BadRequestError: "... is not a
multimodal model"` in ~1s, not a hang -- a clean, fast, unambiguous
capability gap, not the endpoint's usual latency flakiness. Wiring up
real vision captioning would mean extending `LLMProvider`/`LLMMessage`
to carry image content across both provider implementations -- a
structural change to a shared interface, for a feature this decision
already scoped as optional and the current model can't serve anyway.
Deferred rather than built against a model that can't use it; `figures`
ships with `caption`/`caption_tsv` real and queryable, just empty. See
the Phase 4 section of the README for how this affects the lexical arm
of figure retrieval.
