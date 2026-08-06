# Phase 3 ablation report

Evaluated against course_id=4 (DSA2101), 62 hand-authored items across 5 categories: comparison (n=10), cross_session_memory (n=8), multi_turn_coreference (n=12), single_turn_factual (n=22), topic_switch (n=10).

Live-LLM calls collected: 86 ok / 0 failed out of 86 attempted (re-run `bench/phase3_llm_collect.py` to retry failures -- see that script's docstring for why some fail and how retrying works).

## 1. Retrieval-mechanism ablation

Lexical only / dense only / RRF fused / fused + cross-encoder rerank, over all 62 items' raw (un-rewritten) query text.

| mode | recall@6 | mrr@6 | nDCG@10 |
|---|---|---|---|
| lexical | 33.9% | 0.323 | 0.323 |
| vector | 77.4% | 0.632 | 0.675 |
| fused | 79.0% | 0.655 | 0.708 |
| reranked | 82.3% | 0.698 | 0.740 |

**Reranked mode, broken out by category** (where the system is weaker or stronger):

| category | n | recall@6 | mrr@6 | nDCG@10 |
|---|---|---|---|---|
| single_turn_factual | 22 | 86.4% | 0.788 | 0.821 |
| comparison | 10 | 80.0% | 0.608 | 0.663 |
| multi_turn_coreference | 12 | 66.7% | 0.507 | 0.547 |
| topic_switch | 10 | 90.0% | 0.800 | 0.856 |
| cross_session_memory | 8 | 87.5% | 0.719 | 0.758 |

**Update (post-Phase-4):** the table above reflects `FUSED_CANDIDATES=20`
as measured during Phase 3. `FUSED_CANDIDATES` was later halved to 10 to
cut reranker latency (~700ms → ~280ms per item) after confirming, with
this same script, that recall@6 is unchanged in every category at the
lower candidate count — see the README's Phase 4 "post-phase
optimization" note. Left as originally measured here rather than edited
in place, since this table is what Phase 3 actually validated at the
time.

## 2. Query-rewriting ablation -- the headline number

**multi_turn_coreference** (n=12):

| condition | recall@6 | mrr@6 | nDCG@10 |
|---|---|---|---|
| no rewrite (raw last turn) | 66.7% | 0.507 | 0.547 |
| rewritten (understand_query) | 75.0% | 0.586 | 0.626 |

**topic_switch** (n=10):

| condition | recall@6 | mrr@6 | nDCG@10 |
|---|---|---|---|
| no rewrite (raw last turn) | 90.0% | 0.800 | 0.856 |
| rewritten (understand_query) | 90.0% | 0.800 | 0.856 |

**Headline sentence:** query rewriting improved recall@6 on multi-turn coreference queries from 66.7% to 75.0% (n=12).

topic_switch is a parity check, not an uplift target: raw and rewritten recall@6 differ by +0.0% (n=10) -- these turns are already standalone by construction, so rewriting should do no harm, not necessarily improve anything.

## 3. Semantic-memory ablation

Generated answers for the 8 cross_session_memory items, with and without the seeded memory injected into the system prompt. The without-memory row's faithful/citations-correct columns grade that answer on its own terms; its personalized column is a separate baseline check -- the *judge* (not the generator) is told the student's fact and asked whether the memory-blind answer happens to satisfy it anyway, so "0%" isn't assumed, it's measured (see ADR 010):

| condition | n | faithful | citations correct | personalized |
|---|---|---|---|---|
| with memory | 8 | 100.0% | 100.0% | 33.3% (of 6/8 judged) |
| without memory (baseline) | 8 | 100.0% | 100.0% | 50.0% (of 4/8 judged) |

**Cross-session memory improvement:** 33.3% of with-memory answers (6/8 judged) were scored as appropriately reflecting the seeded student-context fact, vs. 50.0% for memory-blind answers judged against the same fact (4/8 judged) -- a real, measured baseline, not a definitional zero.

**Caveat on the 100% faithful/citations-correct figures above:** those come from the judge, and hand-labeling (§4) found the judge wrong on one of these 8 with-memory answers -- csm05's answer cited an excerpt for a base-R syntax claim the excerpt doesn't actually make. The true with-memory faithful rate on this subset is closer to 87.5% (7/8) by my own reading; the judge's 100% here is an overestimate, consistent with the calibration gap reported in §4.

## 4. LLM-as-judge calibration

Judge verdicts (faithful, citations_correct) hand-labeled against 20 answers (20 with a matching cached judge verdict to compare against):
- **faithful** agreement rate: 90.0%
- **citations_correct** agreement rate: 95.0%

Calibration set: the 8 cross_session_memory with-memory answers, plus 6 single_turn_factual + 6 comparison calibration-only answers (`phase3_llm_collect.CALIBRATION_EXTRA_IDS`).

## Honest weaknesses

- **Test set is self-authored, not domain-verified.** All 62 items were written by reading the actual source chunk and constructing a question from it, so grounding (`expected` pages) is exact by construction -- but no one with real DSA2101 domain expertise has reviewed the questions themselves for naturalness or ambiguity.

- **Vocabulary overlap with source text is possible**, since questions were authored while reading the target chunk (e.g. "subset a string" mirrors `str_sub()`'s own phrasing). This could inflate lexical-search performance relative to how a real student would phrase questions. In this run lexical actually underperformed vector/fused/reranked substantially (see §1), so the effect wasn't dominant here, but it isn't ruled out as a factor either.

- **Small per-category n** (8-22 items per category) limits how much confidence to place in any single category's percentage -- a few items flipping would move these numbers noticeably.

- **Single course, single domain** (DSA2101, an R/data-science course). None of these numbers are known to generalize to other subjects.

- **Judge is not independent of the system it's judging** -- same model/provider generates the answers and judges them, a known LLM-as-judge weakness (shared blind spots don't show up as disagreement). The calibration agreement rate above bounds this somewhat but doesn't eliminate it.

- **Provider instability affected coverage, not just latency.** The configured NVIDIA NIM endpoint was observed taking anywhere from ~1s to 381s per call during this build; 11 of 97 total call attempts across every collection run timed out or errored and had to be retried in a later run (final state: 0 of 86 unique calls still unresolved). Any items that remain unresolved are simply missing from the relevant ablation rather than counted as failures, which could bias results if failures aren't random with respect to item difficulty.

- **Memory ablation only measures judge-perceived personalization**, not whether the personalization was actually *helpful* to the student -- those aren't necessarily the same thing, and this harness has no way to distinguish them.

- **The with-memory vs. baseline personalization gap in §3 is noise, not a finding, at this sample size.** Both conditions had exactly 2 answers judged "personalized: true" -- 2/6 with memory vs. 2/4 without -- the different percentages come entirely from the judge's inconsistent null-vs-bool compliance across the two conditions shrinking one denominator, not from memory injection changing how many answers were actually personalized. Two of the eight items (csm05, csm08) were judged personalized in *both* conditions, meaning the memory-blind answer satisfied the fact anyway -- with only 8 items total, this ablation cannot support a directional claim about whether memory helps personalization here, only that the harness can measure it at all.
