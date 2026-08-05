"""Hand-labeled verdicts for the Phase 3 judge-calibration subset (~20
answers: the 8 cross_session_memory items' with_memory answers, plus 6
single_turn_factual + 6 comparison calibration-only answers -- see
phase3_llm_collect.py's CALIBRATION_EXTRA_IDS).

Populated by reading each (question, generated answer, source excerpts)
triple in bench/results/phase3_llm_cache.jsonl directly and judging it the
same way the LLM judge was asked to: faithful (every claim actually
supported by the given excerpts) and citations_correct (every [n] marker
points to an excerpt that supports the claim it's attached to).

Key: (item_id, condition) -> {"faithful": bool, "citations_correct": bool}
"""

HAND_LABELS: dict[tuple[str, str], dict[str, bool]] = {
    ("csm01", "with_memory"): {"faithful": True, "citations_correct": True},
    ("csm02", "with_memory"): {"faithful": True, "citations_correct": True},
    ("csm03", "with_memory"): {"faithful": True, "citations_correct": True},
    ("csm04", "with_memory"): {"faithful": True, "citations_correct": True},
    # Disagrees with the judge on both fields: the answer's final sentence
    # ("In the base R, the corresponding function would be...") attaches
    # citation [5] to a base-R syntax claim that excerpt [5] never makes --
    # [5] shows dplyr's mutate() code, not base R `$<-` assignment. Correct
    # R knowledge, but not sourced from the given excerpts, and the citation
    # doesn't actually support the claim it's attached to.
    ("csm05", "with_memory"): {"faithful": False, "citations_correct": False},
    ("csm06", "with_memory"): {"faithful": True, "citations_correct": True},
    ("csm07", "with_memory"): {"faithful": True, "citations_correct": True},
    ("csm08", "with_memory"): {"faithful": True, "citations_correct": True},
    ("stf01", "default"): {"faithful": True, "citations_correct": True},
    ("stf02", "default"): {"faithful": True, "citations_correct": True},
    ("stf03", "default"): {"faithful": True, "citations_correct": True},
    ("stf04", "default"): {"faithful": True, "citations_correct": True},
    ("stf05", "default"): {"faithful": True, "citations_correct": True},
    ("stf06", "default"): {"faithful": True, "citations_correct": True},
    ("cmp01", "default"): {"faithful": True, "citations_correct": True},
    ("cmp02", "default"): {"faithful": True, "citations_correct": True},
    # Disagrees with the judge on faithful only: the opening sentence
    # ("The apply() function returns a vector or a matrix depending on...")
    # carries no citation marker at all and isn't stated anywhere in the
    # given excerpts (they show apply()'s syntax/usage, never its return
    # type) -- true of real R, but not grounded in what was provided. The
    # citations that *are* present later in the answer are correctly
    # attached to claims the excerpts do support.
    ("cmp03", "default"): {"faithful": False, "citations_correct": True},
    ("cmp04", "default"): {"faithful": True, "citations_correct": True},
    ("cmp05", "default"): {"faithful": True, "citations_correct": True},
    ("cmp06", "default"): {"faithful": True, "citations_correct": True},
}
