from dataclasses import dataclass
from typing import Literal

MemoryType = Literal["topic", "struggle", "preference", "goal"]

VALID_MEMORY_TYPES: frozenset[str] = frozenset({"topic", "struggle", "preference", "goal"})


@dataclass
class ExtractedMemory:
    content: str
    memory_type: MemoryType
    confidence: float
