from collections import defaultdict


def reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked):
            scores[chunk_id] += 1.0 / (k + rank + 1)
    return [chunk_id for chunk_id, _ in sorted(scores.items(), key=lambda kv: -kv[1])]
