# Demo script

A scripted walkthrough of the three capabilities that distinguish this
build from a plain hybrid-RAG chatbot: **multi-turn resolution**,
**cross-session memory**, and **cross-modal (figure) retrieval**. Each
part gives exact queries to type in the UI, what to point out while it
runs, and a `curl`/`psql` command to verify the underlying mechanism
directly — useful if you're presenting this and want to show the
machinery, not just the chat bubble.

## Prerequisites

This script's example queries are grounded against a specific,
already-ingested course: **DSA2101** (an R/data-science course, 7 slide
decks, `Week1_slides.pdf`–`Week7_slides.pdf`), the same dataset used
throughout the Phase 3/4 evaluation harnesses (`backend/scripts/eval/`).
It is **not** shipped in the repo or seedable from a fresh clone — there's
no seed script (checked; none exists) — because it's real personal course
material, not synthetic fixtures.

- **If you have this repo's dev database already populated** (course
  `DSA2101`, `course_id=4` in a typical local setup — confirm with
  `docker compose exec db psql -U notes -d notes -c "SELECT id, name FROM courses;"`),
  the queries below will work verbatim.
- **If you don't**, upload any real course's slides/notes instead, and
  substitute your own factual/follow-up/preference/figure queries — the
  *shape* of each part (a fact, then a pronoun reference to it; a
  preference reveal, then a later session; a "show me a diagram of X"
  request) transfers to any content. The point being demonstrated is the
  mechanism, not these specific R functions.

Throughout, replace `4` with your actual course ID and adjust the base
URL if not running via `docker compose` on default ports
(`http://localhost:8000`).

**A note on live-LLM steps (Parts 1 and 2):** this build's own testing
found the configured provider endpoint's latency varies anywhere from
~1s to several minutes for a single call (ADR 010) — confirmed again
while verifying this script. If a response in Parts 1 or 2 seems to be
taking a while, that's the endpoint, not a bug; give it a minute before
assuming something's wrong.

## Part 1 — Multi-turn resolution

Demonstrates: a follow-up question that's meaningless in isolation gets
classified as context-dependent, rewritten to a standalone query, and
retrieval runs on the rewrite — not the literal pronoun-laden text
(ADR 001–003, `app/query/understanding.py`).

**In the chat UI**, start a new session in the DSA2101 course and send,
one at a time:

1. `What does read_excel() do?`
2. `What does the skip argument do in it?`

**What to point out:** the second message has no verb-independent meaning
— "it" refers to nothing without turn 1. Watch for "Interpreted as: ..."
appearing under the second message in the UI once the response streams in
— that's the rewritten standalone query
(e.g. *"What does the skip argument do in the read_excel() function?"*),
shown only when the rewrite differs from what was typed. The citation
under the answer should point at `Week3_slides.pdf`.

**Verify the mechanism directly:**

```bash
curl -s http://localhost:8000/api/sessions/<session_id>/messages | python3 -m json.tool
```

The second user-role message's `rewritten_query` field carries the
resolved standalone question — this is what actually got embedded and
searched, not the raw "What does the skip argument do in it?" text.

## Part 2 — Cross-session memory

Demonstrates: a fact revealed in one session is extracted, persisted, and
retrieved in a **later, separate session** — durable, not just
within-conversation context (ADR 005–009, `app/memory/`).

Extraction is opportunistic and only checks for staleness (30 minutes of
inactivity) when a *new* session is created. For a live demo, backdating
the previous session's last message is the practical way to trigger it
immediately rather than waiting — call this out explicitly as a demo-only
shortcut, not something the app does in production.

**Step 1 — reveal something durable, in Session A:**

```
I keep mixing up left_join() and inner_join() — I never remember which one keeps unmatched rows.
```

Let the assistant respond, then stop (don't send anything else in this
session).

**Step 2 — force staleness (demo-only shortcut):**

```bash
docker compose exec db psql -U notes -d notes -c \
  "UPDATE chat_messages SET created_at = now() - interval '31 minutes' \
   WHERE session_id = <session_A_id>;"
```

