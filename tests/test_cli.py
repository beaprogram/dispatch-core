"""Tests for the command line interface."""

from pathlib import Path

import pytest

from dispatch_core.__main__ import build_parser, main

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"

BASE_ARGS = [
    "simulate",
    "--graph",
    str(FIXTURE_PATH),
    "--couriers",
    "8",
    "--orders",
    "25",
    "--seed",
    "42",
]


@pytest.mark.parametrize("strategy", ["nearest", "topk-eta"])
def test_simulate_runs_and_reports(capsys: pytest.CaptureFixture[str], strategy: str) -> None:
    """The simulate command runs and prints every headline metric."""
    assert main([*BASE_ARGS, "--strategy", strategy]) == 0

    output = capsys.readouterr().out
    assert strategy in output
    for label in (
        "mean wait",
        "p95 wait",
        "mean delivery",
        "p95 delivery",
        "on time",
        "p50 decision latency",
        "p95 decision latency",
        "route cache hit rate",
    ):
        assert label in output, f"missing metric: {label}"


# Wall clock measurements cannot repeat across runs; simulated time metrics must.
MEASURED_IN_WALL_CLOCK = ("wall clock", "decision latency")


def test_cli_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    """The same arguments reproduce every simulated time metric exactly.

    Decision latency and total wall clock are excluded on purpose: they time real
    execution, so they vary run to run by design. Everything else is a function of
    the seed alone and must match exactly.
    """

    def report() -> list[str]:
        main(BASE_ARGS)
        lines = capsys.readouterr().out.splitlines()
        return [line for line in lines if not any(m in line for m in MEASURED_IN_WALL_CLOCK)]

    first, second = report(), report()
    assert first == second
    assert any("mean delivery" in line for line in first), "report was unexpectedly empty"


def test_wall_clock_metrics_are_excluded_from_the_determinism_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guards the exclusion list above from silently dropping every line."""
    main(BASE_ARGS)
    lines = capsys.readouterr().out.splitlines()
    excluded = [line for line in lines if any(m in line for m in MEASURED_IN_WALL_CLOCK)]
    assert len(excluded) == 3, f"expected 3 wall clock lines, found {len(excluded)}"


def test_defaults_match_the_documented_interface() -> None:
    """The documented defaults are what the parser actually uses."""
    args = build_parser().parse_args(["simulate", "--graph", "g.graphml"])
    assert args.couriers == 200
    assert args.orders == 1000
    assert args.seed == 42
    assert args.strategy == "nearest"
    assert args.k == 5


def test_unknown_strategy_is_rejected() -> None:
    """argparse rejects a strategy outside the supported set."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["simulate", "--graph", "g.graphml", "--strategy", "random"])


def test_command_is_required() -> None:
    """Running with no subcommand exits rather than doing something surprising."""
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
