"""Assembles bench/results/ablation.md from the outputs of the other
phase3_*.py scripts: phase3_retrieval_ablation.py (mechanism ablation),
phase3_rewriting_ablation.py (headline multi-turn number),
phase3_llm_collect.py's cache (memory ablation + judge verdicts), and
phase3_hand_labels.py (my own labels for the judge-calibration subset).

Zero new LLM calls, zero new DB queries -- purely reads and reassembles
what the other scripts already produced. Run this last.

Usage (run inside the backend container):

    docker compose run --rm backend python bench/phase3_report.py \\
        --output bench/results/ablation.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.phase3_dataset import DEFAULT_EVAL_FILE, load_items
from bench.phase3_hand_labels import HAND_LABELS
from bench.phase3_llm_collect import CACHE_PATH, Cache

RETRIEVAL_ABLATION_PATH = Path("bench/results/phase3_retrieval_ablation.json")
REWRITING_ABLATION_PATH = Path("bench/results/phase3_rewriting_ablation.json")
MODES = ["lexical", "vector", "fused", "reranked"]


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _load_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def _raw_attempt_stats(path: Path) -> dict:
    """Unlike Cache.load_all() (which dedups to the final state per key,
    so a retried-and-now-successful call shows 0 failures), this counts
    every attempt including ones later superseded by a retry -- the number
    that actually reflects how often the provider failed during collection."""
    if not path.exists():
        return {"total_attempts": 0, "total_failed_attempts": 0}
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {
        "total_attempts": len(lines),
        "total_failed_attempts": sum(1 for r in lines if not r["ok"]),
    }


def _memory_ablation(cache_rows: list[dict]) -> dict:
    judges_by_item_condition = {
        (r["item_id"], r["condition"]): r["data"] for r in cache_rows if r["kind"] == "judge" and r["ok"]
    }
    with_memory_ids = [iid for (iid, cond) in judges_by_item_condition if cond == "with_memory"]
    without_memory_ids = [iid for (iid, cond) in judges_by_item_condition if cond == "without_memory"]
    with_memory = [judges_by_item_condition[(iid, "with_memory")] for iid in with_memory_ids]
    without_memory = [judges_by_item_condition[(iid, "without_memory")] for iid in without_memory_ids]

    # The without_memory arm's own judge call never received the student's
    # fact (the generator didn't either -- that's the point of the
    # condition), so its `personalized` is null by construction, not
    # measured. "without_memory_graded_with_fact" is a *separate* judge call
    # over the same without_memory answer that tells only the judge the
    # fact, giving a real baseline: does a memory-blind answer happen to
    # satisfy the fact anyway? Without this, "0%" would be an artifact of
    # never asking the question, not a finding (see ADR 010).
    baseline_personalized = {
        iid: d["personalized"]
        for (iid, cond), d in judges_by_item_condition.items()
        if cond == "without_memory_graded_with_fact" and d["personalized"] is not None
    }

    def summarize(rows: list[dict], item_ids: list[str], personalized_override: dict[str, bool] | None = None) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0}
        if personalized_override is not None:
            personalized_values = [personalized_override[iid] for iid in item_ids if iid in personalized_override]
        else:
            # `personalized` is null when the judge didn't comply with
            # "must be non-null when a fact IS given" (see ablation.md §3's
            # note) -- rate is computed over whichever rows it did answer.
            personalized_values = [r["personalized"] for r in rows if r.get("personalized") is not None]
        return {
            "n": n,
            "faithful_rate": round(sum(1 for r in rows if r["faithful"]) / n, 4),
            "citations_correct_rate": round(sum(1 for r in rows if r["citations_correct"]) / n, 4),
            "personalized_rate": round(sum(personalized_values) / len(personalized_values), 4) if personalized_values else None,
            "personalized_n_judged": len(personalized_values),
        }

    return {
        "with_memory": summarize(with_memory, with_memory_ids),
        "without_memory": summarize(without_memory, without_memory_ids, personalized_override=baseline_personalized),
    }


def _judge_agreement(cache_rows: list[dict]) -> dict:
    judges = {(r["item_id"], r["condition"]): r["data"] for r in cache_rows if r["kind"] == "judge" and r["ok"]}
    fields = ["faithful", "citations_correct"]
    agree = {f: 0 for f in fields}
    total = {f: 0 for f in fields}
    rows_compared = 0
    for key, hand in HAND_LABELS.items():
        judge = judges.get(key)
        if judge is None:
            continue
        rows_compared += 1
        for f in fields:
            total[f] += 1
            if hand[f] == judge[f]:
                agree[f] += 1

    return {
        "n_compared": rows_compared,
        "n_hand_labeled": len(HAND_LABELS),
        "agreement_by_field": {f: round(agree[f] / total[f], 4) if total[f] else None for f in fields},
    }


def build_report() -> str:
    course_id, items = load_items(DEFAULT_EVAL_FILE)
    categories = sorted({i.category for i in items})
    n_by_category = {c: sum(1 for i in items if i.category == c) for c in categories}

    retrieval = _load_json(RETRIEVAL_ABLATION_PATH)
    rewriting = _load_json(REWRITING_ABLATION_PATH)
    cache_rows = Cache(CACHE_PATH).load_all()
    memory_ablation = _memory_ablation(cache_rows)
    judge_agreement = _judge_agreement(cache_rows)

    n_ok = sum(1 for r in cache_rows if r["ok"])
    n_failed = sum(1 for r in cache_rows if not r["ok"])
    attempt_stats = _raw_attempt_stats(CACHE_PATH)

    lines: list[str] = []
    lines.append("# Phase 3 ablation report")
    lines.append("")
    lines.append(
        f"Evaluated against course_id={course_id} (DSA2101), {len(items)} hand-authored items across "
        f"{len(categories)} categories: " + ", ".join(f"{c} (n={n_by_category[c]})" for c in categories) + "."
    )
    lines.append(
        f"\nLive-LLM calls collected: {n_ok} ok / {n_failed} failed out of {len(cache_rows)} attempted "
        "(re-run `bench/phase3_llm_collect.py` to retry failures -- see that script's docstring for why "
        "some fail and how retrying works)."
    )

    lines.append("\n## 1. Retrieval-mechanism ablation")
    lines.append(
        "\nLexical only / dense only / RRF fused / fused + cross-encoder rerank, over all "
        f"{retrieval['n_items'] if retrieval else '?'} items' raw (un-rewritten) query text."
    )
    if retrieval:
        lines.append("\n| mode | recall@6 | mrr@6 | nDCG@10 |")
        lines.append("|---|---|---|---|")
        for mode in MODES:
            m = retrieval["overall"][mode]
            lines.append(f"| {mode} | {_pct(m['recall_at_6'])} | {m['mrr_at_6']:.3f} | {m['ndcg_at_10']:.3f} |")

        lines.append("\n**Reranked mode, broken out by category** (where the system is weaker or stronger):")
        lines.append("\n| category | n | recall@6 | mrr@6 | nDCG@10 |")
        lines.append("|---|---|---|---|---|")
        for category, per_mode in retrieval["by_category"].items():
            m = per_mode["reranked"]
            lines.append(f"| {category} | {m['n']} | {_pct(m['recall_at_6'])} | {m['mrr_at_6']:.3f} | {m['ndcg_at_10']:.3f} |")
    else:
        lines.append("\n*(missing -- run `bench/phase3_retrieval_ablation.py`)*")

    lines.append("\n## 2. Query-rewriting ablation -- the headline number")
    if rewriting:
        for category in ["multi_turn_coreference", "topic_switch"]:
            if category not in rewriting:
                continue
            c = rewriting[category]
            raw, rew = c["raw"], c["rewritten"]
            lines.append(f"\n**{category}** (n={raw['n']}):")
            lines.append("\n| condition | recall@6 | mrr@6 | nDCG@10 |")
            lines.append("|---|---|---|---|")
            lines.append(f"| no rewrite (raw last turn) | {_pct(raw['recall_at_6'])} | {raw['mrr_at_6']:.3f} | {raw['ndcg_at_10']:.3f} |")
            lines.append(f"| rewritten (understand_query) | {_pct(rew['recall_at_6'])} | {rew['mrr_at_6']:.3f} | {rew['ndcg_at_10']:.3f} |")
        coref = rewriting.get("multi_turn_coreference")
        if coref:
            lines.append(
                f"\n**Headline sentence:** query rewriting improved recall@6 on multi-turn coreference queries "
                f"from {_pct(coref['raw']['recall_at_6'])} to {_pct(coref['rewritten']['recall_at_6'])} "
                f"(n={coref['raw']['n']})."
            )
        tsw = rewriting.get("topic_switch")
        if tsw:
            delta = tsw["rewritten"]["recall_at_6"] - tsw["raw"]["recall_at_6"]
            lines.append(
                f"\ntopic_switch is a parity check, not an uplift target: raw and rewritten recall@6 differ by "
                f"{delta:+.1%} (n={tsw['raw']['n']}) -- these turns are already standalone by construction, so "
                "rewriting should do no harm, not necessarily improve anything."
            )
    else:
        lines.append("\n*(missing -- run `bench/phase3_rewriting_ablation.py` after `phase3_llm_collect.py`)*")

    lines.append("\n## 3. Semantic-memory ablation")
    wm, wom = memory_ablation["with_memory"], memory_ablation["without_memory"]
    if wm.get("n") and wom.get("n"):
        lines.append(
            f"\nGenerated answers for the {wm['n']} cross_session_memory items, with and without the seeded memory "
            "injected into the system prompt. The without-memory row's faithful/citations-correct columns grade "
            "that answer on its own terms; its personalized column is a separate baseline check -- the *judge* "
            "(not the generator) is told the student's fact and asked whether the memory-blind answer happens to "
            "satisfy it anyway, so \"0%\" isn't assumed, it's measured (see ADR 010):"
        )
        lines.append("\n| condition | n | faithful | citations correct | personalized |")
        lines.append("|---|---|---|---|---|")
        for label, s in [("with memory", wm), ("without memory (baseline)", wom)]:
            if s["personalized_rate"] is not None:
                personalized = f"{_pct(s['personalized_rate'])} (of {s['personalized_n_judged']}/{s['n']} judged)"
            else:
                personalized = "n/a"
            lines.append(f"| {label} | {s['n']} | {_pct(s['faithful_rate'])} | {_pct(s['citations_correct_rate'])} | {personalized} |")
        if wm["personalized_rate"] is not None and wom["personalized_rate"] is not None:
            lines.append(
                f"\n**Cross-session memory improvement:** {_pct(wm['personalized_rate'])} of with-memory answers "
                f"({wm['personalized_n_judged']}/{wm['n']} judged) were scored as appropriately reflecting the "
                f"seeded student-context fact, vs. {_pct(wom['personalized_rate'])} for memory-blind answers "
                f"judged against the same fact ({wom['personalized_n_judged']}/{wom['n']} judged) -- a real, "
                "measured baseline, not a definitional zero."
            )
        lines.append(
            "\n**Caveat on the 100% faithful/citations-correct figures above:** those come from the judge, and "
            "hand-labeling (§4) found the judge wrong on one of these 8 with-memory answers -- csm05's answer "
            "cited an excerpt for a base-R syntax claim the excerpt doesn't actually make. The true with-memory "
            "faithful rate on this subset is closer to 87.5% (7/8) by my own reading; the judge's 100% here is "
            "an overestimate, consistent with the calibration gap reported in §4."
        )
    else:
        lines.append("\n*(missing or incomplete -- run `bench/phase3_llm_collect.py`)*")

    lines.append("\n## 4. LLM-as-judge calibration")
    lines.append(
        f"\nJudge verdicts (faithful, citations_correct) hand-labeled against {judge_agreement['n_hand_labeled']} "
        f"answers ({judge_agreement['n_compared']} with a matching cached judge verdict to compare against):"
    )
    for field, rate in judge_agreement["agreement_by_field"].items():
        lines.append(f"- **{field}** agreement rate: {_pct(rate) if rate is not None else 'n/a'}")
    lines.append(
        "\nCalibration set: the 8 cross_session_memory with-memory answers, plus 6 single_turn_factual + "
        "6 comparison calibration-only answers (`phase3_llm_collect.CALIBRATION_EXTRA_IDS`)."
    )

    lines.append("\n## Honest weaknesses")
    lines.append(
        "\n- **Test set is self-authored, not domain-verified.** All 62 items were written by reading the "
        "actual source chunk and constructing a question from it, so grounding (`expected` pages) is exact "
        "by construction -- but no one with real DSA2101 domain expertise has reviewed the questions "
        "themselves for naturalness or ambiguity."
    )
    lines.append(
        "\n- **Vocabulary overlap with source text is possible**, since questions were authored while reading "
        "the target chunk (e.g. \"subset a string\" mirrors `str_sub()`'s own phrasing). This could inflate "
        "lexical-search performance relative to how a real student would phrase questions. In this run lexical "
        f"actually underperformed vector/fused/reranked substantially (see §1), so the effect wasn't dominant "
        "here, but it isn't ruled out as a factor either."
    )
    lines.append(
        "\n- **Small per-category n** (8-22 items per category) limits how much confidence to place in any "
        "single category's percentage -- a few items flipping would move these numbers noticeably."
    )
    lines.append(
        "\n- **Single course, single domain** (DSA2101, an R/data-science course). None of these numbers are "
        "known to generalize to other subjects."
    )
    lines.append(
        "\n- **Judge is not independent of the system it's judging** -- same model/provider generates the "
        "answers and judges them, a known LLM-as-judge weakness (shared blind spots don't show up as "
        "disagreement). The calibration agreement rate above bounds this somewhat but doesn't eliminate it."
    )
    lines.append(
        "\n- **Provider instability affected coverage, not just latency.** The configured NVIDIA NIM endpoint "
        f"was observed taking anywhere from ~1s to 381s per call during this build; "
        f"{attempt_stats['total_failed_attempts']} of {attempt_stats['total_attempts']} total call attempts "
        f"across every collection run timed out or errored and had to be retried in a later run (final state: "
        f"{n_failed} of {len(cache_rows)} unique calls still unresolved). Any items that remain unresolved are "
        "simply missing from the relevant ablation rather than counted as failures, which could bias results "
        "if failures aren't random with respect to item difficulty."
    )
    lines.append(
        "\n- **Memory ablation only measures judge-perceived personalization**, not whether the personalization "
        "was actually *helpful* to the student -- those aren't necessarily the same thing, and this harness "
        "has no way to distinguish them."
    )
    if wm.get("personalized_n_judged") and wom.get("personalized_n_judged"):
        lines.append(
            "\n- **The with-memory vs. baseline personalization gap in §3 is noise, not a finding, at this "
            f"sample size.** Both conditions had exactly 2 answers judged \"personalized: true\" -- "
            f"2/{wm['personalized_n_judged']} with memory vs. 2/{wom['personalized_n_judged']} without -- the "
            "different percentages come entirely from the judge's inconsistent null-vs-bool compliance across "
            "the two conditions shrinking one denominator, not from memory injection changing how many answers "
            "were actually personalized. Two of the eight items (csm05, csm08) were judged personalized in "
            "*both* conditions, meaning the memory-blind answer satisfied the fact anyway -- with only 8 items "
            "total, this ablation cannot support a directional claim about whether memory helps personalization "
            "here, only that the harness can measure it at all."
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=Path("bench/results/ablation.md"))
    args = parser.parse_args()

    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report)
    print(report)
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
