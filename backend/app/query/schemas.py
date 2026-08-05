from dataclasses import dataclass
from typing import Literal

Intent = Literal["factual_lookup", "comparison", "summarization", "follow_up"]

VALID_INTENTS: frozenset[str] = frozenset({"factual_lookup", "comparison", "summarization", "follow_up"})


@dataclass
class QueryUnderstanding:
    original_query: str
    intent: Intent
    # None when the query was judged standalone -- retrieval runs on
    # original_query in that case. Never discard original_query itself.
    rewritten_query: str | None

    @property
    def retrieval_query(self) -> str:
        return self.rewritten_query or self.original_query
