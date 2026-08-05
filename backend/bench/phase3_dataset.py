"""Loads the Phase 3 evaluation set (scripts/eval/phase3_queries.json) into
typed EvalItem objects. See that file's own `_note` field for how it was
built and why the grounding is exact by construction.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EVAL_FILE = Path(__file__).resolve().parent.parent / "scripts" / "eval" / "phase3_queries.json"

REWRITE_CATEGORIES = frozenset({"multi_turn_coreference", "topic_switch"})
MEMORY_CATEGORY = "cross_session_memory"


@dataclass(frozen=True)
class SeedMemory:
    memory_type: str
    content: str


@dataclass(frozen=True)
class EvalItem:
    id: str
    category: str
    turns: list[str]
    expected: frozenset[tuple[str, int]]
    memory: SeedMemory | None = None

    @property
    def query(self) -> str:
        """The raw, un-rewritten text of the turn being scored."""
        return self.turns[-1]

    @property
    def history(self) -> list[str]:
        """Prior turns, oldest first -- empty for single-turn items."""
        return self.turns[:-1]


def load_items(path: Path = DEFAULT_EVAL_FILE) -> tuple[int, list[EvalItem]]:
    raw = json.loads(path.read_text())
    course_id = raw["course_id"]

    items = []
    for entry in raw["items"]:
        memory = SeedMemory(**entry["memory"]) if "memory" in entry else None
        expected = frozenset((e["document"], e["page"]) for e in entry["expected"])
        items.append(
            EvalItem(
                id=entry["id"],
                category=entry["category"],
                turns=list(entry["turns"]),
                expected=expected,
                memory=memory,
            )
        )
    return course_id, items