**Step 3 — start Session B** (new chat, same course) — this is the
action that opportunistically checks Session A for staleness and, finding
it, runs one extraction call:

```bash
curl -s -X POST http://localhost:8000/api/courses/4/sessions
```

**Step 4 — verify the memory was extracted:**

```bash
curl -s http://localhost:8000/api/courses/4/memories | python3 -m json.tool
```

You should see a new row with `memory_type: "struggle"` and content along
the lines of "struggles to distinguish left_join() from inner_join()
regarding unmatched rows," with a `confidence` score. If nothing appears,
the fact may have scored below `MEMORY_CONFIDENCE_THRESHOLD` (0.6) — try a
more clearly durable statement, or check `docker compose logs backend` for
the extraction call's outcome.

**Step 5 — ask something related in Session B:**

```
What's the difference between left_join() and inner_join()?
```

**What to point out:** the memory is in the system prompt's
`<student_context>` block (non-citable, separate from the sourced
`<excerpts>`), so it can shape tone/emphasis without ever being cited as
a source. **Be honest about what this does and doesn't prove live:** the
memory is verifiably retrieved and injected (Step 4 proves that
directly) — but whether a *specific* live response visibly changes tone
because of it is not guaranteed on every run. The project's own Phase 3
evaluation measured this at n=8 and explicitly does not support a
directional personalization claim at that sample size (see
`backend/bench/results/ablation.md`). Demo the mechanism working
(extraction → persistence → retrieval), not an overclaimed "watch it get
smarter" moment.

## Part 3 — Cross-modal (figure) retrieval

Demonstrates: a natural-language query retrieves a relevant *figure* —
not text — via image embeddings independent of chunk retrieval (ADR
011–014, `app/retrieval/figures.py`).

**In the chat UI**, in any session for the DSA2101 course, send:

```
Show me a screenshot of the RStudio interface with its editor, console, and output panes.
```

**What to point out:** a figure thumbnail appears below the assistant's
answer (`RelatedFigures` component), separate from the numbered text
citations — clicking it opens the same source panel used for text
citations, at the page the figure was extracted from (`Week1_slides.pdf`,
page 15 — verified live as this build's top-ranked result for this exact
query, not assumed from the eval file). The figure is retrievable even
though it has no caption — `figures.caption` is null for every row in
this build (caption generation was evaluated and deferred; ADR 014) —
retrieval works from the image embedding alone.

A second query, also verified as a top-3 hit (`Week5_slides.pdf`, page
72, ranked 2nd of 3):

```
Show me the tidyverse package logos.
```

**Verify the mechanism directly** (capping at 3 to match what the chat
path actually surfaces — the raw endpoint's default `limit` is wider):

```bash
curl -s -G "http://localhost:8000/api/courses/4/figures" \
  --data-urlencode "q=Show me a screenshot of the RStudio interface with its editor, console, and output panes" \
  --data-urlencode "limit=3" | python3 -m json.tool
```

Returns the same `Figure` rows the chat path surfaces, independent of the
chat flow — useful to show the retrieval mechanism works standalone, not
only when wrapped in a generated answer.

**Optional — show the honest weak spot, not just the wins:** the query
`Show me a diagram illustrating how a join matches keys between two
tables.` is one of this eval set's near-misses — verified live, its
top-3 results are three *different*, genuinely relevant join diagrams
from `Week7_slides.pdf` (pages 31, 22, 14), not the specific page picked
as ground truth (page 5, which does still appear in the full result set,
just ranked below the cutoff the chat UI shows). This is a good moment
to cite the actual number if presenting this seriously: **44.4% recall@3**
on a 9-item hand-verified eval set (`bench/phase4_figure_ablation.py`) —
genuinely modest, and this near-miss is exactly the kind of case behind
that number, not a cherry-picked failure.

## What this script deliberately doesn't demonstrate

Per the project's own honesty standard (see the README's
"What this is not" section): no video/keyframe understanding (figures
are static images only) and no trained visual representation learning
(SigLIP is used frozen, exactly as pretrained). Neither was ever
attempted — both were explicit stretch goals in the build plan.
