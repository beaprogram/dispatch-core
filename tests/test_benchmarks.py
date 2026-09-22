"""Tests for the benchmark helpers.

The benchmark scripts themselves are too slow for CI, but the helpers that decide
what gets reported are not, and a bug in those would silently corrupt every
published number.
"""

import pytest

from benchmarks.common import Environment, Timing, spread, time_repeats
from benchmarks.make_results import Metric, comparison_verdict, format_paired, paired_comparison

LOWER_BETTER = Metric("mean_wait_s", "mean wait", "s", lower_is_better=True)
HIGHER_BETTER = Metric("on_time_pct", "on time", "%", lower_is_better=False)


def rows_for(nearest: list[float], topk: list[float], couriers: int = 200) -> list[dict[str, str]]:
    """Build dispatch rows with one seed per supplied value."""
    built = []
    for seed, value in enumerate(nearest):
        built.append(
            {
                "couriers": str(couriers),
                "seed": str(seed),
                "strategy": "nearest",
                "mean_wait_s": str(value),
                "on_time_pct": str(value),
            }
        )
    for seed, value in enumerate(topk):
        built.append(
            {
                "couriers": str(couriers),
                "seed": str(seed),
                "strategy": "topk-eta",
                "mean_wait_s": str(value),
                "on_time_pct": str(value),
            }
        )
    return built


def test_environment_records_every_field() -> None:
    """Every results file must carry the machine and date it came from."""
    environment = Environment.capture()
    keys = {row["key"] for row in environment.as_rows()}
    assert keys == {"cpu", "machine", "os", "python", "run_date"}
    for row in environment.as_rows():
        assert row["value"], f"{row['key']} is empty"


def test_timing_reports_median_and_p95() -> None:
    """Timing summarises its samples without inventing values."""
    timing = Timing(samples_ms=(1.0, 2.0, 3.0, 4.0, 5.0))
    assert timing.median_ms == 3.0
    assert timing.min_ms == 1.0
    assert timing.p95_ms in timing.samples_ms


def test_time_repeats_runs_the_expected_number_of_times() -> None:
    """The warmup is not recorded, the repeats are."""
    calls = []
    timing = time_repeats(lambda: calls.append(1), repeats=5, warmups=1)
    assert len(calls) == 6, "expected 1 warmup plus 5 measured repeats"
    assert len(timing.samples_ms) == 5


def test_spread_returns_mean_min_max() -> None:
    """Spread is what the report uses to show seed variation."""
    assert spread([1.0, 2.0, 6.0]) == (3.0, 1.0, 6.0)
    assert spread([]) == (0.0, 0.0, 0.0)


def test_overlapping_ranges_are_not_called_an_improvement() -> None:
    """A gap smaller than seed variation must not be reported as a win."""
    nearest = spread([100.0, 110.0, 120.0])
    topk = spread([95.0, 105.0, 115.0])
    assert comparison_verdict(nearest, topk, LOWER_BETTER) == "no, ranges overlap"


def test_disjoint_ranges_name_the_better_strategy() -> None:
    """A gap larger than seed variation is reported, with the direction."""
    nearest = spread([100.0, 101.0, 102.0])
    topk = spread([50.0, 51.0, 52.0])
    assert comparison_verdict(nearest, topk, LOWER_BETTER) == "yes, topk-eta better"
    assert comparison_verdict(topk, nearest, LOWER_BETTER) == "yes, nearest better"


def test_verdict_respects_metric_direction() -> None:
    """For on time percentage, higher is better, so the direction flips."""
    low = spread([10.0, 11.0])
    high = spread([90.0, 91.0])
    assert comparison_verdict(low, high, HIGHER_BETTER) == "yes, topk-eta better"
    assert comparison_verdict(high, low, HIGHER_BETTER) == "yes, nearest better"


def test_paired_comparison_counts_wins_per_seed() -> None:
    """Pairing on seed removes seed variance, so a consistent small effect shows."""
    rows = rows_for(nearest=[100.0, 200.0, 300.0], topk=[99.0, 199.0, 299.0])
    wins, total, delta = paired_comparison(rows, 200, LOWER_BETTER)
    assert (wins, total) == (3, 3)
    assert delta == pytest.approx(1.0)


def test_paired_comparison_detects_a_reversal() -> None:
    """When the alternative is worse, the paired result must say so."""
    rows = rows_for(nearest=[100.0, 100.0, 100.0], topk=[110.0, 110.0, 90.0])
    wins, total, delta = paired_comparison(rows, 200, LOWER_BETTER)
    assert wins == 1
    assert total == 3
    assert delta < 0


def test_paired_comparison_respects_metric_direction() -> None:
    """For a higher-is-better metric a larger topk value is a win."""
    rows = rows_for(nearest=[90.0, 90.0], topk=[95.0, 95.0])
    wins, _, delta = paired_comparison(rows, 200, HIGHER_BETTER)
    assert wins == 2
    assert delta == pytest.approx(5.0)


def test_paired_comparison_ignores_other_courier_counts() -> None:
    """Rows from a different sweep cell must not leak into a comparison."""
    rows = rows_for(nearest=[100.0], topk=[50.0], couriers=200)
    rows += rows_for(nearest=[999.0], topk=[1.0], couriers=400)
    _, total, delta = paired_comparison(rows, 200, LOWER_BETTER)
    assert total == 1
    assert delta == pytest.approx(50.0)


def test_format_paired_names_the_winner_and_margin() -> None:
    """The rendered string states direction and size, not a bare verdict."""
    assert format_paired(4, 5, 20.3, LOWER_BETTER) == "topk-eta on 4/5, 20.30 s"
    assert format_paired(1, 5, -14.31, LOWER_BETTER) == "nearest on 4/5, 14.31 s"
    assert format_paired(0, 5, 0.0, LOWER_BETTER) == "tied on all 5"


def test_projection_measurement_matches_the_documented_bound() -> None:
    """The projection error claim in the README is reproducible."""
    from benchmarks.bench_analysis import measure_projection_error

    rows = measure_projection_error()
    assert len(rows) == 1
    assert rows[0]["measurement"] == "projection_worst_relative_error_pct"
    assert 0.0 < float(rows[0]["value"]) < 0.02, (
        "projection error drifted from the documented bound"
    )


def test_ring_measurement_shows_pruning_failing() -> None:
    """The ring worst case claim in the README is reproducible."""
    from benchmarks.bench_analysis import RING_SIZE, measure_ring_pathology

    rows = {row["measurement"]: row["value"] for row in measure_ring_pathology()}
    assert rows["ring_size"] == RING_SIZE
    assert rows["ring_points_checked"] > 0.99 * RING_SIZE, (
        "the ring no longer defeats pruning, so the documented worst case is stale"
    )


def test_analysis_rows_have_a_consistent_shape() -> None:
    """Every analysis row must carry a name, a value, and an explanation."""
    from benchmarks.bench_analysis import measure_projection_error, measure_ring_pathology

    for row in [*measure_projection_error(), *measure_ring_pathology()]:
        assert set(row) == {"measurement", "value", "detail"}
        assert row["detail"], f"{row['measurement']} has no detail"
