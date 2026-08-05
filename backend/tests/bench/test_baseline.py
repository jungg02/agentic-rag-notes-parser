import pytest

from bench.baseline import _human_bytes, percentile


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 kB"),
        (5_980_160, "5.7 MB"),
        (2**30, "1.0 GB"),
        (2**40, "1.0 TB"),
    ],
)
def test_human_bytes(n, expected):
    assert _human_bytes(n) == expected


def test_percentile_single_value_returns_that_value():
    assert percentile([42.0], 95) == 42.0


def test_percentile_p50_is_median_for_odd_count():
    assert percentile([1.0, 2.0, 3.0], 50) == 2.0


def test_percentile_p99_is_close_to_max_for_uniform_samples():
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 99) >= 98.0
