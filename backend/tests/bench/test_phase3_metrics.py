import pytest

from bench.phase3_metrics import first_hit_rank, mean, mrr_at_k, ndcg_at_k, recall_at_k


def test_first_hit_rank_returns_1_indexed_rank_of_first_match():
    assert first_hit_rank([("a", 1), ("b", 2), ("c", 3)], {("b", 2)}) == 2


def test_first_hit_rank_returns_none_when_no_match():
    assert first_hit_rank([("a", 1), ("b", 2)], {("z", 9)}) is None


def test_first_hit_rank_empty_ranked_list_is_a_miss():
    assert first_hit_rank([], {("a", 1)}) is None


def test_recall_at_k_counts_hits_over_total():
    assert recall_at_k([1, None, 3, None]) == 0.5


def test_recall_at_k_empty_is_zero():
    assert recall_at_k([]) == 0.0


@pytest.mark.parametrize(
    "hit_ranks, expected",
    [
        ([1], 1.0),
        ([2], 0.5),
        ([None], 0.0),
        ([1, 2, None], (1.0 + 0.5 + 0.0) / 3),
    ],
)
def test_mrr_at_k(hit_ranks, expected):
    assert mrr_at_k(hit_ranks) == pytest.approx(expected)


def test_ndcg_perfect_ranking_is_1():
    ranked = [("a", 1), ("b", 2), ("c", 3)]
    expected = {("a", 1)}
    assert ndcg_at_k(ranked, expected, k=10) == pytest.approx(1.0)


def test_ndcg_relevant_item_lower_in_ranking_scores_less_than_1():
    ranked = [("x", 9), ("a", 1)]
    expected = {("a", 1)}
    score = ndcg_at_k(ranked, expected, k=10)
    assert 0.0 < score < 1.0


def test_ndcg_no_relevant_items_present_is_zero():
    ranked = [("x", 9), ("y", 8)]
    expected = {("a", 1)}
    assert ndcg_at_k(ranked, expected, k=10) == 0.0


def test_ndcg_empty_expected_set_is_zero():
    assert ndcg_at_k([("a", 1)], set(), k=10) == 0.0


def test_ndcg_idcg_capped_at_k_not_at_len_expected():
    # Two relevant pages exist but k=1 -- best achievable with only one slot
    # is to fill it with either relevant page, so a ranking that does so
    # should score 1.0, not be penalized for "missing" the second one.
    ranked = [("a", 1), ("b", 2)]
    expected = {("a", 1), ("b", 2)}
    assert ndcg_at_k(ranked, expected, k=1) == pytest.approx(1.0)


def test_ndcg_beyond_k_cutoff_is_ignored():
    ranked = [("x", 9), ("a", 1)]
    expected = {("a", 1)}
    assert ndcg_at_k(ranked, expected, k=1) == 0.0


def test_mean_of_values():
    assert mean([1.0, 2.0, 3.0]) == 2.0


def test_mean_empty_is_zero():
    assert mean([]) == 0.0
