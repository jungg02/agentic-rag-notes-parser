"""One command for the whole Phase 3 evaluation suite
(RETRIEVAL_UPGRADE_PLAN.md acceptance criterion: "One command runs the
whole suite").

Runs, in order:
  1. phase3_retrieval_ablation.py -- zero-LLM lexical/vector/fused/reranked
     ablation over all items.
  2. phase3_llm_collect.py -- resumable, cached collection of every live-LLM
     result needed downstream (rewrites, generated answers, judge
     verdicts). Safe to re-run; already-succeeded work is skipped.
  3. phase3_rewriting_ablation.py -- the query-rewriting headline number,
     computed from step 2's cached rewrites plus fresh (zero-LLM) retrieval.
  4. phase3_report.py -- assembles bench/results/ablation.md from
     everything above plus phase3_hand_labels.py's calibration labels.

Step 2 is the one that can legitimately take a while against a flaky
provider (see its own docstring). Re-running this whole command is cheap
after the first pass -- steps 1, 3, and 4 are fast and step 2 skips
whatever already succeeded.

Usage (run inside the backend container so the DB and models are
reachable):

    docker compose run --rm backend python bench/phase3_eval.py
    docker compose run --rm backend python bench/phase3_eval.py --skip-collect  # reuse existing cache
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(args: list[str]) -> None:
    print(f"\n{'=' * 70}\n$ {' '.join(args)}\n{'=' * 70}")
    result = subprocess.run(args, cwd=Path(__file__).resolve().parent.parent)
    if result.returncode != 0:
        raise SystemExit(f"Step failed (exit {result.returncode}): {' '.join(args)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-collect", action="store_true", help="Reuse the existing LLM cache as-is, don't re-run collection.")
    parser.add_argument("--timeout", type=float, default=75.0, help="Passed through to phase3_llm_collect.py.")
    parser.add_argument("--workers", type=int, default=5, help="Passed through to phase3_llm_collect.py.")
    args = parser.parse_args()

    python = sys.executable
    _run([python, "bench/phase3_retrieval_ablation.py"])
    if not args.skip_collect:
        _run([python, "bench/phase3_llm_collect.py", "--timeout", str(args.timeout), "--workers", str(args.workers)])
    _run([python, "bench/phase3_rewriting_ablation.py"])
    _run([python, "bench/phase3_report.py"])

    print("\nDone. See bench/results/ablation.md")


if __name__ == "__main__":
    main()
