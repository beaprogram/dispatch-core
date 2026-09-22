"""Generate benchmarks/results/RESULTS.md from the benchmark CSVs.

Every number in RESULTS.md is read from a CSV produced by a benchmark run. Nothing
here computes a result of its own, and the file is regenerated rather than edited, so
a number in the report always traces back to a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.common import RESULTS_DIR, read_csv, spread

BASELINE_COURIERS = 200


@dataclass(frozen=True, slots=True)
class Metric:
    """A dispatch metric and which direction counts as better."""

    column: str
    label: str
    unit: str
    lower_is_better: bool


SWEEP_METRICS = (
    Metric("mean_wait_s", "mean wait", "s", True),
    Metric("p95_wait_s", "p95 wait", "s", True),
    Metric("mean_delivery_s", "mean delivery", "s", True),
    Metric("p95_delivery_s", "p95 delivery", "s", True),
    Metric("on_time_pct", "on time", "%", False),
)


def environment_block(environment: dict[str, str]) -> list[str]:
    """Render the machine and date a benchmark ran on."""
    return [
        "| field | value |",
        "| --- | --- |",
        *(f"| {key} | {value} |" for key, value in environment.items()),
    ]


def nearest_section() -> list[str]:
    """Quadtree against linear scan."""
    rows, environment = read_csv("bench_nearest.csv")
    lines = [
        "## Nearest neighbour: quadtree against linear scan",
        "",
        *environment_block(environment),
        "",
        f"{rows[0]['queries']} queries per measurement, k = {rows[0]['k']} for k nearest.",
        "Median of 5 repeats after 1 warmup.",
        "",
        "| distribution | n | build median | nearest: quadtree | nearest: scan |"
        " k nearest: quadtree | k nearest: scan | mean points checked |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['distribution']} | {int(row['n']):,} | {float(row['build_median_ms']):.1f} ms "
            f"| {float(row['quadtree_nearest_median_ms']):.2f} ms "
            f"| {float(row['brute_nearest_median_ms']):.2f} ms "
            f"| {float(row['quadtree_knearest_median_ms']):.2f} ms "
            f"| {float(row['brute_knearest_median_ms']):.2f} ms "
            f"| {float(row['mean_points_checked']):.1f} |"
        )
    lines.append("")
    lines.append(
        "Mean points checked counts distance computations per nearest query. It measures"
        " pruning, not runtime: node visits and heap operations are excluded."
    )
    lines.append("")
    return lines


def routing_section() -> list[str]:
    """A* against Dijkstra against networkx."""
    rows, environment = read_csv("bench_routing.csv")
    lines = [
        "## Routing: A* against Dijkstra against networkx",
        "",
        *environment_block(environment),
        "",
        f"{rows[0]['pairs']} seeded source and target pairs on {rows[0]['graph']}.",
        "Median of 5 repeats after 1 warmup. networkx is an independent reference,",
        "not a competitor: the comparison that matters is cost agreement and nodes expanded.",
        "",
        "| weight | algorithm | median total | median per pair | mean nodes expanded |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        expanded = row["mean_nodes_expanded"] or "n/a"
        lines.append(
            f"| {row['weight']} | {row['algorithm']} | {float(row['median_ms']):.1f} ms "
            f"| {float(row['median_ms_per_pair']):.3f} ms | {expanded} |"
        )
    lines.append("")
    return lines


def baseline_section(rows: list[dict[str, str]]) -> list[str]:
    """The documented default configuration."""
    baseline = [r for r in rows if int(r["couriers"]) == BASELINE_COURIERS]
    seeds = sorted({int(r["seed"]) for r in baseline})
    inter_arrival = float(baseline[0]["mean_interarrival_s"])

    lines = [
        "## Dispatch baseline: the documented defaults",
        "",
        f"{baseline[0]['orders']} orders, {BASELINE_COURIERS} couriers, "
        f"{baseline[0]['arrival_rate_per_minute']} orders per minute, "
        f"graph {baseline[0]['graph']}.",
        f"Seeds {seeds}. Each cell is the mean across seeds, with the min and max observed.",
        "",
        "| metric | nearest | topk-eta | difference exceeds seed spread |",
        "| --- | --- | --- | --- |",
    ]

    for metric in SWEEP_METRICS:
        cells = {}
        for strategy in ("nearest", "topk-eta"):
            values = [float(r[metric.column]) for r in baseline if r["strategy"] == strategy]
            cells[strategy] = spread(values)

        verdict = comparison_verdict(cells["nearest"], cells["topk-eta"], metric)
        lines.append(
            f"| {metric.label} ({metric.unit}) "
            f"| {format_spread(cells['nearest'])} "
            f"| {format_spread(cells['topk-eta'])} "
            f"| {verdict} |"
        )

    lines.extend(
        [
            "",
            "### Decision latency, absolute",
            "",
            f"Mean order inter-arrival at this configuration is **{inter_arrival:.2f} s**.",
            "Latency is the wall clock time to choose a courier for one order.",
            "",
            "| strategy | p50 | p95 | mean inter-arrival |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for strategy in ("nearest", "topk-eta"):
        subset = [r for r in baseline if r["strategy"] == strategy]
        p50 = spread(float(r["p50_decision_latency_ms"]) for r in subset)[0]
        p95 = spread(float(r["p95_decision_latency_ms"]) for r in subset)[0]
        lines.append(
            f"| {strategy} | {p50:.4f} ms | {p95:.4f} ms | {inter_arrival * 1000:.0f} ms |"
        )

    lines.extend(
        [
            "",
            "### Route cache",
            "",
            "| strategy | hit rate | hits | misses |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for strategy in ("nearest", "topk-eta"):
        subset = [r for r in baseline if r["strategy"] == strategy]
        rate = spread(float(r["cache_hit_rate"]) for r in subset)[0]
        hits = spread(float(r["cache_hits"]) for r in subset)[0]
        misses = spread(float(r["cache_misses"]) for r in subset)[0]
        lines.append(f"| {strategy} | {rate * 100:.2f} % | {hits:.0f} | {misses:.0f} |")

    lines.append("")
    return lines


def format_spread(values: tuple[float, float, float]) -> str:
    """Render mean with the min and max observed across seeds."""
    mean, low, high = values
    return f"{mean:.1f} (min {low:.1f}, max {high:.1f})"


def paired_comparison(
    rows: list[dict[str, str]], couriers: int, metric: Metric
) -> tuple[int, int, float]:
    """Compare the two strategies seed by seed.

    Both strategies see identical inputs for a given seed, so pairing on seed removes
    seed variance entirely. This is more sensitive than comparing ranges, which can
    hide a small but consistent effect behind the spread between seeds.

    Returns (wins for topk-eta, seeds compared, mean paired difference) where a
    positive difference favours topk-eta.
    """
    seeds = sorted({int(r["seed"]) for r in rows})
    differences = []
    for seed in seeds:
        matching = {
            r["strategy"]: float(r[metric.column])
            for r in rows
            if int(r["couriers"]) == couriers and int(r["seed"]) == seed
        }
        if "nearest" not in matching or "topk-eta" not in matching:
            continue
        delta = matching["nearest"] - matching["topk-eta"]
        differences.append(delta if metric.lower_is_better else -delta)

    wins = sum(1 for d in differences if d > 0)
    mean_delta = sum(differences) / len(differences) if differences else 0.0
    return wins, len(differences), mean_delta


def format_paired(wins: int, total: int, mean_delta: float, metric: Metric) -> str:
    """Render the paired result as wins and mean difference."""
    if mean_delta == 0.0 and wins == 0:
        return f"tied on all {total}"
    direction = "topk-eta" if mean_delta > 0 else "nearest"
    return f"{direction} on {max(wins, total - wins)}/{total}, {abs(mean_delta):.2f} {metric.unit}"


def comparison_verdict(
    nearest: tuple[float, float, float], topk: tuple[float, float, float], metric: Metric
) -> str:
    """State whether the strategy difference is larger than seed to seed noise.

    The test is whether the two per seed ranges overlap. If they do, seed variation
    alone could account for the gap, so it is not reported as an improvement.
    """
    nearest_mean, nearest_low, nearest_high = nearest
    topk_mean, topk_low, topk_high = topk

    overlap = nearest_low <= topk_high and topk_low <= nearest_high
    if overlap:
        return "no, ranges overlap"

    topk_better = topk_mean < nearest_mean if metric.lower_is_better else topk_mean > nearest_mean
    winner = "topk-eta" if topk_better else "nearest"
    return f"yes, {winner} better"


def sweep_section(rows: list[dict[str, str]]) -> list[str]:
    """Courier load sweep."""
    courier_counts = sorted({int(r["couriers"]) for r in rows})
    lines = [
        "## Dispatch load sweep",
        "",
        "Courier count varied at the default arrival rate. Both strategies see identical",
        "generated inputs for a given seed, so any difference comes from the assignment",
        "decision alone.",
        "",
        "Two tests are reported. The range test asks whether the strategies' per seed",
        "ranges are disjoint, which is conservative. The paired test compares the two",
        "strategies seed by seed, which removes seed variance entirely and can detect a",
        "small but consistent effect the range test hides.",
        "",
    ]

    for metric in SWEEP_METRICS:
        lines.extend(
            [
                f"### {metric.label} ({metric.unit})",
                "",
                "| couriers | nearest | topk-eta | exceeds seed spread | paired by seed |",
                "| ---: | --- | --- | --- | --- |",
            ]
        )
        for couriers in courier_counts:
            cells = {}
            for strategy in ("nearest", "topk-eta"):
                values = [
                    float(r[metric.column])
                    for r in rows
                    if int(r["couriers"]) == couriers and r["strategy"] == strategy
                ]
                cells[strategy] = spread(values)
            verdict = comparison_verdict(cells["nearest"], cells["topk-eta"], metric)
            wins, total, delta = paired_comparison(rows, couriers, metric)
            lines.append(
                f"| {couriers} | {format_spread(cells['nearest'])} "
                f"| {format_spread(cells['topk-eta'])} | {verdict} "
                f"| {format_paired(wins, total, delta, metric)} |"
            )
        lines.append("")

    return lines


def disagreement_section(rows: list[dict[str, str]]) -> list[str]:
    """How often topk-eta overrides straight line nearest, and what it buys."""
    courier_counts = sorted({int(r["couriers"]) for r in rows})
    lines = [
        "## Where topk-eta actually differs",
        "",
        "A disagreement is a decision where topk-eta rejected the straight line nearest",
        "courier. Mean ETA saved is averaged over disagreements only, because averaging",
        "over every decision would dilute it with the majority where both strategies pick",
        "the same courier and the saving is exactly zero.",
        "",
        "Seconds per meter is road travel time divided by straight line distance. A higher",
        "value for the rejected courier means the straight line understated its real route.",
        "",
        "| couriers | disagreement rate | mean ETA saved | rejected s/m | chosen s/m |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]

    for couriers in courier_counts:
        subset = [r for r in rows if int(r["couriers"]) == couriers and r["strategy"] == "topk-eta"]
        rate = spread(float(r["disagreement_rate"]) for r in subset)
        saved = spread(float(r["mean_eta_saved_s"]) for r in subset)
        rejected = spread(float(r["rejected_s_per_m"]) for r in subset)[0]
        chosen = spread(float(r["chosen_s_per_m"]) for r in subset)[0]
        lines.append(
            f"| {couriers} | {rate[0] * 100:.2f} % (min {rate[1] * 100:.2f}, max {rate[2] * 100:.2f}) "
            f"| {saved[0]:.1f} s (min {saved[1]:.1f}, max {saved[2]:.1f}) "
            f"| {rejected:.4f} | {chosen:.4f} |"
        )

    lines.append("")
    return lines


def main() -> None:
    """Regenerate RESULTS.md from the CSVs on disk."""
    dispatch_rows, dispatch_environment = read_csv("bench_dispatch.csv")
    seeds = sorted({int(r["seed"]) for r in dispatch_rows})

    lines = [
        "# Benchmark results",
        "",
        "Generated by `python -m benchmarks.make_results` from the CSVs in this directory.",
        "Do not edit by hand: every number here is read from a measured run.",
        "",
        *nearest_section(),
        *routing_section(),
        "## Dispatch",
        "",
        *environment_block(dispatch_environment),
        "",
        f"Seeds used: {seeds} ({len(seeds)} seeds per cell).",
        "",
        *baseline_section(dispatch_rows),
        *sweep_section(dispatch_rows),
        *disagreement_section(dispatch_rows),
    ]

    path = RESULTS_DIR / "RESULTS.md"
    path.write_text("\n".join(lines) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
